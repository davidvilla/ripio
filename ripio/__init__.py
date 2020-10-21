#!/usr/bin/python3

import sys
import json
from pathlib import Path
from functools import lru_cache
from urllib.parse import urlparse, urlunparse
import re

import logging
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger('git').setLevel(logging.WARN)
logging.getLogger("urllib3").setLevel(logging.WARN)

import requests
import git
import toml

from . import utils

PROGNAME = 'ripio'


CONFIG_USAGE = '''\
ERROR: No config file available.

Provide a config file with -c argument or default location: ~/.config/ripio.

    [clone]
    destdir = "~/repos"

    [bitbucket]
    workspaces = ["team1", "team2"]

    [bitbucket.credentials]
    default = "JohnDoe:secret"

    [github]
    workspaces = ["org1", "org2"]

    [github.credentials]
    default = "JohnDoe:secret"

Use these features to create "safe" passwords:
- https://bitbucket.org/account/settings/app-passwords/
- https://github.com/settings/tokens
'''

class error(Exception):
    def __str__(self):
        msg = "- " + getattr(self, 'reason', self.__class__.__name__)
        if len(self.args):
            msg += ": {}".format(self.args[0])

        return msg

    def detail(self, msg):
        return "\n- " + msg


class RepositoryNotFound(error): pass

class RemoteError(error): pass

class ConfigError(error): pass

class MissingConfig(error):
    def __str__(self):
        return CONFIG_USAGE

class BadRepositoryName(error): pass

class BadWorkspaceName(error):
    def __str__(self):
        return super().__str__() + \
            self.detail("See '{} ls -h' for help".format(PROGNAME))

class UnsupportedSite(error): pass

class DestinationDirectoryAlreadyExists(error):
    reason = 'destination directory already exists'


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
    def from_origin(self, path):
        sites = {
            'github.com': 'github',
            'bitbucket.org': 'bitbucket'
        }

        if path.startswith('git@'):
            fields = re.findall(r'\Agit@([^:]+):(.+).git\Z', path)[0]
            return RepoName('{}:{}'.format(sites[fields[0]], fields[1]))

        elif path.startswith('https://'):
            fields = re.findall(r'\Ahttps?://([^/]+)/(.+).git\Z', path)[0]
            return RepoName('{}:{}'.format(sites[fields[0]], fields[1]))

    def __eq__(self, other):
        return self.global_name == other.global_name

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
                BitbucketRepo(full_name).check()  # FIXME
                return full_name
            except RepositoryNotFound as e:
                logging.warning(e)

        raise RepositoryNotFound("in any known workspace '{}'".format(
            str.join(', ', workspaces)))

    def __repr__(self):
        return "<RepoName '{}:{}'>".format(self.site, self.full_name)


def _common_api_check(reply, expected, raises):
    expected = expected or [200]
    raises = raises or {}
    code = reply.status_code

    if code in expected:
        return

    if code in raises:
        raise raises[code]

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    return msg


class ConfigFile:
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


def safe_url(url):
    if not '@' in url:
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

    @classmethod
    def from_dir(cls, dirname, credentials=None):
        origin = git.Repo(Path.cwd()).remote().url
        logging.debug(origin)
        repo_name = RepoName.from_origin(origin)
        return cls.make(repo_name, credentials)
        
    @classmethod
    def make(cls, repo_name, credentials):
        repo_classes = {
            'github': GithubRepo,
            'bitbucket': BitbucketRepo,
        }

        try:
            repo_class = repo_classes[repo_name.site]
        except KeyError:
            raise UnsupportedSite(repo_name.site)            

        return repo_class(repo_name, credentials.get(repo_name.site))
       
    def __repr__(self):
        return "<{} '{}'>".format(self.__class__.__name__, self.name.global_name)


class BitbucketRepo(Repo):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{owner}/{repo}'

    def __init__(self, name, credentials=None):
        self.name = RepoName.cast(name, site='bitbucket')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name = self.name.full_name,
            slug = self.name.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.name.owner.workspace, repo=self.name.slug))

    @classmethod
    def api_check(cls, reply, expected=None, raises=None):
        msg = _common_api_check(reply, expected, raises)
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
        assert attr in 'scm slug full_name size access'.split(), "Missing attribute '{}'".format(attr)

        try:
            return self.basic_data[attr]
        except KeyError:
            return self.data[attr]

    # FIXME: Refactor superclass
    @property
    @lru_cache()
    def data(self):
        logging.debug(self.url)

        # FIXME: catch connection exceptions
        result = requests.get(self.url)

        # FIXME: api_check may do this
        if result.status_code == 404:
            print("___>", self.name)
            raise RepositoryNotFound(self.name)

        self.api_check(result)
        return result.json()

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
        self.api_check(result)
        commits = result.json()['values']

        for c in commits[:3]:
            yield dict(
                hash    = c['hash'],
                author  = c['author']['raw'],
                date    = c['date'],
                message = c['message'])

    def create(self):
        result = requests.post(self.url)
        self.api_check(result)
        return self.slug

    def delete(self):
        self.api_check(requests.delete(self.url), [204],
               raises={404:RepositoryNotFound(self.full_name)})

    def rename(self, new_name):
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        self.check()

        # FIXME: this is required also on create
        result = requests.put(self.url, data={'name':new_name})
        self.api_check(result)
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

    # FIXME: refactor superclass
    def __init__(self, name, credentials=None):
        self.name = RepoName.cast(name, site='github')

        super().__init__(credentials)
        self.basic_data = dict(
            full_name = self.name.full_name,
            slug = self.name.slug)

        self.url = self.auth(self.BASE_URL.format(
            owner=self.name.owner.workspace, repo=self.name.slug))

    @classmethod
    def api_check(cls, reply, expected=None, raises=None):
        msg = _common_api_check(reply, expected, raises)
        if msg is None:
            return

        reply_json = reply.json()
        print(reply_json)

        if 'message' in reply_json:
            msg += '\n' + reply_json['message']
        if 'errors' in reply_json:
            msg += '\n' + reply_json['errors'][0]['message']

        raise RemoteError(msg)

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
        assert attr in 'scm slug full_name size access'.split(), "Missing attribute '{}'".format(attr)

        try:
            return self.basic_data[attr]
        except KeyError:
            return self.data[attr]

    # FIXME: refactor superclass
    @property
    @lru_cache()
    def data(self):
        logging.debug(self.url)
        result = requests.get(self.url)
        self.api_check(result)
        return result.json()

    @property
    @lru_cache()
    def clone_links(self):
        return dict(
            ssh = self.data['ssh_url'],
            https = self.data['clone_url']
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
        self.api_check(result, [200, 409])
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

    def create(self):
        url = self.auth(self.ORG_URL.format(org=self.name.owner.workspace))
        logging.debug(url)

        result = requests.post(
            url,
            data=json.dumps({'name':self.slug, 'private': True}))

        self.api_check(result, [201])
        real_name = result.json()['name']
        return real_name

    def delete(self):
        self.api_check(requests.delete(self.url), [204],
            raises={404:RepositoryNotFound(self.full_name)})

    def rename(self, new_name):
        # FIXME: github supports transfers
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        self.check()

        result = requests.patch(
            self.url,
            data=json.dumps({'name':new_name}))

        print(result.status_code)
        print(result.json())

        self.api_check(result)
        real_name = result.json()['name'].split('/')[-1]
        return real_name

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        logging.debug(url)
        git.Repo.clone_from(url, destdir, progress=dash)
        print()


class BitbucketWorkspace(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name, 'bitbucket')

        self.url = self.BASE_URL.format(name.workspace)

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            next_link = self.auth(next_link)
            result = requests.get(next_link)
            logging.debug(next_link)
            BitbucketRepo.api_check(result)

            page = result.json()
            next_link = page.get('next')
            for repo in page['values']:
                yield BitbucketRepo.from_data(repo, self.credentials)

    def check(self):
        BitbucketRepo.api_check(requests.get(self.auth(self.url)))


class GithubWorkspace(Auth):
    ORG_URL = 'https://api.github.com/orgs/{org}/repos'
    USER_URL = 'https://api.github.com/users/{user}/repos'

    def __init__(self, name, credentials):
        super().__init__(credentials)
        if not isinstance(name, WorkspaceName):
            name = WorkspaceName(name, 'github')

        self.url = self.ORG_URL.format(org=name.workspace)
        result = requests.get(self.auth(self.url))
        if result.status_code == 404:
            logging.info("'{}' is not an organization. Trying as user.".format(
                name.workspace))
            self.url = self.USER_URL.format(user=name.workspace)

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
            GithubRepo.api_check(result)

            page = result.json()
            next_link = get_next_link(result)

            for repo in page:
                yield GithubRepo.from_data(repo, self.credentials)

    def check(self):
        GithubRepo.api_check(requests.get(self.auth(self.url)))
