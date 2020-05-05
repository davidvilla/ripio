#!/usr/bin/python3 -u

import sys
import argparse
import logging
from pathlib import Path
from pprint import pprint
import webbrowser

import ripio


def to_kb(bytes):
    "to kilobytes, not kibibytes"
    k = float(bytes) / 1000
    return "{:.0f} KB".format(k)


def set_verbosity(args):
    level = logging.ERROR - 10 * min(args.verbosity, 3)
    if level != logging.ERROR:
        print("Verbosity set to {}".format(logging.getLevelName(level)))
    logging.getLogger().setLevel(level)


def load_config(args):
    config = None
    home_config = Path.home() / '.ripio'

    if args.config is not None:
        config = ripio.Config(args.config)
    elif home_config.exists():
        config = ripio.Config(home_config)

    logging.debug("Loading config '{}'".format(config.fname))

    try:
        for key in ['credentials', 'destdir', 'bitbucket']:
            try:
                setattr(args, key, getattr(config, key))
            except ripio.MissingConfig:
                pass

    except FileNotFoundError as e:
        logging.error(e)


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


def cmd_print_repos(config):
    ws = ripio.Workspace(config.owner, config.credentials)
    for i, repo in enumerate(ws.ls_repos()):
        print("{0:>4}. {1:>10} - {2.scm:<3} - {2.access:<7} - {2.full_name:<20}".format(
            i+1, to_kb(repo.size), repo))


def cmd_print_head(config):
    full_name = ripio.RepoName.complete(config.repo, config)
    repo = ripio.Repo(full_name, config.credentials)
    commits = repo.last_commits()
    if not commits:
        print("-- repository '{}' is empty".format(repo.full_name))
        return

    for c in commits:
        print("{} - {}\n\t{}\n".format(
            c['date'], c['author']['raw'], c['message'].strip()))


def cmd_repo_rename(config):
    repo = ripio.Repo(config.repo, config.credentials)
    new_name = repo.rename(config.new_name)
    print("Repository '{}' renamed as '{}/{}'".format(
        repo.full_name, repo.full_name.owner, new_name))


def cmd_repo_create(config):
    assert config.credentials
    repo = ripio.Repo(config.repo, config.credentials)
    repo.create()
    print("Repository '{}' created".format(config.repo))


def cmd_repo_delete(config):
    assert config.credentials
    confirm_irrecoverable_operation()
    repo = ripio.Repo(config.repo, config.credentials)
    print("Deleting '{}'".format(repo.full_name))
    repo.delete()


def cmd_repo_clone(config):
    full_name = ripio.RepoName.complete(config.repo, config)
    repo = ripio.Repo(full_name, config.credentials)
    destdir = config.destdir / repo.slug
    print("Cloning({}) '{}' to '{}'".format(
        config.proto, repo.full_name, destdir))
    repo.clone(destdir, config.proto)


def cmd_show_config(config):
    config = vars(config)
    del config['func']
    pprint(config)


def cmd_site(config):
    url = ripio.Repo.from_dir(Path.cwd(), config.credentials).webpage
    print("Openning '{}'".format(url))
    webbrowser.open(url)


def run():
    parser = argparse.ArgumentParser(description='Manage hosted git repositories')
    parser.add_argument('--config', help='alternate config file')
    parser.add_argument('-c', '--credentials', type=ripio.Credentials.make,
                        help="authentication credentials with 'user:pass' format")
    parser.add_argument('-v', '--verbosity', action='count', default=0,
                        help='verbosity level. -v:INFO, -vv:DEBUG')
    cmds = parser.add_subparsers()
    parser_ls = cmds.add_parser('ls', help='list repositories')
    parser_ls.set_defaults(func=cmd_print_repos)
    parser_ls.add_argument('owner', help='team or user')

    parser_head = cmds.add_parser('head', help='show last commits')
    parser_head.set_defaults(func=cmd_print_head)
    parser_head.add_argument('repo', help='repo fullname: owner/slug')

    parser_rename = cmds.add_parser('rename', help='rename repository')
    parser_rename.set_defaults(func=cmd_repo_rename)
    parser_rename.add_argument('repo', help='repo fullname: owner/slug')
    parser_rename.add_argument('new_name', metavar='new-name',
                               help='new repository name')

    parser_create = cmds.add_parser('create', help='create new repository')
    parser_create.set_defaults(func=cmd_repo_create)
    parser_create.add_argument('repo', help='repo fullname: owner/slug')

    parser_delete = cmds.add_parser('delete', help='delete a repository')
    parser_delete.set_defaults(func=cmd_repo_delete)
    parser_delete.add_argument('repo', help='repo fullname: owner/slug')

    parser_clone = cmds.add_parser('clone', help='clone a repository')
    parser_clone.set_defaults(func=cmd_repo_clone)
    parser_clone.add_argument('--http', dest='proto', default='ssh',
                            action='store_const', const='https',
                            help='Use HTTP instead of SSH')
    parser_clone.add_argument('--destdir', default=Path.cwd(), type=Path,
                              help='directory where save repository')
    parser_clone.add_argument('repo', help='repo fullname: owner/slug')

    parser_config = cmds.add_parser('config', help='show config')
    parser_config.set_defaults(func=cmd_show_config)

    parser_site = cmds.add_parser('site', help='open webpage for the current repository')
    parser_site.set_defaults(func=cmd_site)


    config = parser.parse_args()
    set_verbosity(config)
    load_config(config)

    if not hasattr(config, 'func'):
        parser.print_help()
        sys.exit(1)

    try:
        config.func(config)
        print('-- ok')
    except ripio.error as e:
        print(e)
        print('-- fail')
        if config.verbosity == 0:
            print("Try 'ripio -v[v][v]' for more detail")
        sys.exit(1)