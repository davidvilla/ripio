import sys
import json
from pathlib import Path
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

import logging
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger('git').setLevel(logging.WARN)
logging.getLogger("urllib3").setLevel(logging.WARN)

import requests
import git
import toml

from . import utils

PROGNAME = 'ripio'

class error(Exception):
    def __str__(self):
        msg = self.__class__.__name__
        if len(self.args):
            msg += ": {}".format(self.args[0])

        return msg

    def detail(self, msg):
        return "\n- " + msg

class RepositoryNotFound(error): pass

class RemoteError(error): pass

class ConfigError(error): pass

class MissingConfig(error): pass

class BadRepositoryName(error): pass

class BadWorkspaceName(error):
    def __str__(self):
        return super().__str__() + \
            self.detail("See '{} ls -h' for help".format(PROGNAME))

class UnsupportedSite(error): pass


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

    def __repr__(self):
        return "<WorkspaceName '{}:{}'>".format(self.site, self.workspace)


class RepoName:
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
    def cast(cls, name, site):
        if isinstance(name, RepoName):
            return name

        return RepoName(name, site)

    @classmethod
    def complete(cls, name, config):
        try:
            RepoName(name)
            return name
        except BadRepositoryName:
            pass

        workspaces = []

        try:
            workspaces.append(config.bitbucket.credentials.default.split(':')[0])
        except AttributeError:
            pass

        try:
            workspaces.extend(config.bitbucket.workspaces)
        except AttributeError:
            pass

        if not workspaces:
            raise ConfigError("Requires key 'bitbucket.workspaces' to guess a workspaces")

        for ws in workspaces:
            try:
                full_name = '{}/{}'.format(ws, name)
                BitbucketRepo(full_name).check()
                return full_name
            except RepositoryNotFound as e:
                logging.warning(e)

        raise RepositoryNotFound("in any known workspace '{}'".format(
            str.join(', ', workspaces)))

    def __str__(self):
        return self.full_name


def _common_check(reply, expected, raises):
    raises = raises or {}
    code = reply.status_code
    if code == expected:
        return

    if code in raises:
        raise raises[code]

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    return msg

def bb_check(reply, expected=200, raises=None):
    msg = _common_check(reply, expected, raises)
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


def gh_check(reply, expected=200, raises=None):
    msg = _common_check(reply, expected, raises)
    if msg is None:
        return

    reply_json = reply.json()
    print(reply_json)

    if 'message' in reply_json:
        msg += '\n' + reply_json['message']
    if 'errors' in reply_json:
        msg += '\n' + reply_json['errors'][0]['message']

    raise RemoteError(msg)


def origin_to_fullname(origin):
    path = origin.rstrip('.git')
    if path.startswith('git@'):
        return path.split(':')[1]
    elif path.startswith('https://'):
        return path.split('/', 3)[-1]


class Config:
    def __init__(self, fname):
        self.fname = fname
        self.toml = toml.load(fname)
        self.data = utils.dictToObject(self.toml)

    def __getattr__(self, key):
        return getattr(self.data, key)

    # # FIXME: bitbucket supersedes that
    # @property
    # def credentials(self):
    #     try:
    #         return Credentials(self.bitbucket.credentials.default)
    #     except KeyError:
    #         raise MissingConfig

    def get_credentials(self, site):
        try:
            site = getattr(self, site)
            return Credentials(site.credentials.default)
        except KeyError:
            raise MissingConfig

    @property
    def destdir(self):
        try:
            return Path(self.data.clone.destdir).expanduser()
        except KeyError:
            raise MissingConfig

    def is_valid(self):
        if not set(self.toml.keys()).issubset(set('bitbucket')):
            return False

        return True

    def __repr__(self):
        return "<Config '{}'>".format(self.fname)

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
        return "<Credentials '{}:{}'>".format(self.username, '*' * len(self.password))


class Auth:
    def __init__(self, credentials):
        self.credentials = credentials

    def auth(self, url):
        if self.credentials is None:
            return url

        parts = urlparse(url)
        user_pass = '{}:{}@'.format(self.credentials.username, self.credentials.password)
        return urlunparse(parts._replace(netloc=user_pass + parts.netloc))


class GithubRepo(Auth):
    BASE_URL = 'https://api.github.com/orgs/{}/repos'

    # FIXME: refactor superclass
    def __init__(self, name, credentials=None):
        name = RepoName.cast(name, site='github')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name = name.full_name,
            slug = name.slug)

        self.url = self.auth(self.BASE_URL.format(name.owner.workspace))

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance.basic_data = dict(
            scm = 'git',
            slug = data['name'],
            full_name = data['full_name'],
            size = data['size'],
            access = 'private' if data['private'] else 'public')
        return instance

    # FIXME: refactor superclass
    def __getattr__(self, attr):
        assert attr in 'scm slug full_name size access'.split()

        try:
            return self.basic_data[attr]
        except KeyError:
            return self.data[attr]

    def create(self):
        logging.debug(self.url)

        result = requests.post(
            self.url,
            data=json.dumps({'name':self.slug, 'private': True}))
        gh_check(result, 201)
        real_name = result.json()['full_name']
        return real_name


class BitbucketRepo(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}/'

    def __init__(self, name, credentials=None):
        name = RepoName.cast(name, site='bitbucket')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name = name.full_name,
            slug = name.slug)

        self.url = self.auth(self.BASE_URL.format(self.full_name))
        logging.debug(self.url)

    @classmethod
    def from_dir(cls, dirname, credentials=None):
        origin = git.Repo(Path.cwd()).remote().url
        logging.debug(origin)
        full_name = origin_to_fullname(origin)
        return cls(full_name, credentials)

    @classmethod
    def from_data(cls, data, credentials=None):
        instance = cls(data['full_name'], credentials)
        instance.basic_data = dict(
            scm = data['scm'],
            slug = data['slug'],
            full_name = data['full_name'],
            size = data['size'],
            access = 'private' if data['is_private'] else 'public')
        return instance

    def __getattr__(self, attr):
        assert attr in 'scm slug full_name size access'.split()

        try:
            return self.basic_data[attr]
        except KeyError:
            return self.data[attr]

    def check(self):
        self.data

    @property
    @lru_cache
    def data(self):
        result = requests.get(self.url)
        if result.status_code == 404:
            raise RepositoryNotFound(self.full_name)

        bb_check(result)
        return result.json()

    @property
    @lru_cache
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
    @lru_cache
    def webpage(self):
        return self.data['links']['html']['href']

    def last_commits(self, max_=3):
        commits_url = self.url + 'commits/'
        result = requests.get(commits_url)
        bb_check(result)
        commits = result.json()['values']
        return commits[:3]

    def rename(self, new_name):
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        result = requests.get(self.url)
        if result.status_code == 404:
            raise RepositoryNotFound(self.full_name)

        # FIXME: this is required also on create
        result = requests.put(self.url, data={'name':new_name})
        bb_check(result)
        real_name = result.headers['Location'].split('/')[-1]
        return real_name

    def create(self):
        bb_check(requests.post(self.url))

    def delete(self):
        bb_check(requests.delete(self.url), 204,
               raises={404:RepositoryNotFound(self.full_name)})

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        logging.debug(url)
        git.Repo.clone_from(url, destdir, progress=dash)
        print()


class BitbucketWorkspace(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, workspace, credentials):
        super().__init__(credentials)
        self.url = self.BASE_URL.format(workspace)

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            next_link = self.auth(next_link)
            result = requests.get(next_link)
            logging.debug(next_link)
            bb_check(result)

            page = result.json()
            next_link = page.get('next')
            for repo in page['values']:
                yield BitbucketRepo.from_data(repo, self.credentials)

    def check(self):
        bb_check(requests.get(self.auth(self.url)))


class GithubWorkspace(Auth):
    BASE_ORG_URL = 'https://api.github.com/orgs/{}/repos'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name)

        print(credentials)
        self.url = self.BASE_ORG_URL.format(name.workspace)

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
            gh_check(result)

            page = result.json()
            next_link = get_next_link(result)

            for repo in page:
                yield GithubRepo.from_data(repo, self.credentials)
