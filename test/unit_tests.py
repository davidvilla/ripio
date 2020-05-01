from unittest import TestCase

from ripio import Workspace, Repo, Credentials, Config

with open('test/CREDENTIALS') as f:
    CREDENTIALS = f.read()

class WorkspaceTests(TestCase):
    def test_ls_public(self):
        sut = Workspace('ripio-test', None)
        public_repos = ['repo{}'.format(x) for x in range(12)]
        result = sut.ls_repos()
        names = [x.slug for x in result]
        print(names)
        self.assertSetEqual(set(names), set(public_repos))

    def test_ls_all(self):
        cred = Credentials(CREDENTIALS)
        sut = Workspace('ripio-test', cred)
        expected = ['repo{}'.format(x) for x in range(12)] + ['private.hg']
        result = sut.ls_repos()
        names = [x.slug for x in result]
        print(names)
        self.assertSetEqual(set(names), set(expected))

    def test_head(self):
        repo = Repo('ripio-test/repo0')
        result = repo.last_commits()[0]['message']
        self.assertIn('last-commit-message', result)

    def test_create_delete(self):
        repo = Repo('ripio-test/removable', Credentials(CREDENTIALS))
        repo.create()
        repo.delete()

    def test_create_rename_delete(self):
        repo = Repo('ripio-test/removable', Credentials(CREDENTIALS))
        repo.create()
        repo.rename('to-delete')

        repo = Repo('ripio-test/to-delete', Credentials(CREDENTIALS))
        repo.delete()

class ConfigTests(TestCase):
    def test_empty(self):
        sut = Config('test/fixtures/empty.conf')
        self.assert_(sut.is_valid())

    def test_bitbucket_credentials(self):
        sut = Config('test/fixtures/bitbucket.conf')
        result = sut.credentials()
        self.assertEquals(result, 'john.doe:secret')
