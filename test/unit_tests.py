import time
from unittest import TestCase
from pathlib import Path
from doublex import Stub, ANY_ARG

import ripio

with open('test/BITBUCKET_CREDENTIALS') as f:
    BITBUCKET_CREDENTIALS = f.read().strip()

with open('test/GITHUB_CREDENTIALS') as f:
    GITHUB_CREDENTIALS = f.read().strip()


class AuthTests(TestCase):
    def test_already_user(self):
        url = 'https://john.doe@bitbucket.org/repo-test/repo11.git'
        expected = 'https://john.doe:secret@bitbucket.org/repo-test/repo11.git'
        credentials = ripio.Credentials('john.doe:secret')
        sut = ripio.Auth(credentials)
        result = sut.auth(url)
        self.assertEquals(result, expected)


class BitbucketWorkspace(TestCase):
    def setUp(self):
        self.credentials = ripio.Credentials(BITBUCKET_CREDENTIALS)
        self.public_repos = ['repo{}'.format(x) for x in range(12)] + ['empty', 'ripio']
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
    public_repos = ['repo{}'.format(x) for x in range(32)] + ['ripio']

    def setUp(self):
        self.credentials = ripio.Credentials(GITHUB_CREDENTIALS)
        self.all_repos = self.public_repos + ['private', 'empty']
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
            name = ripio.RepoRef('bb:ripio-test/' + r)
            print(name, type(name), isinstance(name, ripio.RepoRef))
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


class RepoRef(TestCase):
    def test_http(self):
        sut = ripio.RepoRef('https://github.com/davidvilla/ripio')
        self.assertEquals(sut.global_name, 'github:davidvilla/ripio')

    def test_git(self):
        ref = ripio.RepoRef('git@github.com:davidvilla/ripio.git')
        self.assertEquals(str(ref), 'github:davidvilla/ripio')

    def test_ssh(self):
        ref = ripio.RepoRef('ssh://git@bitbucket.org/DavidVilla/prego3.git')
        self.assertEquals(str(ref), 'bitbucket:DavidVilla/prego3')

    def test_parse_origin_github_ssh(self):
        ref = ripio.RepoRef.from_origin('git@github.com:davidvilla/ripio.git')
        self.assertEquals(str(ref), 'github:davidvilla/ripio')

    def test_parse_origin_bitbucket_ssh(self):
        ref = ripio.RepoRef.from_origin('ssh://git@bitbucket.org/DavidVilla/prego3.git')
        self.assertEquals(str(ref), 'bitbucket:DavidVilla/prego3')


class Completion(TestCase):
    def setUp(self):
        with Stub() as self.config:
            self.config.get_workspaces(ANY_ARG).returns([])

    def test_no_workspaces_in_config(self):
        with self.assertRaises(ripio.ConfigError):
            ripio.Completion('repo0', self.config)

    def test_one_match(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['DavidVilla', 'ripio-test'])

        sut = ripio.Completion('repo0', self.config)
        self.assertEquals(sut.found, ['bitbucket:ripio-test/repo0'])

    def test_match_in_two_workspaces(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['DavidVilla', 'ripio-test'])

        sut = ripio.Completion('ripio', self.config)
        self.assertEquals(sut.found,
            ['bitbucket:DavidVilla/ripio', 'bitbucket:ripio-test/ripio'])

    def test_match_in_two_sites(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['ripio-test'])
            self.config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('ripio', self.config)
        self.assertEquals(sut.found,
            ['bitbucket:ripio-test/ripio', 'github:ripio-test/ripio'])

    def test_disambiguate_with_site(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['ripio-test'])
            self.config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('github:ripio', self.config)
        self.assertEquals(sut.found, ['github:ripio-test/ripio'])

    def test_disambiguate_with_abbreviated_site(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['ripio-test'])
            self.config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('gh:ripio', self.config)
        self.assertEquals(sut.found, ['github:ripio-test/ripio'])

    def test_no_matches(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['ripio-test'])

        with self.assertRaises(ripio.WrongCompletion):
            ripio.Completion('missing', self.config)

    def test_bitbucket_private_repo_without_credentials(self):
        with self.config:
            self.config.get_workspaces('bitbucket').returns(['ripio-test'])

        sut = ripio.Completion('private', self.config)
        self.assertEquals(sut.found, [])
        self.assertEquals(sut.denied, ['bitbucket:ripio-test/private'])

    def test_github_private_repo_without_credentials(self):
        with self.config as c:
            c.get_workspaces('github').returns(['ripio-test'])

        with self.assertRaises(ripio.WrongCompletion):
            sut = ripio.Completion('private', self.config)
            self.assertEquals(sut.found, [])
            self.assertEquals(sut.denied, ['github:ripio-test/private'])

    def test_public_repo_and_default_bitbucket_workspace(self):
        with self.config:
            self.config.get_credentials('bitbucket').returns(
                ripio.Credentials(BITBUCKET_CREDENTIALS))

        sut = ripio.Completion('ripio', self.config)
        self.assertEquals(sut.found, ['bitbucket:DavidVilla/ripio'])


class EmptyConfigFile(TestCase):
    # def test_empty(self):
    #     sut = ripio.ConfigFile('test/fixtures/empty.conf')
    #     self.assert_(sut.is_valid())
    def test_destdir(self):
        sut = ripio.ConfigFile()
        self.assertEquals(sut.destdir, Path.cwd())


class ConfigFile(TestCase):
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
        expected = ripio.RepoRef('bb:DavidVilla/ripio')
        result = ripio.RepoRef.from_origin(
            'git@bitbucket.org:DavidVilla/ripio.git')
        self.assertEquals(result, expected)

    def test_bitbucket_https(self):
        expected = ripio.RepoRef('bb:DavidVilla/ripio')
        result = ripio.RepoRef.from_origin(
            'https://bitbucket.org/DavidVilla/ripio')
        self.assertEquals(result, expected)

    def test_wrong_url(self):
        with self.assertRaises(ripio.BadRepositoryName):
            ripio.RepoRef.from_origin(
                'wrong://bitbucket.org/DavidVilla/ripio.git')


class Github_URL(TestCase):
    def test_github_ssh(self):
        expected = ripio.RepoRef('gh:davidvilla/python-doublex')
        result = ripio.RepoRef.from_origin(
            'git@github.com:davidvilla/python-doublex.git')
        self.assertEquals(result, expected)

    def test_github_https(self):
        expected = ripio.RepoRef('gh:davidvilla/python-doublex')
        result = ripio.RepoRef.from_origin(
            'https://github.com/davidvilla/python-doublex')
        self.assertEquals(result, expected)


# FIXME: test "cmd: ripio site"
