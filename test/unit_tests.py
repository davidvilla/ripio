import time
from unittest import TestCase

import ripio

with open('test/CREDENTIALS') as f:
    CREDENTIALS = f.read()

class WorkspaceTests(TestCase):
    def test_ls_public(self):
        sut = ripio.Workspace('ripio-test', None)
        public_repos = ['repo{}'.format(x) for x in range(12)]
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(public_repos))

    def test_ls_all(self):
        cred = ripio.Credentials(CREDENTIALS)
        sut = ripio.Workspace('ripio-test', cred)
        expected = ['repo{}'.format(x) for x in range(12)] + ['private.hg']
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(expected))

    def test_head(self):
        repo = ripio.Repo('ripio-test/repo0')
        result = repo.last_commits()[0]['message']
        self.assertIn('last-commit-message', result)

    def test_create_delete(self):
        repo = ripio.Repo('ripio-test/removable', ripio.Credentials(CREDENTIALS))
        repo.create()
        repo.delete()
        time.sleep(1)

    def test_create_rename_delete(self):
        repo = ripio.Repo('ripio-test/removable', ripio.Credentials(CREDENTIALS))
        repo.create()
        repo.rename(ripio.RepoName('to-delete'))

        repo = ripio.Repo('ripio-test/to-delete', ripio.Credentials(CREDENTIALS))
        repo.delete()
        time.sleep(1)

class ConfigTests(TestCase):
    def test_empty(self):
        sut = ripio.Config('test/fixtures/empty.conf')
        self.assert_(sut.is_valid())

    def test_bitbucket_credentials(self):
        sut = ripio.Config('test/fixtures/bitbucket.conf')
        result = sut.credentials()
        self.assertEquals(result, ripio.Credentials('john.doe:secret'))
