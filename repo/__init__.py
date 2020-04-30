
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

    print("-- ERROR --")
    error = json.loads(reply.content)['error']
    print(error['message'])
    if 'detail' in error:
        print(json.dumps(error['detail'], ident=2))


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
        parts._replace('netloc', user_pass + parts.netloc)
        return urlunparse(parts)


class RepoData:
    def __init__(self, data):
        self.scm = data['scm']
        self.full_name = data['full_name']
        self.size = data['size']
        self.access = 'private' if data['is_private'] else 'public'


class Repo(Auth):
    pass


class Workspace(Auth):
    BASE_URL = 'https://api.bitbucket.org/2.0/repositories/{}?sort=slug'

    def __init__(self, workspace, credentials):
        super().__init__(credentials)
        self.url = self.auth(self.BASE_URL.format(workspace))

    def ls_repos(self):
        next_link = self.url
        while next_link is not None:
            result = requests.get(next_link)
            check_(result)
            page = json.loads(result.content)
            for repo in page['values']:
                yield RepoData(repo)
                next_link = page.get('next')
