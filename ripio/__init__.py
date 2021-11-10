#!/usr/bin/python3

import sys
import json
from pathlib import Path
from functools import lru_cache
from urllib.parse import urlparse, urlunparse
import re
import logging
import socket

import requests
import requests.utils
import git
import toml

from . import utils

logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger('git').setLevel(logging.WARN)
logging.getLogger("urllib3").setLevel(logging.WARN)

PROGNAME = 'ripio'
BITBUCKET = 'bitbucket.org'
GITHUB = 'github.com'

sites = {
    BITBUCKET: 'bitbucket',
    GITHUB: 'github',
}

SITE_ABBREVS = {
    'bitbucket': 'bitbucket',
    'bb':        'bitbucket',
    'github':    'github',
    'gh':        'github'}

CONFIG_USAGE = '''\
Provide a config file with --config argument or its default location: '~/.config/ripio'.

    [clone]
    destdir = "~/repos"

    [bitbucket]
    credentials = "JohnDoe:secret"
    workspaces = ["team1", "team2"]

    [github]
    credentials = "JohnDoe:secret"
    workspaces = ["org1", "org2"]

Use these features to create "safe" credentials:
- https://bitbucket.org/account/settings/app-passwords/
- https://github.com/settings/tokens
'''


class error(Exception):
    def __init__(self, *args):
        self.value = None

    def __str__(self):
        msg = "- " + getattr(self, 'reason', self.__class__.__name__)
        msg += ": {}".format(self.get_value())

        return msg

    def get_value(self):
        if self.value:
            return self.value
        if len(self.args):
            return self.args[0]

        return ''

    def detail(self, msg):
        return "\n- " + msg


class RepositoryNotFound(error):
    def __init__(self, repo_ref):
        assert isinstance(repo_ref, RepoRef)
        self.value = str(repo_ref)


class RemoteError(error): pass

class ConfigError(error): pass

class MissingConfig(error):
    def __str__(self):
        return "ERROR: No config file available.\n\n" + CONFIG_USAGE

class BadRepositoryName(error): pass

class BadWorkspaceName(error):
    def __str__(self):
        return super().__str__() + \
            self.detail("See '{} ls -h' for help".format(PROGNAME))

class UnsupportedSite(error): pass

class DirectoryAlreadyExists(error):
    reason = 'destination directory already EXISTS'

class DirectoryIsNotRepository(error):
    reason = 'destination directory exists but is NOT a git repository'

class WrongCompletion(error): pass

class AccessDenied(error):
    def __init__(self, arg):
        self.value = str(arg)

class AlreadyExists(error): pass

class MissingRemoteOrigin(error):
    reason = "missing remote 'origin' for repository"

class RepositoryAlreadyCloned(error):
    reason = "repository already cloned"

class UnrelatedRepository(error):
    reason = "unrelated destination directory"

    def __init__(self, destdir, repo_ref):
        assert isinstance(repo_ref, RepoRef)
        self.value = str(destdir), str(repo_ref)

class RateLimitExceeded(error):
    reason = "Site rate limit exceeded. Too many requests!"

class NetworkError(error):
    pass


def http_get(*args, **kargs):
    try:
        return requests.get(*args, **kargs)
    except requests.exceptions.ConnectionError as e:
        raise NetworkError(e)


class WorkspaceName:
    def __init__(self, full_workspace, site=None):
        if full_workspace.count(':') > 1 or '/' in full_workspace:
            raise BadWorkspaceName(full_workspace)

        try:
            self.site, self.workspace = full_workspace.split(':')
        except ValueError:
            if site is None:
                raise BadWorkspaceName(full_workspace)

            self.site = site
            self.workspace = full_workspace

        try:
            self.site = SITE_ABBREVS[self.site]
        except KeyError:
            raise UnsupportedSite(self.site)

    def __repr__(self):
        return "<WorkspaceName '{}:{}'>".format(self.site, self.workspace)

    def __str__(self):
        return "{}:{}".format(self.site, self.workspace)


class RepoRef:
    def __init__(self, site_full_name, site=None):
        if site_full_name.startswith(('https://', 'git@', 'ssh://')):
            site_full_name = RepoRef.parse_origin(site_full_name)

        if site_full_name.count('/') != 1:
            raise BadRepositoryName(site_full_name)

        try:
            self.owner, self.slug = site_full_name.split('/')
        except ValueError:
            raise BadRepositoryName(site_full_name)

        self.owner = WorkspaceName(self.owner, site=site)
        self.site = self.owner.site
        self.full_name = '{}/{}'.format(self.owner.workspace, self.slug)
        self.global_name = '{}:{}'.format(self.site, self.full_name)

    @classmethod
    def parse_origin(cls, url):
        if url.startswith('ssh://'):
            fields = re.findall(r'\Assh://git@([^/]+)/(.+).git\Z', url)[0]

        elif url.startswith('git@'):
            fields = re.findall(r'\Agit@([^:]+):(.+).git\Z', url)[0]

        elif url.startswith('https://'):
            fields = re.findall(r'\Ahttps?://([^/]+)/(.+)(?:\.git)?\Z', url)[0]

        else:
            raise BadRepositoryName(url)

        return '{}:{}'.format(sites[fields[0]], fields[1])

    @classmethod
    def from_origin(cls, url):
        return RepoRef(cls.parse_origin(url))

    @classmethod
    def from_parts(cls, workspace, name, site=None):
        return RepoRef(workspace + '/' + name, site=site)

    def __eq__(self, other):
        assert isinstance(other, RepoRef)
        return self.global_name == other.global_name

    @classmethod
    def cast(cls, name, site):
        if isinstance(name, RepoRef):
            return name

        return RepoRef(name, site)

    def __repr__(self):
        return "<RepoRef '{}'>".format(self)

    def __str__(self):
        return self.global_name


class Completion:
    def __init__(self, name, config):
        self.found = []
        self.denied = []
        self.workspaces = self.list_workspaces(config)

        if not self.workspaces:
            raise ConfigError("Requires keys '*.workspaces' to guess repo urls")

        self.complete(name, config)

        if not self.found and not self.denied:
            workspaces = str.join('\n', [" - {}".format(x) for x in self.workspaces])
            logging.error("No guess found for any known workspace:\n{}".format(workspaces))
            raise WrongCompletion(name)

    def complete(self, name, config):
        try:
            RepoRef(name)
            return [name]
        except BadRepositoryName:
            pass

        workspaces = self.workspaces[:]

        # for site:name names
        if name.count(':') == 1:
            site, name = name.split(':')
            workspaces = [ws for ws in workspaces if ws.site == SITE_ABBREVS[site]]

        for ws in workspaces:
            try:
                repo = ws.make_repo(name)
                if repo.checked_exists():
                    self.found.append(repo.ref.global_name)
            except AccessDenied:
                self.denied.append(repo.ref.global_name)

    @classmethod
    def list_workspaces(cls, config):
        retval = []
        ws_class = {'bitbucket': BitbucketWorkspace,
                    'github':    GithubWorkspace}
        sites = ws_class.keys()

        for site in sites:
            credentials = config.get_credentials(site)
            try:
                name = credentials.username
                retval.append(ws_class[site](name, credentials))
            except AttributeError:
                logging.debug("No credentials found for '{}'".format(site))

            try:
                for name in config.get_workspaces(site):
                    retval.append(ws_class[site](name, credentials))
            except AttributeError as e:
                logging.debug(e)

        return retval


class ConfigFile:
    def __init__(self, fname=None):
        if not fname:
            self.data = utils.dictToObject({})
            return

        try:
            self.fname = fname
            self.toml = toml.load(fname)
            self.data = utils.dictToObject(self.toml)
        except toml.decoder.TomlDecodeError as e:
            logging.error("Wrong config file:\n  {}\n".format(e))
            print(CONFIG_USAGE)
            sys.exit(1)

    def __getattr__(self, key):
        return getattr(self.data, key)

    def get_credentials(self, site):
        try:
            site = getattr(self, site)
            return Credentials(site.credentials)
        except AttributeError:
            return None

    def get_workspaces(self, site):
        try:
            return getattr(self.data, site).workspaces
        except AttributeError:
            return []

    @property
    def destdir(self):
        try:
            return Path(self.data.clone.destdir).expanduser()
        except AttributeError:
            return Path.cwd()

    # def is_valid(self):
    #     if not set(self.toml.keys()).issubset(set('bitbucket')):
    #         return False

    #     return True

    def __repr__(self):
        return "<Config '{}'>".format(self.fname)

    def __str__(self):
        retval = "- credentials\n"
        for site in ['bitbucket', 'github']:
            retval += "  - {:10} {}\n".format(site+':', self.get_credentials(site))

        retval += "- workspaces\n"
        for site in ['bitbucket', 'github']:
            retval += "  - {:10} {}\n".format(site+':', self.get_workspaces(site))

        return retval


class Credentials:
    def __init__(self, credentials):
        self.username, self.password = credentials.split(':')

    @classmethod
    def make(cls, credentials):
        if credentials is None:
            return None

        return Credentials(credentials)

    def __eq__(self, other):
        return (self.username, self.password) == (other.username, other.password)

    def __repr__(self):
        return "<Credentials {}>".format(self)

    def __str__(self):
        return "'{}:{}'".format(self.username, '*' * len(self.password))


class Auth:
    def __init__(self, credentials):
        self.credentials = credentials

    def auth(self, url):
        assert url.startswith('https')

        if self.credentials is None:
            return url

        parts = urlparse(url)
        user_pass = '{}:{}@'.format(self.credentials.username, self.credentials.password)
        netloc = parts.netloc
        if '@' in netloc:
            netloc = netloc.split('@')[1]

        return urlunparse(parts._replace(netloc=user_pass + netloc))


def safe_url(url):
    if '@' not in url:
        return url

    parts = urlparse(url)

    # FIXME: regex?
    user_pass, plain_netloc = parts.netloc.split('@')
    username = user_pass.split(':')[0]
    safe_netloc = '{}:{}@{}'.format(username, '****', plain_netloc)
    return urlunparse(parts._replace(netloc=safe_netloc))


class Repo(Auth):
    def check(self):
        "check access to server"
        self.webpage
        return True

    def exists(self):
        try:
            self.check()
            return True
        except RepositoryNotFound:
            return False

    def checked_exists(self):
        "Renamed repos keep old URLs, so must verify same slug"
        try:
            if not self.exists():
                return False

            self._load_full_data()
            return self.slug == self.name
        except RepositoryNotFound:
            pass

        return False

    def _load_full_data(self):
        logging.debug(self.url)

        # FIXME: catch connection exceptions
        result = http_get(self.url)
        self.reply_check(result)
        retval = result.json()
        retval['access'] = self._get_access(retval)
        retval['size'] = self._get_size(retval)
        return retval

    @lru_cache()
    def __getattr__(self, attr):
        try:
            return self._data[attr]
        except KeyError:
            self._data.update(self._load_full_data())
            try:
                return self._data[attr]
            except KeyError:
                raise AttributeError(attr)

    @classmethod
    def from_dir(cls, dirname, credentials=None):
        try:
            origin = git.Repo(dirname).remote().url
        except git.exc.InvalidGitRepositoryError:
            raise DirectoryIsNotRepository(dirname)
        except ValueError:
            raise MissingRemoteOrigin(dirname)

        logging.debug(origin)
        repo_ref = RepoRef.from_origin(origin)
        return cls.make(repo_ref, credentials)

    @classmethod
    def make(cls, repo_ref, credentials):
        repo_classes = {
            'github': GithubRepo,
            'bitbucket': BitbucketRepo,
        }

        try:
            repo_class = repo_classes[repo_ref.site]
        except KeyError:
            raise UnsupportedSite(repo_ref.site)

        return repo_class(repo_ref, credentials.get(repo_ref.site))

    def __repr__(self):
        return "<{} '{}'>".format(self.__class__.__name__, self.ref.global_name)

    def __str__(self):
        return str(self.ref)

    def info(self):
        data = dict(
            webpage=self.webpage,
            origin_https=self.clone_links['https'],
            origin_ssh=self.clone_links['ssh'],
            access=self.access,
            size='{:,} KB'.format(self.size)
        )

        retval = ""
        for key, val in data.items():
            retval += f"- {key.replace('_', ' ')+':':14} {val}\n"

        return retval

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        if proto == 'https':
            url = self.auth(url)

        logging.debug(url)

        try:
            git.Repo.clone_from(url, destdir, progress=dash)
        except git.exc.GitCommandError as e:
            logging.error("cloning failed.")
            if proto == 'https':
                raise e

            print("- retrying with 'https'...")
            self.clone(destdir, proto='https')


def api_check(reply, expected, raises):
    expected = expected or [200]
    raises = raises or {}
    code = reply.status_code
    logging.debug(reply)

    if code in expected:
        return

    if code in raises:
        raise raises[code]

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    return msg


class Bitbucket:
    @classmethod
    def api_check(cls, reply, expected=None, raises=None):
        msg = api_check(reply, expected, raises)
        if msg is None:
            return

        content_type = reply.headers.get('Content-Type')
        logging.debug("Reply Content-Type: '{}'".format(content_type))

        if 'application/json' not in content_type:
            if 'text/html' not in content_type:
                msg += '\n' + reply.text
            raise RemoteError(msg)

        error = reply.json()['error']
        msg += '\n' + error['message']
        if 'detail' in error:
            msg += '\n' + json.dumps(error['detail'], indent=2)

        raise RemoteError(msg)


class Github:
    @classmethod
    def api_check(cls, reply, expected=None, raises=None):
        raises = raises or {}
        raises[403] = RateLimitExceeded(GITHUB)

        msg = api_check(reply, expected, raises)
        if msg is None:
            return

        reply_json = reply.json()

        if 'message' in reply_json:
            msg += '\n' + reply_json['message']
        if 'errors' in reply_json:
            msg += '\n' + reply_json['errors'][0]['message']

        raise RemoteError(msg)


class BitbucketRepo(Repo):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{owner}/{repo}'

    def __init__(self, name, credentials=None):
        self.ref = RepoRef.cast(name, site='bitbucket')

        super().__init__(credentials)
        self._data = dict(
            full_name=self.ref.full_name,
            slug=self.ref.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.ref.owner.workspace, repo=self.ref.slug))

    def reply_check(self, reply, expected=None, raises=None):
        raises = raises or {}
        raises.update({
            403: AccessDenied(self.ref),
            404: RepositoryNotFound(self.ref)
        })
        Bitbucket.api_check(reply, expected, raises=raises)

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance._data.update(dict(
            scm=data['scm'],
            slug=data['slug'],
            full_name=data['full_name'],
            size=cls._get_size(data),
            access=cls._get_access(data)
        ))
        return instance

    @classmethod
    def _get_access(cls, data):
        return 'private' if data['is_private'] else 'public'

    @classmethod
    def _get_size(cls, data):
        return float(data['size']) / 1000

    @property
    @lru_cache()
    def clone_links(self):
        '''Example:
           "clone": [
            {
                "href": "https://bitbucket.org/repo-test/repo11.git",
                "name": "https"
            },
            {
                "href": "git@bitbucket.org:repo-test/repo11.git",
                "name": "ssh"
            }
        ]'''
        retval = {}
        for link in self.links['clone']:
            retval[link['name']] = link['href']
        return retval

    @property
    @lru_cache()
    def webpage(self):
        return self.links['html']['href']

    def last_commits(self, max_=3):
        commits_url = self.url + '/commits'
        result = http_get(commits_url)
        self.reply_check(result)
        commits = result.json()['values']

        for c in commits[:3]:
            yield dict(
                hash    = c['hash'],
                author  = c['author']['raw'],
                date    = c['date'],
                message = c['message'])

    def create(self, private=True):
        result = requests.post(self.url, data={'is_private':private})
        self.reply_check(result, raises={
            400: AlreadyExists(self.ref)
        })
        return self.slug

    def delete(self):
        self.reply_check(requests.delete(self.url), [204])

    def rename(self, new_name):
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        self.check()

        # FIXME: this is required also on create
        result = requests.put(self.url, data={'name': new_name})
        self.reply_check(result)
        real_name = result.headers['Location'].split('/')[-1]
        return real_name

    @property
    @lru_cache()
    def permissions(self):
        URL = 'https://api.bitbucket.org/2.0/user/permissions/repositories?q=repository.name="{}"'
        url = self.auth(URL.format(self.ref.slug))
        print(url)
        result = http_get(url)
        print(self.ref)
        self.reply_check(result)
        result = result.json()

        if len(result['values']) == 0:
            return 'none'

        print(len(result['values']), result['values'])

        result = [r for r in result['values'] if r['repository']['full_name'] == self.ref.full_name][0]
        return result['permission']


class GithubRepo(Repo):
    BASE_URL = 'https://api.github.com/repos/{owner}/{repo}'
    ORG_URL  = 'https://api.github.com/orgs/{org}/repos'
    OWNER_URL = 'https://api.github.com/user/repos'

    # FIXME: refactor superclass
    def __init__(self, name, credentials=None):
        self.ref = RepoRef.cast(name, site='github')

        super().__init__(credentials)
        self._data = dict(
            full_name=self.ref.full_name,
            slug=self.ref.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.ref.owner.workspace, repo=self.ref.slug))

    def reply_check(self, reply, expected=None, raises=None):
        raises = raises or {}
        raises.update({
            401: AccessDenied(self.ref),
            404: RepositoryNotFound(self.ref)
        })
        Github.api_check(reply, expected, raises=raises)

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance._data.update(dict(
            scm='git',
            slug=data['name'],
            full_name=data['full_name'],
            size=cls._get_size(data),
            access=cls._get_access(data)
        ))
        return instance

    @classmethod
    def _get_access(self, data):
        return 'private' if data['private'] else 'public'

    @classmethod
    def _get_size(cls, data):
        return float(data['size'])

    @property
    @lru_cache()
    def clone_links(self):
        return dict(
            ssh=self.ssh_url,
            https=self.clone_url
        )

    @property
    @lru_cache()
    def webpage(self):
        return self.html_url

    def last_commits(self, max_=3):
        # https://developer.github.com/v3/repos/commits/#list-commits
        url = self.url + '/commits'
        logging.debug(url)

        result = http_get(url)
        self.reply_check(result, [200, 409])
        if result.status_code == 409:
            return []

        commits = result.json()

        for c in commits[:3]:
            yield dict(
                hash    = c['sha'],
                author  = "{} <{}>".format(
                    c['commit']['author']['name'], c['commit']['author']['email']),
                date    = c['commit']['author']['date'],
                message = c['commit']['message'])

    def get_user_url(self):
        if self.credentials and \
            self.ref.owner.workspace.lower() == self.credentials.username.lower():
            return self.OWNER_URL

        return self.ORG_URL.format(org=self.ref.owner.workspace)

    def create(self, private=True):
        url = self.auth(self.get_user_url())
        logging.debug(url)

        result = requests.post(
            url,
            data=json.dumps({'name': self.slug, 'private': private}))

        self.reply_check(result, [201], raises={
            422: AlreadyExists(self.ref)
        })
        real_name = result.json()['name']
        return real_name

    def delete(self):
        self.reply_check(requests.delete(self.url), [204])

    def rename(self, new_name):
        # FIXME: github API supports transfers
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        self.check()

        result = requests.patch(
            self.url,
            data=json.dumps({'name': new_name}))

        self.reply_check(result)
        real_name = result.json()['name'].split('/')[-1]
        return real_name


class Workspace(Auth):
    def __str__(self):
        return str(self.name)

    def __repr__(self):
        return "<Workspace '{}'>".format(str(self))


class BitbucketWorkspace(Workspace):
    site = 'bitbucket'
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name, 'bitbucket')

        self.name = name
        self.url = self.BASE_URL.format(name.workspace)

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            next_link = self.auth(next_link)
            result = http_get(next_link)
            logging.debug(next_link)
            Bitbucket.api_check(result)

            page = result.json()
            next_link = page.get('next')
            for repo in page['values']:
                yield BitbucketRepo.from_data(repo, self.credentials)

    def check(self):
        Bitbucket.api_check(http_get(self.auth(self.url)))

    def make_repo(self, reponame):
        return BitbucketRepo(
            RepoRef.from_parts(self.name.workspace, reponame, 'bitbucket'),
            self.credentials)


class GithubWorkspace(Workspace):
    site = 'github'
    ORG_URL   = 'https://api.github.com/orgs/{org}/repos'
    USER_URL  = 'https://api.github.com/users/{user}/repos'
    OWNER_URL = 'https://api.github.com/user/repos'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name, 'github')

        self.name = name
        self.url = self.ORG_URL.format(org=name.workspace)
        result = http_get(self.auth(self.url))
        if result.status_code == 404:
            logging.info("'{}' is not an organization. Trying as user.".format(
                name.workspace))
            self.url = self.get_user_url()

    def get_user_url(self):
        if self.credentials \
            and self.name.workspace.lower() == self.credentials.username.lower():
            return self.OWNER_URL + '?type=owner'

        return self.USER_URL.format(user=self.name.workspace)

    def ls_repos(self):
        def get_next_link(result):
            try:
                return result.links['next']['url']
            except KeyError:
                return None

        next_link = self.url
        while next_link is not None:
            next_link = self.auth(next_link)
            result = http_get(next_link)
            logging.debug(next_link)
            Github.api_check(result)

            page = result.json()
            next_link = get_next_link(result)

            for repo in page:
                yield GithubRepo.from_data(repo, self.credentials)

    def check(self):
        Github.api_check(http_get(self.auth(self.url)))

    def make_repo(self, reponame):
        return GithubRepo(
            RepoRef.from_parts(self.name.workspace, reponame, 'github'),
            self.credentials)
