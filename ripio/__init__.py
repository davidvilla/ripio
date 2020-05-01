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


class error(Exception): pass

class MissingRepo(error):
    def __str__(self):
        return "Repository '{}' do not exist".format(self.args[0])

class RemoteError(error):
    def __str__(self):
        return self.args[0]

class MissingConfig(error): pass


def owner(full_name):
    return full_name.split('/')[0]

def slug(full_name):
    return full_name.split('/')[1]


def check_(reply, expected=200):
    if reply.status_code == expected:
        return

    if reply.status_code == 404:
        raise RemoteError('Not found')

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    if reply.headers.get('Content-Type') != 'application/json':
        raise RemoteError(reply.text)

    error = reply.json()['error']
    msg += '\n' + error['message']
    if 'detail' in error:
        msg += '\n' + json.dumps(error['detail'], indent=2)
        raise RemoteError(msg)


class Config:
    def __init__(self, fname):
        self.fname = fname
        self.toml = toml.load(fname)

    def credentials(self):
        try:
            return Credentials(self.toml['bitbucket']['credentials']['default'])
        except KeyError:
            raise MissingConfig

    def destdir(self):
        try:
            return Path(self.toml['clone']['destdir']).expanduser()
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
        super().__init__(credentials)
        self.full_name = full_name
        self.slug = slug(full_name)
        self.url = self.auth(self.BASE_URL.format(self.full_name))
        logging.debug(self.url)

    @property
    @lru_cache
    def data(self):
        result = requests.get(self.url)
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
        result = requests.get(self.url)
        if result.status_code == 404:
            raise MissingRepo(self.full_name)

        result = requests.put(self.url, data={'name':new_name})
        check_(result)
        real_name = result.headers['Location'].split('/')[-1]
        return real_name

    def create(self):
        check_(requests.post(self.url))

    def delete(self):
        check_(requests.delete(self.url), 204)

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
