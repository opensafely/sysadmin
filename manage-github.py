import argparse
import os
import select
import sys
import yaml

from github import Github, GithubException

import client

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
        raise RuntimeException("Could not find branch {}".format(branches))

    for protected_branch in protected_branches:
        try:
            current_protection = convert_protection(protected_branch.get_protection())
        except GithubException as e:
            if e.status == 404:
                protection = kwargs
            else:
                raise
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


def ensure_teams(config, org):
    """Ensure that all teams have the correct members and repositories.

    The current policy is: 
     - Developers team members and protected_repositories are explicitly
       defined in config.
     - Researchers team members are everyone except explicitly defined bots.
     - Developers have admin permission to protected_repositories.
     - Researchers have admin permission to all other repositories
     - All master (or main) branches are protected, with enforce_admins=True
     - protected_repositories master (or main) branches additionally require review.
     - Small explicit Managers team has admin access to all repos.

    The primary goal is to partition the repos into studies and infrastructure,
    and restrict researchers from being able to write to infrastructure repos.
    Additionally, we restrict force-push from the cli via enabling branch
    protection with enforce_admins=True, and additionally restrict
    infrastructure repos to require review, which prevents pushing directly to
    master (or main).

    Note: the reason researchers have admin permissions is to allow them to
    invite external collaborators. The branch protection provides some extra
    security that they cannot force push even though they are admins. All other
    admin operations have to go via the website, with 2FA, so are less likely
    to be usable in an attack.
    """

    opensafely = client.GithubTeam(org)
    researchers = client.GithubTeam(org.get_team_by_slug('researchers'))
    developers = client.GithubTeam(org.get_team_by_slug('developers'))
    managers = client.GithubTeam(org.get_team_by_slug('managers'))

    # everyone is a researcher
    print('Checking researchers membership...')
    for member in opensafely.members.values():
        # avoid elevating bot accounts
        if member.login not in config['bots']:
            yield from researchers.add_member(member)

    print('Checking developers membership...')
    for dev in config['developers']:
        if dev in opensafely.members:  # protect against deleted users
            yield from developers.add_member(opensafely.members[dev])

    print('Checking admins membership...')
    for manager in config['managers']:
        if manager in opensafely.members:  # protect against deleted users
            yield from managers.add_member(opensafely.members[manager])

    # TODO: remove developers/admins if they are no longer in the groups. In
    # the common case (someone leaving), then I think there is no need, as
    # their account will be removed from the organisation and thus any teams.
    # But we should probably check that

    print('Checking org repositories...')
    for repo in opensafely.repos.values():
        # admins have access to all repos
        yield from managers.add_repo(repo, 'admin')

        # either a protected developer repo or not
        if repo.full_name in config['protected_repositories']:
            # stricter branch protection for these repos 
            yield from protect_branch(
                repo,
                enforce_admins=True,
                required_approving_review_count=1,
            )
            yield from developers.add_repo(repo, 'admin')
            yield from researchers.add_repo(repo, 'triage')
        else:
            # basic branch protection against force pushes, even for admins
            protect_branch(repo, enforce_admins=True)
            researchers.add_repo(repo, 'admin')


def input_with_timeout(prompt, timeout=5.0):
    print(prompt)
    i, _, _ = select.select([sys.stdin], [], [], 5)
    if i:
        return sys.stdin.readline().strip().lower()
    else:
        return None


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

    org = client.get_org()
    config = yaml.safe_load(open(args.config))

    if mode == 'dry-run':
        print('*** DRY RUN - no changes will be made ***')

    pending_changes = []

    # analyse changes needed
    for change in ensure_teams(config, org):
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
                10.0,
            )
            if answer == 'y':
                for change in pending_changes:
                    print(change)
                    change()
        else:
            print('No changes needed')
    

if __name__ == '__main__':
    main()
