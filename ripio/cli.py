#!/usr/bin/python3 -u

import os
import sys
import argparse
import logging
from pathlib import Path
import webbrowser

import ripio
import ripio.utils as utils


def set_verbosity(args):
    level = logging.ERROR - 10 * min(args.verbosity, 3)
    if level != logging.ERROR:
        print("Verbosity set to {}".format(logging.getLevelName(level)))
        print("Try 'ripio -vvv' for even more detail")
    logging.getLogger().setLevel(level)


def get_repo(config, name=None, guess=True):
    name = name or config.repo
    try:
        repo_ref = ripio.RepoRef(name)
        print(f"- repository identity is '{repo_ref}'")

    except ripio.BadRepositoryName:
        if not guess:
            raise

        print(f"- trying to complete '{name}' at known workspaces...")
        completion = ripio.Completion(name, config.config_file)

        if len(completion.found) == 1:
            repo_ref = ripio.RepoRef(completion.found[0])
            print("- guessing '{}'".format(repo_ref))

        elif len(completion.found) > 1:
            print("- serveral completions found:")
            for r in completion.found:
                print(f"  - {r}")
            sys.exit(1)

    return ripio.Repo.make(repo_ref, config.credentials)


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
            i+1, utils.to_kB(repo.size), repo))


def cmd_print_head(config):
    repo = get_repo(config)
    commits = list(repo.last_commits())
    if not commits:
        print("- repository '{}' is empty".format(repo.full_name))
        return

    for c in commits:
        print("-- {}\n   {}\n   {}\n\n   {}".format(
            c['hash'], c['author'], c['date'], c['message']))


def cmd_repo_rename(config):
    repo = get_repo(config)
    new_name = repo.rename(config.new_name)
    print("- repository '{}' renamed as '{}/{}'".format(
        repo.name.full_name, repo.name.owner.workspace, new_name))


def cmd_repo_create(config):
    repo = get_repo(config, guess=False)
    name = repo.create()
    print("- repository '{}' created".format(name))


def cmd_repo_delete(config):
    repo = get_repo(config)
    repo.check()
    utils.confirm_irrecoverable_operation()
    print("- deleting '{}'".format(repo.full_name))
    repo.delete()


def cmd_repo_clone(config):
    repo = get_repo(config)
    destdir = config.destdir / repo.slug

    if not repo.exists():
        raise ripio.RepositoryNotFound(repo.name)

    if Path(destdir).exists():
        raise ripio.DestinationDirectoryAlreadyExists(destdir)

    print("- cloning({}) '{}' to '{}'".format(
        config.proto, repo.full_name, utils.pretty_path(destdir)))
    repo.clone(destdir, config.proto)


def cmd_show_config(config):
    config = vars(config)
    del config['func']

    print("Command line config:")
    for key, value in sorted(config.items()):
        if key in ['config_file', 'parser']:
            continue

        print(f"- {key}: '{value}'")

    print("\nConfig file:")
    print(config['config_file'])


def cmd_site(config):
    root = ripio.utils.find_in_ancestors('.git', str(Path.cwd()))
    repo = ripio.Repo.from_dir(root, config.credentials)
    url = repo.webpage
    print("- openning '{}'".format(url))
    webbrowser.open(url)


def cmd_info(config):
    repo = get_repo(config)
    print(repo.info())


def cmd_help(config):
    config.parser.print_help()


class BaseConfig(argparse.Namespace):
    def load_file(self):
        if os.path.exists(self.config):
            self.config_file = ripio.ConfigFile(self.config)
        else:
            raise ripio.MissingConfig

        # FIXME: verbosity argument does not affect this, it is previous to setLevel
        # logging.debug("Loading config '{}'".format(self.config_file.fname))

        self.destdir = self.config_file.destdir

    @property
    def credentials(self):
        return {
            'bitbucket': self.config_file.get_credentials('bitbucket'),
            'github':    self.config_file.get_credentials('github')
            }


def run():
    repo_help = 'repo ref (site:owner/slug) or name'

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='''\
Manage hosted git repositories.
General repository name format is: 'site:owner/name'.
Examples:
- 'github:twitter/wordpress' or 'gh:twitter/wordpress'
- 'bitbucket:paypal/exmaple' or 'bb:paypal/exmaple'

Abbreviated names are allowed when suitable configuration is given.
''')

    parser.add_argument('--config', help='alternate config file',
                        default=Path.home() / '.config/ripio')
    parser.add_argument('-v', '--verbosity', action='count', default=0,
                        help='verbosity level. -v:INFO, -vv:DEBUG')
    cmds = parser.add_subparsers()

    parser_help = cmds.add_parser('help', help='show help')
    parser_help.set_defaults(func=cmd_help)

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
    parser_head.add_argument('repo', help=repo_help)

    parser_rename = cmds.add_parser('rename', help='rename repository')
    parser_rename.set_defaults(func=cmd_repo_rename)
    parser_rename.add_argument('repo', help=repo_help)
    parser_rename.add_argument('new_name', metavar='new-name',
                               help='new repository name')

    parser_create = cmds.add_parser('create', help='create new repository')
    parser_create.set_defaults(func=cmd_repo_create)
    parser_create.add_argument('repo', help=repo_help)

    parser_delete = cmds.add_parser('delete', help='delete a repository')
    parser_delete.set_defaults(func=cmd_repo_delete)
    parser_delete.add_argument('repo', help=repo_help)

    parser_clone = cmds.add_parser('clone', help='clone a repository')
    parser_clone.set_defaults(func=cmd_repo_clone)
    parser_clone.add_argument('--http', dest='proto', default='ssh',
                              action='store_const', const='https',
                              help='Use HTTP instead of SSH')
    parser_clone.add_argument('--destdir', default=Path.cwd(), type=Path,
                              help='directory where save repository')
    parser_clone.add_argument('repo', help=repo_help)

    parser_config = cmds.add_parser('config', help='show config')
    parser_config.set_defaults(func=cmd_show_config)

    parser_site = cmds.add_parser('site', help='open webpage for the current repository')
    parser_site.set_defaults(func=cmd_site)

    parser_info = cmds.add_parser('info', help='show repository info')
    parser_info.set_defaults(func=cmd_info)
    parser_info.add_argument('repo', help=repo_help)

    config = parser.parse_args(namespace=BaseConfig())
    config.load_file()
    set_verbosity(config)

    if not hasattr(config, 'func'):
        parser.print_help()
        sys.exit(1)

    try:
        config.parser = parser
        config.func(config)
    except ripio.error:
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
main = main_production
