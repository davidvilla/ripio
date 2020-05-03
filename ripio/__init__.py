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

class error(Exception):
    def __str__(self):
        msg = self.__class__.__name__
        if len(self.args):
            msg += ": {}".format(self.args[0])

        return msg

class RepositoryNotFound(error): pass

class RemoteError(error): pass

class ConfigError(error): pass

class MissingConfig(error): pass

class BadRepositoryName(error): pass

class RepoName:
    def __init__(self, full_name):
        if full_name.count('/') > 1:
            raise BadRepositoryName(full_name)

        self.full_name = full_name.strip().strip('/')

        try:
            self.owner, self.slug = self.full_name.split('/')
        except ValueError:
            raise BadRepositoryName(full_name)

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
                Repo(full_name).data  # FIXME: create mehod 'check'
                return full_name
            except RepositoryNotFound as e:
                logging.warning(e)

        raise RepositoryNotFound("in any known workspace '{}'".format(
            str.join(', ', workspaces)))

    def __str__(self):
        return self.full_name

def check_(reply, expected=200, raises=None):
    raises = raises or {}
    code = reply.status_code
    if code == expected:
        return

    if code in raises:
        raise raises[code]

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    if 'application/json' not in reply.headers.get('Content-Type'):
        msg += '\n' + reply.text
        raise RemoteError(msg)

    error = reply.json()['error']
    msg += '\n' + error['message']
    if 'detail' in error:
        msg += '\n' + json.dumps(error['detail'], indent=2)

    raise RemoteError(msg)


class Config:
    def __init__(self, fname):
        self.fname = fname
        self.toml = toml.load(fname)
        self.data = utils.dictToObject(self.toml)

    def __getattr__(self, key):
        return getattr(self.data, key)

    # FIXME: bitubcket supersedes that
    @property
    def credentials(self):
        try:
            return Credentials(self.bitbucket.credentials.default)
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


class RepoData:
    def __init__(self, data):
        self.scm = data['scm']
        self.slug = data['slug']
        self.full_name = data['full_name']
        self.size = data['size']
        self.access = 'private' if data['is_private'] else 'public'


class Repo(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}/'

    def __init__(self, full_name, credentials=None):
        if not isinstance(full_name, RepoName):
            full_name = RepoName(full_name)

        super().__init__(credentials)
        self.full_name = full_name
        self.slug = full_name.slug
        self.url = self.auth(self.BASE_URL.format(self.full_name))
        logging.debug(self.url)

    @property
    @lru_cache
    def data(self):
        result = requests.get(self.url)
        if result.status_code == 404:
            raise RepositoryNotFound(self.full_name)

        check_(result)
        return result.json()

    @property
    @lru_cache
    def clone_links(self):
        '''"clone": [
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

    def last_commits(self, max_=3):
        commits_url = self.url + 'commits/'
        result = requests.get(commits_url)
        check_(result)
        commits = result.json()['values']
        return commits[:3]

    def rename(self, new_name):
        if '/' in new_name:
            raise error('New name must have no workspace, transfer is not supported')

        result = requests.get(self.url)
        if result.status_code == 404:
            raise RepositoryNotFound(self.full_name)

        result = requests.put(self.url, data={'name':new_name})
        check_(result)
        real_name = result.headers['Location'].split('/')[-1]
        return real_name

    def create(self):
        check_(requests.post(self.url))

    def delete(self):
        check_(requests.delete(self.url), 204,
               raises={404:RepositoryNotFound(self.full_name)})

    def clone(self, destdir, proto='ssh'):
        def dash(*data):
            print('-', end='', flush=True)

        url = self.clone_links[proto]
        logging.debug(url)
        git.Repo.clone_from(url, destdir, progress=dash)
        print()

class Workspace(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, workspace, credentials):
        super().__init__(credentials)
        self.url = self.BASE_URL.format(workspace)

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            result = requests.get(self.auth(next_link))
            logging.debug(next_link)
            check_(result)

            page = result.json()
            next_link = page.get('next')
            for repo in page['values']:
                yield RepoData(repo)

    def check(self):
        check_(requests.get(self.auth(self.url)))
