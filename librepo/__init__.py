import sys
import json
from urllib.parse import urlparse, urlunparse

import logging
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.WARNING)

import requests

class error(Exception): pass

class MissingRepo(error):
    def __str__(self):
        return "Repository '{}' do not exist".format(self.args[0])

class RemoteError(error):
    def __str__(self):
        return self.args[0]


def owner(full_name):
    return full_name.split(':')[0]

def slug(full_name):
    return full_name.split(':')[1]


def check_(reply, expected=200):
    if reply.status_code == expected:
        return

    if reply.status_code == 404:
        raise RemoteError('Not found')

    msg = "ERROR {}: {}".format(reply.status_code, reply.reason)
    if not reply.content:
        raise RemoteError(msg)

    error = reply.json()['error']
    msg += '\n' + error['message']
    if 'detail' in error:
        msg += '\n' + json.dumps(error['detail'], indent=2)
        raise RemoteError(msg)


class Credentials:
    def __init__(self, credentials):
        self.username, self.password = credentials.split(':')

    @classmethod
    def make(cls, credentials):
        if credentials is None:
            return None

        return Credentials(credentials)


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
        self.url = self.auth(self.BASE_URL.format(self.full_name))
        logging.debug(self.url)

    def last_commits(self, max_=3):
        commits_url = self.url + 'commits'
        result = requests.get(commits_url)
        check_(result)
        commits = json.loads(result.content)['values']
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
        result = requests.post(self.url)
        check_(result)

    def delete(self):
        result = requests.delete(self.url)
        check_(result, 204)


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
