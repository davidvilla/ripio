#!/usr/bin/python3

import sys
import json
from pathlib import Path
from functools import lru_cache
from urllib.parse import urlparse, urlunparse
import re
import logging

import requests
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

class DestinationDirectoryAlreadyExists(error):
    reason = 'destination directory already EXISTS'

class WrongCompletion(error): pass

class AccessDenied(error):
    def __init__(self, arg):
        self.value = str(arg)

class AlreadyExists(error): pass


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

        abbrevs = {'bb': 'bitbucket', 'gh': 'github'}
        if self.site in abbrevs:
            self.site = abbrevs[self.site]

        if self.site not in ['bitbucket', 'github']:
            raise UnsupportedSite(self.site)

    def __repr__(self):
        return "<WorkspaceName '{}:{}'>".format(self.site, self.workspace)

    def __str__(self):
        return "{}:{}".format(self.site, self.workspace)


class RepoRef:
    def __init__(self, site_full_name, site=None):
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
    def from_origin(self, path):
        if path.startswith('git@'):
            fields = re.findall(r'\Agit@([^:]+):(.+).git\Z', path)[0]
            return RepoRef('{}:{}'.format(sites[fields[0]], fields[1]))

        elif path.startswith('https://'):
            fields = re.findall(r'\Ahttps?://([^/]+)/(.+).git\Z', path)[0]
            return RepoRef('{}:{}'.format(sites[fields[0]], fields[1]))

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

        for ws in self.workspaces:
            try:
                repo = ws.make_repo(name)
                if repo.exists():
                    self.found.append(repo.name.global_name)
            except AccessDenied:
                self.denied.append(repo.name.global_name)

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
            except AttributeError as e:
                logging.debug(e)

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
        if self.credentials is None:
            return url

        parts = urlparse(url)
        user_pass = '{}:{}@'.format(self.credentials.username, self.credentials.password)
        return urlunparse(parts._replace(netloc=user_pass + parts.netloc))


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
        self.data
        return True

    def exists(self):
        try:
            self.check()
            return True
        except RepositoryNotFound:
            return False

    @property
    @lru_cache()
    def data(self):
        logging.debug(self.url)

        # FIXME: catch connection exceptions
        result = requests.get(self.url)
        self.reply_check(result)
        retval = result.json()
        retval['access'] = self._get_access(retval)
        return retval

    def __getattr__(self, attr):
        assert attr in 'scm slug full_name size access'.split(), \
            "Missing attribute '{}'".format(attr)

        try:
            return self.basic_data[attr]
        except KeyError:
            return self.data[attr]

    @classmethod
    def from_dir(cls, dirname, credentials=None):
        origin = git.Repo(dirname).remote().url
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
        return "<{} '{}'>".format(self.__class__.__name__, self.name.global_name)

    def info(self):
        data = dict(
            access=self.access,
            size=utils.to_kB(self.size)
        )

        retval = ""
        for key, val in data.items():
            retval += f"- {key+':':8} {val:>10}\n"

        return retval


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
        self.name = RepoRef.cast(name, site='bitbucket')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name=self.name.full_name,
            slug=self.name.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.name.owner.workspace, repo=self.name.slug))

    def reply_check(self, reply, expected=None, raises=None):
        raises = raises or {}
        raises.update({
            403: AccessDenied(self.name),
            404: RepositoryNotFound(self.name)
        })
        Bitbucket.api_check(reply, expected, raises=raises)

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance.basic_data = dict(
            scm=data['scm'],
            slug=data['slug'],
            full_name=data['full_name'],
            size=data['size'],
            access='private' if data['is_private'] else 'public')
        return instance

    def _get_access(self, data):
        return 'private' if data['is_private'] else 'public'

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
        for link in self.data['links']['clone']:
            retval[link['name']] = link['href']
        return retval

    @property
    @lru_cache()
    def webpage(self):
        return self.data['links']['html']['href']

    def last_commits(self, max_=3):
        commits_url = self.url + '/commits'
        result = requests.get(commits_url)
        self.reply_check(result)
        commits = result.json()['values']

        for c in commits[:3]:
            yield dict(
                hash    = c['hash'],
                author  = c['author']['raw'],
                date    = c['date'],
                message = c['message'])

    def create(self):
        result = requests.post(self.url, data={'is_private':True})
        self.reply_check(result, raises={
            400: AlreadyExists(self.name)
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

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        logging.debug(url)
        git.Repo.clone_from(url, destdir, progress=dash)
        print()


class GithubRepo(Repo):
    BASE_URL = 'https://api.github.com/repos/{owner}/{repo}'
    ORG_URL  = 'https://api.github.com/orgs/{org}/repos'
    OWNER_URL = 'https://api.github.com/user/repos'

    # FIXME: refactor superclass
    def __init__(self, name, credentials=None):
        self.name = RepoRef.cast(name, site='github')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name=self.name.full_name,
            slug=self.name.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.name.owner.workspace, repo=self.name.slug))

    def reply_check(self, reply, expected=None, raises=None):
        raises = raises or {}
        raises.update({
            401: AccessDenied(self.name),
            404: RepositoryNotFound(self.name)
        })
        Github.api_check(reply, expected, raises=raises)

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance.basic_data = dict(
            scm='git',
            slug=data['name'],
            full_name=data['full_name'],
            size=data['size'],
            access='private' if data['private'] else 'public')
        return instance

    def _get_access(self, data):
        return 'private' if data['private'] else 'public'

    @property
    @lru_cache()
    def clone_links(self):
        return dict(
            ssh=self.data['ssh_url'],
            https=self.data['clone_url']
        )

    @property
    @lru_cache()
    def webpage(self):
        return self.data['html_url']

    def last_commits(self, max_=3):
        # https://developer.github.com/v3/repos/commits/#list-commits
        url = self.url + '/commits'
        logging.debug(url)

        result = requests.get(url)
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
        if self.name.owner.workspace.lower() == self.credentials.username.lower():
            return self.OWNER_URL

        return self.ORG_URL.format(org=self.name.owner.workspace)

    def create(self):
        url = self.auth(self.get_user_url())
        logging.debug(url)

        result = requests.post(
            url,
            data=json.dumps({'name': self.slug, 'private': True}))

        self.reply_check(result, [201], raises={
            422: AlreadyExists(self.name)
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

        print(result.status_code)
        print(result.json())

        self.reply_check(result)
        real_name = result.json()['name'].split('/')[-1]
        return real_name

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        logging.debug(url)
        git.Repo.clone_from(url, destdir, progress=dash)
        print()


class Workspace(Auth):
    def __str__(self):
        return str(self.name)

    def __repr__(self):
        return "<Workspace '{}'>".format(str(self))


class BitbucketWorkspace(Workspace):
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
            result = requests.get(next_link)
            logging.debug(next_link)
            Bitbucket.api_check(result)

            page = result.json()
            next_link = page.get('next')
            for repo in page['values']:
                yield BitbucketRepo.from_data(repo, self.credentials)

    def check(self):
        Bitbucket.api_check(requests.get(self.auth(self.url)))

    def make_repo(self, reponame):
        return BitbucketRepo(
            RepoRef.from_parts(self.name.workspace, reponame, 'bitbucket'),
            self.credentials)


class GithubWorkspace(Workspace):
    ORG_URL   = 'https://api.github.com/orgs/{org}/repos'
    USER_URL  = 'https://api.github.com/users/{user}/repos'
    OWNER_URL = 'https://api.github.com/user/repos'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name, 'github')

        self.name = name
        self.url = self.ORG_URL.format(org=name.workspace)
        result = requests.get(self.auth(self.url))
        if result.status_code == 404:
            logging.info("'{}' is not an organization. Trying as user.".format(
                name.workspace))
            self.url = self.get_user_url()

    def get_user_url(self):
        if self.name.workspace.lower() == self.credentials.username.lower():
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
            result = requests.get(next_link)
            logging.debug(next_link)
            Github.api_check(result)

            page = result.json()
            next_link = get_next_link(result)

            for repo in page:
                yield GithubRepo.from_data(repo, self.credentials)

    def check(self):
        Github.api_check(requests.get(self.auth(self.url)))

    def make_repo(self, reponame):
        return GithubRepo(
            RepoRef.from_parts(self.name.workspace, reponame, 'github'),
            self.credentials)
