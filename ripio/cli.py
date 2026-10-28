#!/usr/bin/python3 -u

import os
import sys
import argparse
import logging
from pathlib import Path
import webbrowser
import warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import ripio
import ripio.utils as utils

warnings.filterwarnings('ignore')


VERBOSITY_LEVELS = [logging.ERROR, logging.INFO, logging.DEBUG]


def set_verbosity(args):
    level = VERBOSITY_LEVELS[min(args.verbosity, len(VERBOSITY_LEVELS) - 1)]
    if level != logging.ERROR:
        print("Verbosity set to {}".format(logging.getLevelName(level)))
        if args.verbosity < len(VERBOSITY_LEVELS) - 1:
            print("Try 'ripio -vv' for even more detail")
    logging.getLogger().setLevel(level)


def get_repo(config, name=None, guess=True):
    name = name or config.repo
    try:
        repo_ref = ripio.RepoRef(name)
        print(f"- repository identity is '{repo_ref}'")

    except ripio.BadRepositoryName:
        if not guess:
            raise

        # FIXME: refactor this handler
        print(f"- trying to complete '{name}' at known workspaces...")
        completion = ripio.Completion(name, config.config_file)

        if len(completion.found) == 1:
            repo_ref = ripio.RepoRef(completion.found[0])
            print("- found '{}'".format(repo_ref))

        elif len(completion.found) > 1:
            print("- serveral completions found:")
            for r in completion.found:
                print(f"  - {r}")
            sys.exit(1)

    return ripio.Repo.make(repo_ref, config.credentials)


def cwd_root():
    return ripio.utils.find_in_ancestors('.git', str(Path.cwd()))


def cwd_repo(config):
    return ripio.Repo.from_dir(cwd_root(), config.credentials)


def cmd_ls_repos(config):
    ws_name = ripio.WorkspaceName(config.owner)
    if ws_name.site not in ['bitbucket', 'github']:
        raise ripio.UnsupportedSite(ws_name.site)

    credentials = config.credentials.get(ws_name.site)

    if ws_name.site == 'bitbucket':
        ws = ripio.BitbucketWorkspace(ws_name, credentials)
    else:
        ws = ripio.GithubWorkspace(ws_name, credentials)

    fmt = "{0:>4}. {1:>12,} KB - {2.scm:<3} - {2.access:<7} - {2.full_name:<20}"
    for i, repo in enumerate(ws.ls_repos()):
        print(fmt.format(i+1, repo.size, repo))


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
        repo.ref.full_name, repo.ref.owner.workspace, new_name))


def cmd_repo_create(config):
    repo = get_repo(config, guess=False)
    repo.create(config.private)
    print("- repository '{}' created".format(repo))

    if config.clone:
        config.proto = 'ssh'  # FIXME: add to config file
        _clone(config, repo)


def cmd_repo_delete(config):
    repo = get_repo(config)
    repo.check()
    utils.confirm_irrecoverable_operation()
    repo.delete()
    print("- repository '{}' deleted".format(repo))


def cmd_repo_clone(config):
    repo = get_repo(config)
    _clone(config, repo)


def _clone(config, repo):
    destdir = config.destdir / repo.slug

    if not repo.exists():
        raise ripio.RepositoryNotFound(repo.ref)

    for local_dir, local_ref in ripio.Repo.find_local_clones(
            config.destdir, repo.slug, config.credentials):
        if local_dir == destdir:
            continue
        print("- warning: '{}' is already cloned at '{}'".format(
            local_ref.global_name, utils.pretty_path(local_dir)))

    if Path(destdir).exists():
        local_clone = ripio.Repo.from_dir(destdir, config.credentials)
        if local_clone.ref == repo.ref:
            raise ripio.RepositoryAlreadyCloned(destdir)

        raise ripio.UnrelatedRepository(destdir, local_clone.ref)

    print("- cloning({}) '{}' to '{}'".format(
        config.proto, repo, utils.pretty_path(destdir)))
    repo.clone(destdir, config.proto)
    print()


def cmd_local_status(config):
    if config.issues and config.offline:
        raise ripio.error("'--issues' looks up origins at their sites, so not with '--offline'")

    print("- local clones at '{}'".format(utils.pretty_path(config.destdir)))
    repos = ripio.LocalRepo.find(config.destdir)
    errors = {}

    def listed(repo):
        if not (config.modified or config.unpushed or config.issues):
            return True

        return (config.modified and repo.modified) or (config.unpushed and repo.unpushed) or \
            (config.issues and _has_issue(repo, errors))

    # origins are looked up only for the listed repositories, when known in advance
    if not config.issues:
        repos = [r for r in repos if listed(r)]

    if not config.offline:
        print("- checking origins at their sites...")
        errors = _check_origins(repos, config.credentials)

    repos = [r for r in repos if listed(r)]

    if config.by_date:
        repos.sort(key=lambda r: r.last_modified or datetime.min, reverse=True)

    width = max((len(r.path.name) for r in repos), default=0)
    fmt = "{0:>4}. {1} {2:<2} {3:>6} - {4:<10} - {5:<7} - {6:<{width}} - {7}"
    for i, repo in enumerate(repos):
        # details with 'ripio info'
        problem = _has_issue(repo, errors)
        flags = ('M' if repo.modified else ' ') + ('?' if repo.untracked else ' ')
        unpushed = '+{}'.format(repo.unpushed) if repo.unpushed else ''
        date = repo.last_modified.strftime('%Y-%m-%d') if repo.last_modified else '-'
        origin = repo.ref or (repo.origin and ripio.safe_url(repo.origin)) or '(no origin)'
        print(fmt.format(i+1, _ansi('!', BLINK, RED) if problem else ' ', flags, unpushed,
                         date, repo.access or '-', repo.path.name, origin, width=width))

    _warn_unchecked(errors)


def _has_issue(repo, errors):
    """Origin not found or denied at its site. A former name (renamed or transferred
    origin) is not an issue: sites redirect it, and 'ripio info' reports it"""
    return isinstance(errors.get(repo), (ripio.RepositoryNotFound, ripio.AccessDenied))


BLINK, RED, GREEN = '5', '31', '32'


def _ansi(text, *codes):
    "Styled text, on terminals only"
    if not sys.stdout.isatty() or 'NO_COLOR' in os.environ:
        return text

    return '\033[{}m{}\033[0m'.format(';'.join(codes), text)


def _mark(ok):
    return _ansi('✔', GREEN) if ok else _ansi('✘', RED)


def _check_origins(repos, credentials):
    "Look up the supported origins at their sites, returning errors by repository"
    def check(repo):
        try:
            repo.check_origin(credentials)
        except ripio.error as e:
            return e

    repos = [r for r in repos if r.ref is not None]
    with ThreadPoolExecutor(max_workers=16) as pool:
        return {repo: e for repo, e in zip(repos, pool.map(check, repos)) if e is not None}


def _warn_unchecked(errors):
    "Site-wide errors (rate limit, network...), not about a repository, are reported once"
    unchecked = {}
    for error in errors.values():
        if isinstance(error, (ripio.RepositoryNotFound, ripio.AccessDenied)):
            continue

        key = (type(error), str(error.get_value()))
        unchecked.setdefault(key, []).append(error)

    for same_errors in unchecked.values():
        print("- warning: {} origins could not be checked:".format(len(same_errors)))
        print(same_errors[0])


def cmd_show_config(config):
    config = vars(config)
    del config['func']

    print("command line config:")
    for key, value in sorted(config.items()):
        if key in ['config_file', 'parser']:
            continue

        print(f"- {key}: '{value}'")

    print("\nconfig file:")
    print(config['config_file'])
    print("run 'ripio help' for a config file example.")


def cmd_site(config):
    repo = cwd_repo(config)
    url = repo.webpage
    print("- openning '{}'".format(url))
    webbrowser.open(url)


def clone_dir(config):
    "Clone directory given as repo argument: a path, or a directory at destdir"
    for path in [Path(config.repo), config.destdir / config.repo]:
        if (path / '.git').exists():
            return path

    return None


def cmd_info(config):
    local_dirs = []
    root = clone_dir(config) if config.repo else cwd_root()
    if config.repo and root is None:
        repo = get_repo(config)
    else:
        repo = ripio.Repo.from_dir(root, config.credentials)
        local_dirs.append(Path(root))

    try:
        print(repo.info(), end='')
    except ripio.error as e:
        # the site failed (i.e. repository not found), but local clones are still shown
        print("- {:14} {}".format('name:', repo.ref))
        problem = {ripio.RepositoryNotFound: 'NOT found',
                   ripio.AccessDenied: 'access DENIED'}.get(type(e))
        _print_clones(config, [repo.ref], local_dirs, problem=problem)
        raise

#    print(repo.permissions)

    _print_last_commit(repo)
    _print_ci(repo)

    current = ripio.RepoRef(repo.canonical_name)
    _print_clones(config, [repo.ref, current], local_dirs, current, config.credentials)


def _print_last_commit(repo):
    try:
        commit = next(iter(repo.last_commits()), None)
    except ripio.error as e:
        print("- {:14} unavailable ({})".format('last commit:', type(e).__name__))
        return

    title = 'none'
    if commit is not None:
        title = (commit['message'].strip().splitlines() or [''])[0]

    print("- {:14} {}".format('last commit:', title))


def _print_ci(repo):
    try:
        run = repo.last_ci_run()
    except NotImplementedError:
        return
    except ripio.error as e:
        print("- {:14} unavailable ({})".format('CI:', type(e).__name__))
        return

    if run is None:
        print("- {:14} no runs".format('CI:'))
        return

    if run['status'] == 'completed':
        state = '{} {}'.format(_mark(run['conclusion'] == 'success'), run['conclusion'].upper())
        duration, end = 'duration:', run['updated']
    else:
        state = run['status'].replace('_', ' ').upper()
        duration, end = 'elapsed:', datetime.now()

    seconds = (end - run['started']).total_seconds()
    print("- {:14} {}, workflow '{}'".format('CI:', state, run['workflow']))
    print("  - {:12} {}".format('commit:', run['title']))
    print("  - {:12} {}".format('executed:', _when(run['started'])))
    print("  - {:12} {}".format(duration, ripio.format_duration(seconds)))


def _print_clones(config, refs, local_dirs, current=None, credentials=None, problem=None):
    clones = ripio.LocalRepo.find_clones(config.destdir, refs, credentials)
    found = {c.path.resolve() for c in clones}
    clones += [ripio.LocalRepo(d) for d in local_dirs if d.resolve() not in found]

    if not clones:
        print("- {:14} none at '{}'".format('local clone:', utils.pretty_path(config.destdir)))

    for clone in clones:
        _print_clone_info(clone, current, problem)


def _when(date):
    "Date and how long ago, like '2026-10-28 19:36 (1h 23m ago)'"
    ago = ripio.format_duration((datetime.now() - date).total_seconds())
    return '{:%Y-%m-%d %H:%M} ({} ago)'.format(date, ago)


def _commits(n):
    return '{} commit{}'.format(n, '' if n == 1 else 's')


def _print_clone_info(clone, current, problem=None):
    to_push = []
    if clone.unpushed:
        to_push.append(_commits(clone.unpushed))
    if clone.modified:
        to_push.append('uncommitted changes')
    if clone.untracked:
        to_push.append('untracked files')

    to_pull = _commits(clone.unpulled) if clone.unpulled else 'nothing'
    to_pull += ', as of last fetch'
    if clone.last_fetch:
        to_pull += ' on ' + _when(clone.last_fetch)

    last_change = '-'
    if clone.last_modified:
        last_change = _when(clone.last_modified)

    print("- {:14} {}".format('local clone:', utils.pretty_path(clone.path)))

    # sites ignore case in owner and repository names
    if current is not None and clone.ref.global_name.lower() != current.global_name.lower():
        problem = 'former name'

    if problem is not None:
        print("  - {:12} {} {} ({})".format('origin:', _mark(False), clone.ref, problem))

    print("  - {:12} {}".format('last change:', last_change))
    print("  - {:12} {} {}".format('to push:', _mark(not to_push), ', '.join(to_push) or 'nothing'))
    print("  - {:12} {} {}".format('to pull:', _mark(not clone.unpulled), to_pull))


def cmd_ls_colab(config):
    repo = get_repo(config)
    print(repo.list_collaborators())


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

        # an explicit '--destdir' takes precedence over the config file
        if getattr(self, 'destdir', None) is None:
            self.destdir = self.config_file.destdir

    @property
    def credentials(self):
        return {
            'bitbucket': self.config_file.get_credentials('bitbucket'),
            'github':    self.config_file.get_credentials('github')
            }


def run():
    repo_help = 'repo ref (site:owner/slug), url or name'

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='''\
Manage hosted git repositories.

General repository reference format is: 'site:owner/name', like:
- 'github:twitter/wordpress' or 'gh:twitter/wordpress'
- 'bitbucket:paypal/example' or 'bb:paypal/example'

Abbreviated references are allowed (when 'workspaces' are configured):
- 'example'
- 'github:example' o 'gh:example'

URLs are valid too:
- 'https://github.com/twitter/wordpress'
- 'git@github.com:twitter/wordpress.git'
''' + ripio.CONFIG_USAGE)

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
    parser_create.add_argument('--public', dest='private', default=True,
                               action='store_false',
                               help='set public, default is private')
    parser_create.add_argument('--clone', action='store_true',
                               help='clone after create')
    parser_create.add_argument('repo', help=repo_help)

    parser_delete = cmds.add_parser('delete', help='delete a repository')
    parser_delete.set_defaults(func=cmd_repo_delete)
    parser_delete.add_argument('repo', help=repo_help)

    parser_clone = cmds.add_parser('clone', help='clone a repository')
    parser_clone.set_defaults(func=cmd_repo_clone)
    parser_clone.add_argument('--http', dest='proto', default='ssh',
                              action='store_const', const='https',
                              help='Use HTTP instead of SSH')
    parser_clone.add_argument('--destdir', type=Path,
                              help='directory where save repository')
    parser_clone.add_argument('repo', help=repo_help)

    parser_local = cmds.add_parser('local', help='inspect local clones')
    local_cmds = parser_local.add_subparsers(metavar='COMMAND', required=True)

    parser_local_status = local_cmds.add_parser(
        'status', help='list local clones and their pending changes',
        formatter_class=argparse.RawTextHelpFormatter,
        description='''\
List the git repositories at the clone directory ('[clone] destdir' in the
config file). Remote branches are the ones known since the last fetch.

  M   changes in tracked files (staged or not)
  ?   untracked files
  +N  N commits not pushed to any remote (local branches and detached HEAD)
  !   origin not found or access denied at its site ('ripio info DIR')

Given several of '-m', '-u' and '-i', repositories matching any of them are listed.

The date is the last modification: the newest of the last commit in local
branches (or detached HEAD) and the files with local changes.

Origins at github/bitbucket are looked up to show their access (private or
public) and their problems ('!'). Bitbucket does not redirect the former
names of renamed repositories: they are not found.
''')
    parser_local_status.set_defaults(func=cmd_local_status)
    parser_local_status.add_argument('--destdir', type=Path,
                                     help='directory with the local clones')
    parser_local_status.add_argument('-m', '--modified', action='store_true',
                                     help='only repositories with changes in tracked files')
    parser_local_status.add_argument('-u', '--unpushed', action='store_true',
                                     help='only repositories with unpushed commits')
    parser_local_status.add_argument('-i', '--issues', action='store_true',
                                     help="only repositories with origin issues ('!')")
    parser_local_status.add_argument('-t', '--by-date', action='store_true',
                                     help='sort by last modification, newest first')
    parser_local_status.add_argument('--offline', action='store_true',
                                     help='do not look up origins at their sites')

    parser_config = cmds.add_parser('config', help='show config')
    parser_config.set_defaults(func=cmd_show_config)

    parser_site = cmds.add_parser('site', help='open webpage for the current repository')
    parser_site.set_defaults(func=cmd_site)

    parser_info = cmds.add_parser('info', help='show repository info and its local clones')
    parser_info.set_defaults(func=cmd_info)
    parser_info.add_argument('repo', nargs='?', help=repo_help + ', or clone directory')

    parser_ls_colabs = cmds.add_parser('ls-colab', help='list collaborators')
    parser_ls_colabs.set_defaults(func=cmd_ls_colab)
    parser_ls_colabs.add_argument('repo', help=repo_help)

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
