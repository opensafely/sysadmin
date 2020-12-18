import argparse
import glob
import os
import re
import sys
import subprocess

from github import Github
from github.GithubException import GithubException
import yaml

import client

ORG_NAME = "OpenSAFELY"
REPO_NAME_PATTERN = "^.*-research$"
BASE_PATH = os.path.abspath("research")


def main():
    parser = argparse.ArgumentParser(prog="repoupdater")
    subparsers = parser.add_subparsers(help="sub-command help", dest="subcommand")
    list_parser = subparsers.add_parser("list", help="list all repos")
    update_parser = subparsers.add_parser(
        "update", help="update all repos via pull or clone"
    )
    exec_parser = subparsers.add_parser(
        "exec", help="execute command against all repos"
    )
    exec_parser.add_argument("command")
    exec_parser.add_argument("args", nargs="*")
    pull_request_parser = subparsers.add_parser(
        "pull-request", help="submit pull request against each repo"
    )
    pull_request_parser.add_argument("branch")
    pull_request_parser.add_argument("title")

    args = parser.parse_args()

    if args.subcommand == "list":
        list_()
    elif args.subcommand == "update":
        update()
    elif args.subcommand == "exec":
        exec_([args.command] + args.args)
    elif args.subcommand == "pull-request":
        pull_request(args.branch, args.title)
    else:
        assert False, args.subcommand


def list_():
    client = get_client()
    for repo in get_repos(client):
        print(repo.html_url)


def update():
    client = get_client()
    repos = get_repos(client)
    if check_for_uncommitted_changes(repos):
        sys.exit(1)

    for repo in repos:
        path = os.path.join(BASE_PATH, repo.name)
        print("-" * 80)
        print(repo.name)

        if os.path.exists(path):
            os.chdir(path)
            subprocess.run(["git", "checkout", "master"], check=True)
            subprocess.run(["git", "pull"], check=True)
        else:
            subprocess.run(["git", "clone", repo.ssh_url, path], check=True)


def exec_(argv):
    for path in sorted(glob.glob(os.path.join(BASE_PATH, "*"))):
        print("-" * 80)
        print(os.path.basename(path))
        os.chdir(path)
        subprocess.run(argv)


def pull_request(branch, title):
    client = get_client()
    repos = get_repos(client)

    for repo in repos:
        path = os.path.join(BASE_PATH, repo.name)
        print("-" * 80)
        print(repo.name)
        os.chdir(path)
        subprocess.run(["git", "checkout", branch], check=True)
        subprocess.run(["git", "push", "-u", "origin", branch], check=True)
        try:
            repo.create_pull(head=branch, base="master", title=title, body="")
        except GithubException as e:
            if e.status == 422:
                print(e)
                continue
            raise


def get_client():
    return client.github_client()


def get_repos(client, org_name=ORG_NAME):
    org = client.get_organization(org_name)
    protected = config = yaml.safe_load(open('config.yaml'))['protected_repositories']
    repos = [repo for repo in org.get_repos() if repo.full_name not in protected]
    return sorted(repos, key=lambda repo: repo.name)


def check_for_uncommitted_changes(repos):
    found_uncommitted_changes = False

    for repo in repos:
        path = os.path.join(BASE_PATH, repo.name)
        if os.path.exists(path):
            os.chdir(path)

            p = subprocess.run(["git", "status", "--porcelain"], capture_output=True)
            if p.stdout:
                print("-" * 80)
                print(f"Uncommitted changes to {repo.name}")
                subprocess.run(["git", "status"])
                found_uncommitted_changes = True

    return found_uncommitted_changes


if __name__ == "__main__":
    main()
