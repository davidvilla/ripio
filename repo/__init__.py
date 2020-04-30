import sys
import json
from urllib.parse import urlparse, urlunparse

import requests


def owner(full_name):
    return full_name.split(':')[0]

def slug(full_name):
    return full_name.split(':')[1]



def check_(reply, expected=200):
    if reply.status_code == expected:
        return

    if reply.status_code == 404:
        print("Not found")
        sys.exit(1)

    print("-- ERROR --")
    error = reply.json()['error']
    print(error['message'])
    if 'detail' in error:
        print(error['detail'])

    sys.exit(1)


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
        self.full_name = data['full_name']
        self.size = data['size']
        self.access = 'private' if data['is_private'] else 'public'


class Repo(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}/'

    def __init__(self, full_name, credentials):
        super().__init__(credentials)
        self.full_name = full_name

    def last_commits(self, max_=3):
        commits_url = self.auth(self.BASE_URL.format(self.full_name) + 'commits')
        print(commits_url)
        result = requests.get(commits_url)
        check_(result)
        commits = json.loads(result.content)['values']
        return commits[:3]


class Workspace(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, workspace, credentials):
        super().__init__(credentials)
        self.url = self.BASE_URL.format(workspace)

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            result = requests.get(self.auth(next_link))
            check_(result)
            page = json.loads(result.content)
            next_link = page.get('next')
            for repo in page['values']:
                yield RepoData(repo)
