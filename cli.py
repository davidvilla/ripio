#!/usr/bin/python3 -u

import sys
import argparse

from repo import Workspace, Repo, Credentials, owner, slug

def to_kb(bytes):
    "to kilobytes, not kibibytes"
    k = float(bytes) / 1000
    return "{:.0f} KB".format(k)


def canceled():
    print('-- canceled')
    sys.exit(1)


def user_confirm(text, valid_answers):
    try:
        answer = input(text)
    except KeyboardInterrupt:
        canceled()

    if answer not in valid_answers:
        canceled()

    return answer


def confirm_irrecoverable_operation():
    user_confirm("This is an IRRECOVERABLE operation!!\nAre you sure? (write uppercase 'yes'): ",
                 valid_answers=['YES'])


def cmd_print_repos(args):
    ws = Workspace(args.owner, args.credentials)
    for i, repo in enumerate(ws.ls_repos()):
        print("{0:>4}. {1:>10} - {2.scm:<3} - {2.access:<7} - {2.full_name:<20}".format(
            i+1, to_kb(repo.size), repo))


def cmd_print_head(args):
    repo = Repo(args.repo, args.credentials)
    commits = repo.last_commits()
    if not commits:
        print("-- repository '{}' is empty".format(repo.fullname))
        return

    for c in commits:
        print("{} - {}\n\t{}\n".format(
            c['date'], c['author']['raw'], c['message']))


def cmd_repo_rename(args):
    repo = Repo(args.repo, args.credentials)
    repo.rename(args.new_name)
    print("Repository '{}' renamed as '{}/{}'".format(
        repo.fullname, owner(repo.fullname), args.new_name))


def cmd_repo_create(args):
    assert args.credentials
    repo = Repo(args.repo, args.credentials)
    repo.create()
    print("Repository '{}' created".format(args.repo))


def cmd_repo_delete(args):
    assert args.credentials
    confirm_irrecoverable_operation()
    repo = Repo(args.repo, args.credentials)
    print("Deleting '{}'\n".format(repo.fullname))
    repo.delete()
    print("-- ok")


def cmd_repo_clone(args):
    repo = Repo(args.repo)
    print("Cloning '{}' to '{}'\n".format(repo.fullname, repo.localpath))
    repo.clone()
    print("-- ok")


parser = argparse.ArgumentParser(description='manage bitbucket repositories')
parser.add_argument('-c', '--credentials', type=Credentials.make,
                    help="authentication credentials with 'user:pass' format")
cmds = parser.add_subparsers()
parser_ls = cmds.add_parser('ls', help='list repositories')
parser_ls.set_defaults(func=cmd_print_repos)
parser_ls.add_argument('owner', help='team or user')

parser_log = cmds.add_parser('head', help='show last commits')
parser_log.set_defaults(func=cmd_print_head)
parser_log.add_argument('repo', help='repo fullname: owner/slug')

parser_rename = cmds.add_parser('rename', help='rename repository')
parser_rename.set_defaults(func=cmd_repo_rename)
parser_rename.add_argument('repo', help='repo fullname: owner/slug')
parser_rename.add_argument('new_name', metavar='new-name', help='new repository name')

parser_create = cmds.add_parser('create', help='create new repository')
parser_create.set_defaults(func=cmd_repo_create)
parser_create.add_argument('repo', help='repo fullname: owner/slug')

parser_delete = cmds.add_parser('delete', help='delete a repository')
parser_delete.set_defaults(func=cmd_repo_delete)
parser_delete.add_argument('repo', help='repo fullname: owner/slug')

parser_clone = cmds.add_parser('clone', help='clone a repository')
parser_clone.set_defaults(func=cmd_repo_clone)
parser_clone.add_argument('repo', help='repo fullname: owner/slug')



args = parser.parse_args()

if not hasattr(args, 'func'):
    parser.print_help()
    sys.exit(1)

args.func(args)
