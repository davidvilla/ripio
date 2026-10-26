import os
import time
import random
import subprocess
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime

import pytest
from doublex import Stub, ANY_ARG

import ripio

TEST_DIR = Path(__file__).parent
FIXTURES = TEST_DIR / 'fixtures'


BITBUCKET = SimpleNamespace(
    name='bitbucket',
    abbrev='bb',
    workspace_class=ripio.BitbucketWorkspace,
    repo_class=ripio.BitbucketRepo,
    public_repos={'repo{}'.format(x) for x in range(12)} | {'empty', 'ripio'},
    private_repos={'private'},
    rename_delay=0)

GITHUB = SimpleNamespace(
    name='github',
    abbrev='gh',
    workspace_class=ripio.GithubWorkspace,
    repo_class=ripio.GithubRepo,
    public_repos={'repo{}'.format(x) for x in range(32)} | {'ripio'},
    private_repos={'private', 'empty'},
    # Github needs some time after creation before it allows a rename
    rename_delay=3)


@pytest.fixture(params=[BITBUCKET, GITHUB], ids=lambda site: site.name)
def site(request):
    return request.param


@pytest.fixture(scope='session')
def credentials():
    "Credentials for a site, from 'test/<SITE>_CREDENTIALS'; skips the test when missing"
    def get(site):
        path = TEST_DIR / '{}_CREDENTIALS'.format(site.upper())
        if not path.exists():
            pytest.skip("missing '{}'".format(path))

        return ripio.Credentials(path.read_text().strip())

    return get


@pytest.fixture
def make_workspace(site, credentials):
    def make(name, auth=True):
        return site.workspace_class(name, credentials(site.name) if auth else None)

    return make


@pytest.fixture
def make_repo(site, credentials):
    def make(name, auth=True):
        return site.repo_class(name, credentials(site.name) if auth else None)

    return make


class TestAuth:
    def test_already_user(self):
        url = 'https://john.doe@bitbucket.org/repo-test/repo11.git'
        expected = 'https://john.doe:secret@bitbucket.org/repo-test/repo11.git'
        credentials = ripio.Credentials('john.doe:secret')
        sut = ripio.Auth(credentials)
        assert sut.auth(url) == expected


class TestWorkspace:
    @pytest.mark.network
    def test_ls_public(self, site, make_workspace):
        sut = make_workspace('ripio-test', auth=False)
        names = {x.slug for x in sut.ls_repos()}
        assert names == site.public_repos

    @pytest.mark.network
    def test_ls_all(self, site, make_workspace):
        sut = make_workspace('ripio-test')
        names = {x.slug for x in sut.ls_repos()}
        assert names == site.public_repos | site.private_repos

    @pytest.mark.network
    def test_ls_missing_workspace(self, make_workspace):
        ws = make_workspace('ripio-missing-work-space')
        with pytest.raises(ripio.RemoteError):
            ws.check()

    def test_not_supported_site(self, make_workspace):
        with pytest.raises(ripio.UnsupportedSite):
            make_workspace('not-supported-site:name', auth=False)

    @pytest.mark.network
    def test_redundant_site(self, site, make_workspace):
        make_workspace(site.name + ':ripio-test')

    @pytest.mark.network
    def test_abbreviated_site(self, site, make_workspace):
        make_workspace(site.abbrev + ':ripio-test')


# class TestGithubWorkspaceUser:
#     def test_ls_public(self):
#         sut = ripio.GithubWorkspace('davidvilla', None)
#         names = {x.slug for x in sut.ls_repos()}
#         assert names == GITHUB.public_repos


def remove_repos(make_repo, names):
    # best-effort cleanup: a repo just renamed away from one of these
    # names can make the remote site report a transient conflict here
    # instead of a clean "not found" for the old name, so retry a couple
    # of times before giving up on that name
    i = 0
    attempts = 6
    for name in names:
        for attempt in range(attempts):
            try:
                make_repo('ripio-test/' + name).delete()
                i += 1
                break
            except ripio.RepositoryNotFound:
                break
            except ripio.error:
                if attempt == attempts - 1:
                    break
                time.sleep(2)

    if i:
        time.sleep(1)


@pytest.fixture
def new_names(make_repo):
    # a fresh name per test, so create()/rename() never touch a name
    # that was just deleted while the remote site still enforces its
    # name-reuse cooldown
    suffix = random.randint(100000, 999999)
    names = SimpleNamespace(
        removable='removable-{}'.format(suffix),
        to_delete='to-delete-{}'.format(suffix))

    yield names

    # random names are never reused, so nothing else will ever clean these up
    remove_repos(make_repo, [names.removable, names.to_delete])


@pytest.mark.network
class TestRepo:
    def test_head(self, make_repo):
        repo = make_repo('ripio-test/repo0')
        result = list(repo.last_commits())[0]['message']
        assert 'last-commit-message' in result

    def test_head_empty_repo(self, make_repo):
        repo = make_repo('ripio-test/empty')
        assert list(repo.last_commits()) == []

    def test_create(self, make_repo, new_names):
        repo = make_repo('ripio-test/' + new_names.removable)
        assert repo.create() == new_names.removable

    def test_create_rename(self, site, make_repo, new_names):
        repo = make_repo('ripio-test/' + new_names.removable)
        repo.create()
        time.sleep(site.rename_delay)
        assert repo.rename(new_names.to_delete) == new_names.to_delete

    def test_delete_missing(self, make_repo):
        repo = make_repo('ripio-test/missing')
        with pytest.raises(ripio.RepositoryNotFound):
            repo.delete()


@pytest.mark.network
class TestGithubUser:
    def test_head(self, credentials):
        repo = ripio.GithubRepo('davidvilla/ripio-dummy', credentials('github'))
        result = list(repo.last_commits())[0]['message']
        assert 'last-commit-message' in result

    # def test_create_user_repo(self, credentials):
    #     repo = ripio.GithubRepo('davidvilla/removable', credentials('github'))
    #     assert repo.create() == 'removable'


class TestRepoRef:
    @pytest.mark.parametrize('name, expected', [
        pytest.param('https://github.com/davidvilla/ripio', 'github:davidvilla/ripio', id='http'),
        pytest.param('git@github.com:davidvilla/ripio.git', 'github:davidvilla/ripio', id='git'),
        pytest.param('ssh://git@bitbucket.org/DavidVilla/prego3.git', 'bitbucket:DavidVilla/prego3',
                     id='ssh'),
        pytest.param('github:davidvilla/ripio', 'github:davidvilla/ripio', id='colon-form'),
        pytest.param('github/davidvilla/ripio', 'github:davidvilla/ripio', id='slash-form'),
        pytest.param('gh/davidvilla/ripio', 'github:davidvilla/ripio',
                     id='slash-form-abbreviated-site'),
    ])
    def test_global_name(self, name, expected):
        assert str(ripio.RepoRef(name)) == expected

    def test_slash_form_equals_colon_form(self):
        assert ripio.RepoRef('bitbucket/DavidVilla/prego3') == \
            ripio.RepoRef('bitbucket:DavidVilla/prego3')

    @pytest.mark.parametrize('origin, expected', [
        pytest.param('git@github.com:davidvilla/ripio.git', 'github:davidvilla/ripio',
                     id='github-git'),
        pytest.param('https://github.com/davidvilla/python-doublex', 'gh:davidvilla/python-doublex',
                     id='github-https'),
        pytest.param('ssh://git@bitbucket.org/DavidVilla/prego3.git', 'bitbucket:DavidVilla/prego3',
                     id='bitbucket-ssh'),
        pytest.param('git@bitbucket.org:DavidVilla/ripio.git', 'bb:DavidVilla/ripio',
                     id='bitbucket-git'),
        pytest.param('https://bitbucket.org/DavidVilla/ripio', 'bb:DavidVilla/ripio',
                     id='bitbucket-https'),
    ])
    def test_from_origin(self, origin, expected):
        assert ripio.RepoRef.from_origin(origin) == ripio.RepoRef(expected)

    def test_wrong_url(self):
        with pytest.raises(ripio.BadRepositoryName):
            ripio.RepoRef.from_origin('wrong://bitbucket.org/DavidVilla/ripio.git')


@pytest.fixture
def config():
    with Stub() as config:
        config.get_workspaces(ANY_ARG).returns([])

    return config


class TestCompletion:
    def test_no_workspaces_in_config(self, config):
        with pytest.raises(ripio.ConfigError):
            ripio.Completion('repo0', config)

    @pytest.mark.network
    def test_one_match(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['DavidVilla', 'ripio-test'])

        sut = ripio.Completion('repo0', config)
        assert sut.found == ['bitbucket:ripio-test/repo0']

    @pytest.mark.network
    def test_match_in_two_workspaces(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['DavidVilla', 'ripio-test'])

        sut = ripio.Completion('ripio', config)
        assert sut.found == ['bitbucket:DavidVilla/ripio', 'bitbucket:ripio-test/ripio']

    @pytest.mark.network
    def test_match_in_two_sites(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['ripio-test'])
            config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('ripio', config)
        assert sut.found == ['bitbucket:ripio-test/ripio', 'github:ripio-test/ripio']

    @pytest.mark.network
    def test_disambiguate_with_site(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['ripio-test'])
            config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('github:ripio', config)
        assert sut.found == ['github:ripio-test/ripio']

    @pytest.mark.network
    def test_disambiguate_with_abbreviated_site(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['ripio-test'])
            config.get_workspaces('github').returns(['ripio-test'])

        sut = ripio.Completion('gh:ripio', config)
        assert sut.found == ['github:ripio-test/ripio']

    @pytest.mark.network
    def test_no_matches(self, config):
        with config:
            config.get_workspaces('bitbucket').returns(['ripio-test'])

        with pytest.raises(ripio.WrongCompletion):
            ripio.Completion('missing', config)

    @pytest.mark.network
    def test_bitbucket_private_repo_without_credentials(self, config):
        # Bitbucket returns 404 (not 403) for private repos when unauthenticated,
        # so it is indistinguishable from a missing repo.
        with config:
            config.get_workspaces('bitbucket').returns(['ripio-test'])

        with pytest.raises(ripio.WrongCompletion):
            ripio.Completion('private', config)

    @pytest.mark.network
    def test_github_private_repo_without_credentials(self, config):
        with config:
            config.get_workspaces('github').returns(['ripio-test'])

        with pytest.raises(ripio.WrongCompletion):
            ripio.Completion('private', config)

    @pytest.mark.network
    def test_public_repo_and_default_bitbucket_workspace(self, config, credentials):
        # Bitbucket app passwords authenticate with an email as username, which
        # no longer matches the account's workspace slug, so the workspace must
        # be given explicitly instead of guessed from the credentials' username.
        with config:
            config.get_credentials('bitbucket').returns(credentials('bitbucket'))
            config.get_workspaces('bitbucket').returns(['DavidVilla'])

        sut = ripio.Completion('ripio', config)
        assert sut.found == ['bitbucket:DavidVilla/ripio']


class FakeRef:
    def __init__(self, global_name):
        self.site, _ = global_name.split(':')
        self.global_name = global_name


class FakeRepo:
    def __init__(self, global_name, uid, canonical_name):
        self.ref = FakeRef(global_name)
        self.uid = uid
        self.canonical_name = canonical_name

    def checked_exists(self):
        return True


class FakeWorkspace:
    def __init__(self, site, repo):
        self.site = site
        self._repo = repo

    def make_repo(self, name):
        return self._repo


class TestCompletionDedup:
    # a repo renamed or transferred to another owner on GitHub/Bitbucket keeps
    # answering on its old URL, so a bare-name guess can match it under both
    # the stale and the current owner/slug; Completion must collapse those
    # into a single, current entry instead of reporting a false ambiguity.
    def make_completion(self, workspaces):
        sut = ripio.Completion.__new__(ripio.Completion)
        sut.found = []
        sut.denied = []
        sut.workspaces = workspaces
        sut.complete('myrepo', config=None)
        return sut

    def test_renamed_or_transferred_repo_is_listed_once_under_its_current_name(self):
        stale = FakeRepo('github:oldorg/myrepo', uid=42, canonical_name='github:neworg/myrepo')
        current = FakeRepo('github:neworg/myrepo', uid=42, canonical_name='github:neworg/myrepo')

        sut = self.make_completion([
            FakeWorkspace('github', stale),
            FakeWorkspace('github', current)])

        assert sut.found == ['github:neworg/myrepo']

    def test_different_repos_sharing_a_slug_are_not_merged(self):
        a = FakeRepo('github:orgA/myrepo', uid=1, canonical_name='github:orgA/myrepo')
        b = FakeRepo('github:orgB/myrepo', uid=2, canonical_name='github:orgB/myrepo')

        sut = self.make_completion([
            FakeWorkspace('github', a),
            FakeWorkspace('github', b)])

        assert sut.found == ['github:orgA/myrepo', 'github:orgB/myrepo']


class TestConfigFile:
    # def test_empty(self):
    #     sut = ripio.ConfigFile(FIXTURES / 'empty.conf')
    #     assert sut.is_valid()

    def test_empty_destdir(self):
        sut = ripio.ConfigFile()
        assert sut.destdir == Path.cwd()

    def test_bitbucket_credentials(self):
        sut = ripio.ConfigFile(FIXTURES / 'bitbucket.conf')
        result = sut.get_credentials('bitbucket')
        assert result == ripio.Credentials('john.doe:secret')

    def test_username_included_as_workspace_by_default(self):
        sut = ripio.ConfigFile(FIXTURES / 'bitbucket.conf')
        assert set(sut.bitbucket.workspaces) == {'ripio-test', 'DavidVilla'}


def git_cmd(cwd, *args, env=None):
    subprocess.run(
        ['git', '-c', 'user.name=ripio', '-c', 'user.email=ripio@example.com'] + list(args),
        cwd=cwd, check=True, capture_output=True, env=dict(os.environ, **(env or {})))


def commit(repo, msg, date='2020-01-01T10:00:00'):
    git_cmd(repo, 'commit', '--allow-empty', '-m', msg,
            env={'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date})


def touch(path, date):
    os.utime(path, (date.timestamp(), date.timestamp()))


@pytest.fixture
def clone(tmp_path):
    "Clone of a local bare repository, which plays the remote site: no network access"
    git_cmd(tmp_path, 'init', '--bare', 'remote.git')
    git_cmd(tmp_path, 'clone', 'remote.git', 'clone')
    clone = tmp_path / 'clone'

    (clone / 'tracked').write_text('initial')
    git_cmd(clone, 'add', 'tracked')
    commit(clone, 'initial')
    git_cmd(clone, 'push', 'origin', 'HEAD')
    return clone


class TestLocalRepo:
    def test_clean(self, clone):
        sut = ripio.LocalRepo(clone)
        assert not sut.modified
        assert not sut.untracked
        assert sut.unpushed == 0

    def test_changed_tracked_file(self, clone):
        (clone / 'tracked').write_text('changed')
        sut = ripio.LocalRepo(clone)
        assert sut.modified
        assert not sut.untracked

    def test_staged_new_file(self, clone):
        (clone / 'new').write_text('new')
        git_cmd(clone, 'add', 'new')
        sut = ripio.LocalRepo(clone)
        assert sut.modified

    def test_untracked_file_is_not_a_modification(self, clone):
        (clone / 'untracked').write_text('untracked')
        sut = ripio.LocalRepo(clone)
        assert not sut.modified
        assert sut.untracked

    def test_unpushed_commits(self, clone):
        commit(clone, 'one')
        commit(clone, 'two')
        sut = ripio.LocalRepo(clone)
        assert sut.unpushed == 2

    def test_unpushed_commits_in_branch_without_upstream(self, clone):
        git_cmd(clone, 'checkout', '-b', 'feature')
        commit(clone, 'feature')
        git_cmd(clone, 'checkout', '-')
        sut = ripio.LocalRepo(clone)
        assert sut.unpushed == 1

    def test_unpushed_commits_in_detached_head(self, clone):
        git_cmd(clone, 'checkout', '--detach')
        commit(clone, 'detached')
        sut = ripio.LocalRepo(clone)
        assert sut.unpushed == 1

    def test_hosted_origin(self, clone):
        git_cmd(clone, 'remote', 'set-url', 'origin', 'git@github.com:ripio-test/repo1.git')
        sut = ripio.LocalRepo(clone)
        assert sut.ref == ripio.RepoRef('gh:ripio-test/repo1')

    def test_unsupported_origin(self, clone):
        url = 'git@gitlab.com:ripio-test/repo1.git'
        git_cmd(clone, 'remote', 'set-url', 'origin', url)
        sut = ripio.LocalRepo(clone)
        assert sut.origin == url
        assert sut.ref is None

    def test_no_remote_has_every_commit_unpushed(self, tmp_path):
        git_cmd(tmp_path, 'init', 'local')
        commit(tmp_path / 'local', 'one')
        commit(tmp_path / 'local', 'two')
        sut = ripio.LocalRepo(tmp_path / 'local')
        assert sut.origin is None
        assert sut.ref is None
        assert sut.unpushed == 2

    def test_empty_repository(self, tmp_path):
        git_cmd(tmp_path, 'init', 'empty')
        sut = ripio.LocalRepo(tmp_path / 'empty')
        assert sut.unpushed == 0
        assert sut.last_modified is None

    def test_last_modified_is_last_commit_date(self, clone):
        sut = ripio.LocalRepo(clone)
        assert sut.last_modified == datetime(2020, 1, 1, 10, 0)

    def test_last_modified_includes_commits_in_other_branches(self, clone):
        git_cmd(clone, 'checkout', '-b', 'feature')
        commit(clone, 'feature', date='2021-01-01T10:00:00')
        git_cmd(clone, 'checkout', '-')
        sut = ripio.LocalRepo(clone)
        assert sut.last_modified == datetime(2021, 1, 1, 10, 0)

    def test_last_modified_includes_changed_files(self, clone):
        date = datetime(2022, 1, 1, 10, 0)
        (clone / 'tracked').write_text('changed')
        touch(clone / 'tracked', date)
        sut = ripio.LocalRepo(clone)
        assert sut.last_modified == date

    def test_last_modified_includes_untracked_files_with_unusual_names(self, clone):
        date = datetime(2022, 1, 1, 10, 0)
        (clone / 'untracked ñ').write_text('untracked')
        touch(clone / 'untracked ñ', date)
        sut = ripio.LocalRepo(clone)
        assert sut.last_modified == date

    def test_last_modified_includes_renamed_files(self, clone):
        date = datetime(2022, 1, 1, 10, 0)
        git_cmd(clone, 'mv', 'tracked', 'renamed')
        touch(clone / 'renamed', date)
        sut = ripio.LocalRepo(clone)
        assert sut.last_modified == date

    def test_deleted_file_has_no_date(self, clone):
        (clone / 'tracked').unlink()
        sut = ripio.LocalRepo(clone)
        assert sut.modified
        assert sut.last_modified == datetime(2020, 1, 1, 10, 0)

    def test_bare_repository(self, tmp_path):
        git_cmd(tmp_path, 'init', '--bare', 'bare.git')
        with pytest.raises(ripio.DirectoryIsNotRepository):
            ripio.LocalRepo(tmp_path / 'bare.git')

    def test_find_skips_bare_repositories_and_plain_directories(self, tmp_path, clone):
        (tmp_path / 'plain').mkdir()
        (tmp_path / 'file').write_text('file')
        git_cmd(tmp_path, 'init', 'another')

        result = ripio.LocalRepo.find(tmp_path)
        assert [r.path.name for r in result] == ['another', 'clone']

    def test_find_missing_directory(self, tmp_path):
        with pytest.raises(ripio.DirectoryNotFound):
            ripio.LocalRepo.find(tmp_path / 'missing')


# FIXME: test "cmd: ripio site"
