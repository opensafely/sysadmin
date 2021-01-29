import os
import sys
from github import Github


ERROR_MSG = '''
Error: missing environment variable ORG_TOKEN. You need a Personal
Access Token, with the admin:org and all repo permssions

https://docs.github.com/en/github/authenticating-to-github/creating-a-personal-access-token
'''


def github_client():
    token = os.environ.get('ORG_TOKEN')
    if not token:
        sys.exit(ERROR_MSG)
    return Github(token)


def get_org(org):
    return github_client().get_organization(org)


class Change():
    def __init__(self, cmd, msg, *args):
        self.cmd = cmd
        self.msg = msg
        self.args = args

    def __str__(self):
        return self.msg.format(*self.args)

    def __call__(self):
        return self.cmd()


class GithubTeam():
    """Represents an organisation or team on Github.

    """

    def __init__(self, team):
        self.team = team
        self._members = None
        self._repos = None

    @property
    def members(self):
        if self._members is None:
            #print(' - loading members for {}'.format(self.team.name))
            self._members = {m.login: m for m in self.team.get_members()}
        return self._members

    @property
    def repos(self):
        if self._repos is None:
            #print(' - loading repos for {}'.format(self.team.name))
            self._repos = {r.full_name: r for r in self.team.get_repos()}
        return self._repos

    def add_member(self, member):
        if member.login not in self.members:
            yield Change(
                lambda: self.team.add_membership(member),
                'add {} to {} team',
                member.login,
                self.team.name,
            )

    def add_repo(self, repo, permission):
        if repo.full_name not in self.repos:
            yield Change(
                lambda: self.team.add_to_repos(repo),
                'add {} repo to {} team',
                repo.name,
                self.team.slug,
            )

        current = self.team.get_repo_permission(repo)
        # this is a little awkward, as 'maintain' permission is newish, so the
        # library doesn't provide nice access
        if current is None or not current.raw_data[permission]:
            yield Change(
                lambda: self.team.set_repo_permission(repo, permission),
                'granted {} on {} to {}',
                permission,
                repo.name,
                self.team.slug,
            )
