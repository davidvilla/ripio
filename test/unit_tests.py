import time
from argparse import Namespace
from unittest import TestCase

import ripio

with open('test/BITBUCKET_CREDENTIALS') as f:
    BITBUCKET_CREDENTIALS = f.read().strip()

with open('test/GITHUB_CREDENTIALS') as f:
    GITHUB_CREDENTIALS = f.read().strip()


class BitbucketWorkspace(TestCase):
    def setUp(self):
        self.credentials = ripio.Credentials(BITBUCKET_CREDENTIALS)
        self.public_repos = ['repo{}'.format(x) for x in range(12)] + ['empty']
        self.all_repos = self.public_repos + ['empty', 'private']
        self.prefix = 'bitbucket:'
        self.abbreviated_prefix = 'bb:'

    def make_workspace(self, name, auth=True):
        creds = self.credentials if auth else None
        return ripio.BitbucketWorkspace(name, creds)

    def test_ls_public(self):
        sut = self.make_workspace('ripio-test', False)
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(self.public_repos))

    def test_ls_all(self):
        sut = self.make_workspace('ripio-test')
        result = sut.ls_repos()
        names = [x.slug for x in result]
        self.assertSetEqual(set(names), set(self.all_repos))

    def test_ls_missing_workspace(self):
        ws = self.make_workspace('ripio-missing-work-space')

        with self.assertRaises(ripio.RemoteError) as e:
            ws.check()
            self.assertEquals('ripio-missing-work-space', str(e))

    def test_not_supported_site(self):
        with self.assertRaises(ripio.UnsupportedSite):
            self.make_workspace('not-supported-site:name')

    def test_redundant_site(self):
        self.make_workspace(self.prefix + 'ripio-test')

    def test_abbreviated_site(self):
        self.make_workspace(self.abbreviated_prefix + 'ripio-test')


class GithubWorkspace(BitbucketWorkspace):
    public_repos = ['repo{}'.format(x) for x in range(32)]

    def setUp(self):
        self.credentials = ripio.Credentials(GITHUB_CREDENTIALS)
        self.all_repos = self.public_repos  + ['private', 'empty']
        self.prefix = 'github:'
        self.abbreviated_prefix = 'gh:'

    def make_workspace(self, name, auth=True):
        creds = self.credentials if auth else None
        return ripio.GithubWorkspace(name, creds)


# class GithubWorkspaceUser(TestCase):
#     def make_workspace(self, name, auth=True):
#         creds = self.credentials if auth else None
#         return ripio.GithubWorkspace(name, creds)

#     def test_ls_public(self):
#         sut = self.make_workspace('davidvilla', auth=False)
#         result = sut.ls_repos()
#         names = [x.slug for x in result]
#         self.assertSetEqual(set(names), set(GithubWorkspace.public_repos))


class BitbucketRepo(TestCase):
    def setUp(self):
        self.remove_fixtures()

    @classmethod
    def make_repo(cls, name, auth=True):
        creds = ripio.Credentials(BITBUCKET_CREDENTIALS) if auth else None
        print(creds)
        return ripio.BitbucketRepo(name, creds)

    @classmethod
    def remove_fixtures(cls):
        i = 0
        for r in ['removable', 'to-delete']:
            name = 'ripio-test/' + r
            try:
                cls.make_repo(name).delete()
                i += 1
            except ripio.RepositoryNotFound:
                pass

        if i:
            time.sleep(1)

    def test_head(self):
        repo = self.make_repo('ripio-test/repo0')
        result = list(repo.last_commits())[0]['message']
        self.assertIn('last-commit-message', result)

    def test_head_empty_repo(self):
        repo = self.make_repo('ripio-test/empty')
        result = list(repo.last_commits())
        self.assertEquals(result, [])

    def test_create(self):
        repo = self.make_repo('ripio-test/removable')
        name = repo.create()
        self.assertEquals(name, 'removable')

    def test_create_rename(self):
        repo = self.make_repo('ripio-test/removable')
        repo.create()
        name = repo.rename('to-delete')
        self.assertEquals(name, 'to-delete')

    def test_delete_missing(self):
        repo = self.make_repo('ripio-test/missing')
        with self.assertRaises(ripio.RepositoryNotFound):
            repo.delete()


class GithubRepo(BitbucketRepo):
    def setUp(self):
        self.remove_fixtures()

    @classmethod
    def make_repo(cls, name, auth=True):
        creds = ripio.Credentials(GITHUB_CREDENTIALS) if auth else None
        return ripio.GithubRepo(name, creds)


class GithubUser(TestCase):
    @classmethod
    def make_repo(cls, name, auth=True):
        creds = ripio.Credentials(GITHUB_CREDENTIALS) if auth else None
        return ripio.GithubRepo(name, creds)

    def test_head(self):
        repo = self.make_repo('davidvilla/ripio-dummy')
        result = list(repo.last_commits())[0]['message']
        self.assertIn('last-commit-message', result)

    # def test_create_user_repo(self):
    #     repo = self.make_repo('davidvilla/removable')
    #     name = repo.create()
    #     self.assertEquals(name, 'removable')


class Completer(TestCase):
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


class Config(TestCase):
    def test_empty(self):
        sut = ripio.ConfigFile('test/fixtures/empty.conf')
        self.assert_(sut.is_valid())

    def test_bitbucket_credentials(self):
        sut = ripio.ConfigFile('test/fixtures/bitbucket.conf')
        result = sut.get_credentials('bitbucket')
        self.assertEquals(result, ripio.Credentials('john.doe:secret'))

    def test_username_included_as_workspace_by_default(self):
        sut = ripio.ConfigFile('test/fixtures/bitbucket.conf')
        result = sut.bitbucket.workspaces
        expected = set(['ripio-test', 'DavidVilla'])
        self.assertEquals(set(result), expected)


class Bitbucket_URL(TestCase):
    def test_bitbucket_ssh(self):
        result = ripio.origin_to_fullname(
            'git@bitbucket.org:DavidVilla/ripio.git')
        self.assertEquals(result, 'DavidVilla/ripio')

    def test_bitbucket_https(self):
        result = ripio.origin_to_fullname(
            'https://bitbucket.org/DavidVilla/ripio.git')
        self.assertEquals(result, 'DavidVilla/ripio')


class Github_URL(TestCase):
    def test_github_ssh(self):
        result = ripio.origin_to_fullname(
            'git@ithub.com:davidvilla/python-doublex.git')
        self.assertEquals(result, 'davidvilla/python-doublex')

    def test_github_https(self):
        result = ripio.origin_to_fullname(
            'https://github.com/davidvilla/python-doublex.git')
        self.assertEquals(result, 'davidvilla/python-doublex')
