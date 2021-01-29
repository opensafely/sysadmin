import argparse
import itertools
import os
import select
import sys
import yaml

from github import Github, GithubException

import client


# This applies to all repos. Values are from
# https://docs.github.com/en/rest/reference/repos#update-a-repository
REPO_POLICY = {
    "delete_branch_on_merge": True
}

# This applies to study repo master/main branchs. See convert_protection
# function for values.
STUDY_BRANCH_POLICY = {
    "enforce_admins": True,
}

# This applies to code repo master/main branchs. See convert_protection
# function for values.
CODE_BRANCH_POLICY = {
    "enforce_admins": True,
    "required_approving_review_count": 1,
}


def convert_protection(protection):
    """Convert protection read format to the write format.

    Converts results of branch.get_protection() into a dict that can passed to
    branch.edit_protection(). That this is necessary is a sad thing.

    Input: https://pygithub.readthedocs.io/en/latest/github_objects/BranchProtection.html

    Output: keyword args as per:

    https://pygithub.readthedocs.io/en/latest/github_objects/Branch.html#github.Branch.Branch.edit_protection
    """
    reviews = protection.required_pull_request_reviews
    output = dict(
        enforce_admins=protection.enforce_admins,
        dismissal_users=getattr(reviews, 'dismissal_users', None),
        dismissal_teams=getattr(reviews, 'dismissal_teams', None),
        dismiss_stale_reviews=getattr(reviews, 'dismiss_stale_reviews', None),
        require_code_owner_reviews=getattr(reviews, 'require_code_owner_reviews', None),
        required_approving_review_count=getattr(reviews, 'required_approving_review_count', None),
        strict=getattr(protection.required_status_checks, 'strict', None),
        contexts=getattr(protection.required_status_checks, 'contexts', None),
        # TODO: user/team push restrictions if we need them
    )
 
    return output


def protect_branch(repo, branch=None, **kwargs):
    """Audit and enforce branch protections.
    
    Keyword args can be used to set additional restrictions, as per:

    https://pygithub.readthedocs.io/en/latest/github_objects/Branch.html#github.Branch.Branch.edit_protection
    
    We set enforce_admins=True by default

    """
    # our security model requires enforce_admins
    kwargs['enforce_admins'] = True
    protection = {}
    protected_branches = []

    # cope with master -> main name transition, including possibility that both
    # exist
    if branch is None:
        branches = ['master', 'main']
    else:
        branches = [branch]

    for branch_name in branches:
        try: 
            b = repo.get_branch(branch_name)
            protected_branches.append(b)
        except GithubException as e:
            if e.status != 404:
                raise

    if not protected_branches:
        yield client.Change(
            lambda: None,
            "ERROR: Could not find {} branches in {}",
            branches,
            repo.full_name,
        )

    for protected_branch in protected_branches:
        try:
            current_protection = convert_protection(protected_branch.get_protection())
        except GithubException as e:
            if e.status == 404:
                # new repo no protection set
                protection = kwargs
            else:
                # this occurs when a private repo is forked *into* the opensafely org
                # currently just vaccine-eligibility repo, we want to avoid that in future.
                yield client.Change(
                    lambda: None,
                    'ERROR: exception getting branch protection on {}/{}\n{}',
                    repo.full_name,
                    protected_branch.name,
                    e,
                )
                continue
        else:
            for k, v in kwargs.items():
                if current_protection[k] != v:
                    protection[k] = v

        if protection: 
            yield client.Change(
                lambda: protected_branch.edit_protection(**protection),
                'setting branch protection on {}/{} to:\n{}',
                repo.name,
                protected_branch.name,
                ', '.join('{}={}'.format(k, v) for k, v in protection.items()),
            )


def configure_repo(repo, **kwargs):
    """Configure a repo according to config."""
    if repo.archived:
        return
    to_change = {}
    for name, value  in kwargs.items():
        if getattr(repo, name) != value:
            to_change[name] = value

    if to_change:
        yield client.Change(
            lambda: repo.edit(**to_change),
            'setting repo policy:\n{}',
            to_change,
        )


def input_with_timeout(prompt, timeout=5.0):
    print(prompt)
    i, _, _ = select.select([sys.stdin], [], [], 5)
    if i:
        return sys.stdin.readline().strip().lower()
    else:
        return None


def manage_code(org, repo_policy=None, branch_policy=None):
    """Ensure that all opensafe-core repos have the correct configuration."""
    code = client.GithubTeam(org)
    for repo in code.repos.values():
        print(repo.full_name)
        if repo_policy:
            yield from configure_repo(repo, **repo_policy)
        if branch_policy:
            yield from protect_branch(repo, **branch_policy)


def manage_studies(org, repo_policy, branch_policy, config):
    """Ensure all opensafely repos have the correct config.

    This also involves adding non_study repos to the editors team, and all
    others to the researchers team.
    """
    opensafely = client.GithubTeam(org)
    researchers = client.GithubTeam(org.get_team_by_slug('researchers'))
    editors = client.GithubTeam(org.get_team_by_slug('editors'))

    # everyone is in researchers group
    for member in opensafely.members.values():
        # avoid elevating bot accounts
        if member.login not in config['bots']:
            yield from researchers.add_member(member)

    for repo in opensafely.repos.values():
        print(repo.full_name)
        yield from configure_repo(repo, **repo_policy)
        yield from protect_branch(repo, **branch_policy)

        if repo.full_name in config["not_studies"]:
            yield from editors.add_repo(repo, 'admin')
        else:
            # researchers have access to all studies
            yield from researchers.add_repo(repo, 'admin')
         

def main(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser(
        description='Apply policy to OpenSAFELY github org'
    )
    parser.add_argument('config', help='The team config')
    parser.add_argument('--exec', action='store_true',
                        dest='execute',
                        help='Automatically execute commands')
    parser.add_argument('--dry-run', action='store_true',
                        dest='dry_run',
                        help='Just print what would change and exit')

    args = parser.parse_args(argv)
    # we run in one of three modes:
    # --dry-run: analyse changes, but do not apply
    # --exec: analyse changes and apply immediately
    # default: analyse changes and ask for confirmation before applying them
    mode = 'default'
    if args.dry_run:
        mode = 'dry-run'
    elif args.execute:
        mode = 'execute'

    studies = client.get_org('opensafely')
    core = client.get_org('opensafely-core')
    config = yaml.safe_load(open(args.config))

    if mode == 'dry-run':
        print('*** DRY RUN - no changes will be made ***')

    pending_changes = []

    # analyse changes needed
    changes = itertools.chain(
        manage_studies(studies, REPO_POLICY, STUDY_BRANCH_POLICY, config),
        manage_code(core, REPO_POLICY, CODE_BRANCH_POLICY),
    )

    for change in changes:
        print(change)
        if mode == 'execute':
            change()
        else:
            pending_changes.append(change)

    if mode == 'dry-run':
        print('*** DRY RUN - no changes were made ***')
    elif mode == 'default': 
        if pending_changes:
            answer = input_with_timeout(
                "Do you want to apply the above changes (y/n)?", 
                30.0,
            )
            if answer == 'y':
                for change in pending_changes:
                    print(change)
                    change()
        else:
            print('No changes needed')
    

if __name__ == '__main__':
    main()
