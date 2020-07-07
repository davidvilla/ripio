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
        print("Try 'ripio -vvv' for even more detail")
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
        for key in ['destdir', 'bitbucket', 'github']:
            try:
                setattr(args, key, getattr(config, key))
            except ripio.MissingConfig:
                pass

        args.credentials = {
            'bitbucket': config.get_credentials('bitbucket'),
            'github':    config.get_credentials('github')}

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


def cmd_ls_repos(config):
    ws_name = ripio.WorkspaceName(config.owner)
    if ws_name.site == 'bitbucket':
        ws = ripio.BitbucketWorkspace(ws_name, config.credentials.get('bitbucket'))
    elif ws_name.site == 'github':
        ws = ripio.GithubWorkspace(ws_name, config.credentials.get('github'))
    else:
        raise ripio.UnsupportedSite(ws_name.site)

    for i, repo in enumerate(ws.ls_repos()):
        print("{0:>4}. {1:>10} - {2.scm:<3} - {2.access:<7} - {2.full_name:<20}".format(
            i+1, to_kb(repo.size), repo))


def get_repo(config):
    assert config.credentials
    ws_name = ripio.RepoName(config.repo)
    print(ws_name.site)
    print(ws_name.full_name)

    if ws_name.site == 'bitbucket':
        return ripio.BitbucketRepo(ws_name, config.credentials.get('bitbucket'))
    elif ws_name.site == 'github':
        return ripio.GithubRepo(ws_name, config.credentials.get('github'))
    
    raise ripio.UnsupportedSite(ws_name.site)


def cmd_print_head(config):
    # full_name = ripio.RepoName.complete(config.repo, config)
    repo = get_repo(config)
    commits = list(repo.last_commits())
    if not commits:
        print("-- repository '{}' is empty".format(repo.full_name))
        return

    for c in commits:
        print("-- {}\n   {}\n   {}\n\n   {}".format(
            c['hash'], c['author'], c['date'], c['message']))


def cmd_repo_rename(config):
    repo = get_repo(config)
    new_name = repo.rename(config.new_name)
    print("Repository '{}' renamed as '{}/{}'".format(
        repo.name.full_name, repo.name.owner.workspace, new_name))


def cmd_repo_create(config):
    repo = get_repo(config)
    name = repo.create()
    print("Repository '{}' created".format(name))


def cmd_repo_delete(config):
    repo = get_repo(config)
    repo.check()
    confirm_irrecoverable_operation()
    print("Deleting '{}'".format(repo.full_name))
    repo.delete()


def cmd_repo_clone(config):
    repo = get_repo(config)
    destdir = config.destdir / repo.slug

    if Path(destdir).exists():
        raise ripio.DestinationDirectoryAlreadyExists(destdir)

    print("Cloning({}) '{}' to '{}'".format(
        config.proto, repo.full_name, destdir))
    repo.clone(destdir, config.proto)


def cmd_show_config(config):
    config = vars(config)
    del config['func']
    pprint(config)


def cmd_site(config):
    url = ripio.BitbucketRepo.from_dir(Path.cwd(), config.credentials).webpage
    print("Openning '{}'".format(url))
    webbrowser.open(url)


def run():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter,
        description='''\
Manage hosted git repositories.
General repository name format is: 'site:owner/name'.
Examples:
- 'github:twitter/wordpress' or 'gh:twitter/wordpress'
- 'bitbucket:paypal/exmaple' or 'bb:paypal/exmaple'

Abbreviated names are allowed when suitable configuration is given.
''')

    parser.add_argument('--config', help='alternate config file')
    parser.add_argument('-c', '--credentials', type=ripio.Credentials.make,
                        help="authentication credentials with 'user:pass' format")
    parser.add_argument('-v', '--verbosity', action='count', default=0,
                        help='verbosity level. -v:INFO, -vv:DEBUG')
    cmds = parser.add_subparsers()
    parser_ls = cmds.add_parser(
        'ls', help='list repositories',
        formatter_class=argparse.RawTextHelpFormatter,
        description='''\
General workspace format is: 'site:name'.
Examples:
- 'github:twitter'   or 'gh:twitter'
- 'bitbucket:paypal' or 'bb:paypal'

Abbreviated names are allowed when suitable configuration is given.
''')
    parser_ls.set_defaults(func=cmd_ls_repos)
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
    except ripio.error as e:
        if config.verbosity == 0:
            print("Try 'ripio -v' for detail")
        raise


def main_production():
    try:
        run()
        print('-- ok')
    except ripio.error as e:
        print(e)
        print('-- fail')
        sys.exit(1)

def main_debug():
    run()

main = main_debug