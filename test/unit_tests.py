import time
from argparse import Namespace
from unittest import TestCase

import ripio

with open('test/BITBUCKET_CREDENTIALS') as f:
    BITBUCKET_CREDENTIALS = f.read()

with open('test/GITHUB_CREDENTIALS') as f:
    GITHUB_CREDENTIALS = f.read()


class BitbucketWorkspaceTests(TestCase):
    def test_ls_public(self):
        sut = ripio.BitbucketWorkspace('ripio-test', None)
        public_repos = ['repo{}'.format(x) for x in range(12)]
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(public_repos))

    def test_ls_all(self):
        cred = ripio.Credentials(BITBUCKET_CREDENTIALS)
        sut = ripio.BitbucketWorkspace('ripio-test', cred)
        expected = ['repo{}'.format(x) for x in range(12)] + ['private.hg']
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(expected))

    def test_ls_missing_workspace(self):
        cred = ripio.Credentials(BITBUCKET_CREDENTIALS)
        ws = ripio.BitbucketWorkspace('ripio-missing-work-space', cred)

        with self.assertRaises(ripio.RemoteError) as e:
            ws.check()
            self.assertEquals('ripio-missing-work-space', str(e))


class GithubWorkspaceTest(TestCase):
    def test_ls_public(self):
        sut = ripio.GithubWorkspace('github:ripio-test', None)
        public_repos = ['repo{}'.format(x) for x in range(32)]
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(public_repos))


class BitbucketRepoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        creds = ripio.Credentials(BITBUCKET_CREDENTIALS)
        for r in ['removable to-delete']:
            name = 'ripio-test/{}'.format(r)
            try:
                ripio.BitbucketRepo(name, creds).delete()
            except ripio.RepositoryNotFound:
                pass

    def setUp(self):
        self.credentials = ripio.Credentials(BITBUCKET_CREDENTIALS)

    def test_head(self):
        repo = ripio.BitbucketRepo('ripio-test/repo0')
        result = list(repo.last_commits())[0]['message']
        self.assertIn('last-commit-message', result)

    def test_create_delete(self):
        repo = ripio.BitbucketRepo('ripio-test/removable', self.credentials)
        repo.create()
        repo.delete()
        time.sleep(1)

    def test_create_rename_delete(self):
        repo = ripio.BitbucketRepo('ripio-test/removable', self.credentials)
        repo.create()
        repo.rename('to-delete')

        repo = ripio.BitbucketRepo('ripio-test/to-delete', self.credentials)
        repo.delete()
        time.sleep(1)

    def test_delete_missing(self):
        repo = ripio.BitbucketRepo('ripio-test/missing', self.credentials)
        with self.assertRaises(ripio.RepositoryNotFound):
            repo.delete()

class GithubRepoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        creds = ripio.Credentials(GITHUB_CREDENTIALS)
        for r in ['removable to-delete']:
            name = 'ripio-test/{}'.format(r)
            try:
                ripio.GithubRepo(name, creds).delete()
            except ripio.RepositoryNotFound:
                pass

    def setUp(self):
        self.credentials = ripio.Credentials(GITHUB_CREDENTIALS)

    def test_head(self):
        repo = ripio.GithubRepo('ripio-test/repo0')
        result = list(repo.last_commits())[0]['message']
        self.assertIn('last-commit-message', result)

    def test_head_empty_repo(self):
        repo = ripio.GithubRepo('ripio-test/empty', self.credentials)
        result = list(repo.last_commits())
        self.assertEquals(result, [])

    def test_create_delete(self):
        repo = ripio.GithubRepo('ripio-test/removable', self.credentials)
        repo.create()
        repo.delete()
        time.sleep(1)

    def test_create_rename_delete(self):
        repo = ripio.GithubRepo('ripio-test/removable', self.credentials)
        repo.create()
        repo.rename('to-delete')

        repo = ripio.GithubRepo('ripio-test/to-delete', self.credentials)
        repo.delete()
        time.sleep(1)

    def test_delete_missing(self):
        repo = ripio.GithubRepo('ripio-test/missing', self.credentials)
        with self.assertRaises(ripio.RepositoryNotFound):
            repo.delete()



class CompleterTests(TestCase):
    # def test_fullname(self):
    #     name = ripio.RepoName.complete('ripio-test/repo0', None)
    #     self.assertEquals(name, 'ripio-test/repo0')

    def test_slug(self):
        ns = Namespace()
        ns.bitbucket = Namespace(workspaces=['DavidVilla', 'ripio-test'])
        name = ripio.RepoName.complete('repo0', ns)
        self.assertEquals(name, 'ripio-test/repo0')

    def test_no_workspaces_in_config(self):
        ns = Namespace()
        with self.assertRaises(ripio.ConfigError):
            ripio.RepoName.complete('repo0', ns)


class ConfigTests(TestCase):
    def test_empty(self):
        sut = ripio.Config('test/fixtures/empty.conf')
        self.assert_(sut.is_valid())

    def test_bitbucket_credentials(self):
        sut = ripio.Config('test/fixtures/bitbucket.conf')
        result = sut.get_credentials('bitbucket')
        self.assertEquals(result, ripio.Credentials('john.doe:secret'))

    def test_username_included_as_workspace_by_default(self):
        sut = ripio.Config('test/fixtures/bitbucket.conf')
        result = sut.bitbucket.workspaces
        expected = set(['ripio-test', 'DavidVilla'])
        self.assertEquals(set(result), expected)


class Bitbucket_URL_Tests(TestCase):
    def test_bitbucket_ssh(self):
        result = ripio.origin_to_fullname(
            'git@bitbucket.org:DavidVilla/ripio.git')
        self.assertEquals(result, 'DavidVilla/ripio')

    def test_bitbucket_https(self):
        result = ripio.origin_to_fullname(
            'https://bitbucket.org/DavidVilla/ripio.git')
        self.assertEquals(result, 'DavidVilla/ripio')


class Github_URL_Tests(TestCase):
    def test_github_ssh(self):
        result = ripio.origin_to_fullname(
            'git@ithub.com:davidvilla/python-doublex.git')
        self.assertEquals(result, 'davidvilla/python-doublex')

    def test_github_https(self):
        result = ripio.origin_to_fullname(
            'https://github.com/davidvilla/python-doublex.git')
        self.assertEquals(result, 'davidvilla/python-doublex')