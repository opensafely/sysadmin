import argparse
import os
import sys
import yaml

from github import Github, GithubException

EXECUTE = False

parser = argparse.ArgumentParser(
    description='Apply policy to OpenSAFELY github org'
)
parser.add_argument('config', help='The team config')
parser.add_argument('--exec', action='store_true',
                    dest='execute',
                    help='Actually execute commands')



class Team():
    def __init__(self, team):
        self.team = team
        # we preload these calls as we check for membership lots
        print('Loading {}...'.format(team.name))
        self.members = {m.login: m for m in team.get_members()}
        self.repos = {r.full_name: r for r in team.get_repos()}

    def add_member(self, member):
        if member.login in self.members:
            return
        if EXECUTE:
            self.team.add_membership(member)
        print('{}: added {} to team'.format(self.team.name, member.login))

    def add_repo(self, repo, permission):
        if repo.full_name not in self.repos:
            if EXECUTE:
                self.team.add_to_repos(repo)
            print('{}: added {} to team'.format(self.team.slug, repo.full_name))

        current = self.team.get_repo_permission(repo)
        # this is a little awkward, as 'maintain' permission is newish, so the
        # library doesn't provide nice access
        if current is None or not current.raw_data[permission]:
            if EXECUTE:
                self.team.set_repo_permission(repo, permission)
            print('{}: granted {} on {}'.format(
                self.team.slug, permission, repo.full_name)
            )


def protect_branch(repo, branch=None, **kwargs):
    """Audit and enforce branch protections.
    
    Keyword args can be used to set additional restrictions, as per:

    https://developer.github.com/v3/repos/branches/#parameters-1 
    
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
            current_protection = protected_branch.get_protection().raw_data
        except GithubException as e:
            if e.status == 404:
                print('{} {}: branch not protected'.format(repo.full_name, branch))
                protection = kwargs
                current_protection = {}
            else:
                raise

        for name, expected in kwargs.items():
            if current_protection.get(name) != expected:
                protection[name] = expected

        for name, value in protection.items():
            print('{} {}: setting {} to {}'.format(repo.full_name, branch, name, value))
        if EXECUTE and protection:
            protected_branch.edit_protection(**protection)


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

    opensafely = Team(org)
    researchers = Team(org.get_team_by_slug('researchers'))
    developers = Team(org.get_team_by_slug('developers'))
    managers = Team(org.get_team_by_slug('managers'))

    # everyone is a researcher
    print('Updating researchers membership...')
    for member in opensafely.members.values():
        # avoid elevating bot accounts
        if member.login not in config['bots']:
            researchers.add_member(member)

    print('Updating developers membership...')
    for dev in config['developers']:
        if dev in opensafely.members:  # protect against deleted users
            developers.add_member(opensafely.members[dev])

    print('Updating admins membership...')
    for manager in config['managers']:
        if manager in opensafely.members:  # protect against deleted users
            managers.add_member(opensafely.members[manager])

    # TODO: remove developers/admins if they are no longer in the groups. In
    # the common case (someone leaving), then I think there is no need, as
    # their account will be removed from the organisation and thus any teams.
    # But we should probably check that

    print('Updating team repositories...')
    for repo in opensafely.repos.values():
        # admins have access to all repos
        managers.add_repo(repo, 'admin')

        # either a protected developer repo or not
        if repo.full_name in config['protected_repositories']:
            # stricter branch protection for these repos 
            protect_branch(
                repo,
                enforce_admins=True,
                required_approving_review_count=1,
            )
            developers.add_repo(repo, 'admin')
            researchers.add_repo(repo, 'triage')
        else:
            # basic branch protection against force pushes, even for admins
            protect_branch(repo, enforce_admins=True)
            researchers.add_repo(repo, 'admin')


if __name__ == '__main__':
    args = parser.parse_args()
    EXECUTE = args.execute

    config = yaml.safe_load(open(args.config))

    if 'GITHUB_TOKEN' not in os.environ:
        print('Error: missing environment variable GITHUB_TOKEN')
        print('You need a Personal Access Token, with the admin:org and all repo permssions')
        print('https://docs.github.com/en/github/authenticating-to-github/creating-a-personal-access-token')
        sys.exit(1)

    gh = Github(os.environ['GITHUB_TOKEN'])
    org = gh.get_organization('OpenSAFELY')

    if not EXECUTE:
        print('*** DRY RUN - no changes will be made ***')

    ensure_teams(config, org)

    if not EXECUTE:
        print('*** DRY RUN - no changes were made ***')
        print('run "{} --exec" to execute'.format(" ".join(sys.argv)))
