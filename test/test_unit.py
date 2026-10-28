import os
import time
import random
import subprocess
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone

import pytest
from doublex import Stub, ANY_ARG

import ripio
from ripio import cli

TEST_DIR = Path(__file__).parent
FIXTURES = TEST_DIR / 'fixtures'


BITBUCKET = SimpleNamespace(
    name='bitbucket',
    abbrev='bb',
    host='bitbucket.org',
    workspace_class=ripio.BitbucketWorkspace,
    repo_class=ripio.BitbucketRepo,
    public_repos={'repo{}'.format(x) for x in range(12)} | {'empty', 'ripio'},
    private_repos={'private'},
    rename_delay=0)

GITHUB = SimpleNamespace(
    name='github',
    abbrev='gh',
    host='github.com',
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

    def test_check_origin(self, site, credentials, clone):
        origin = 'git@{}:ripio-test/repo1.git'.format(site.host)
        git_cmd(clone, 'remote', 'set-url', 'origin', origin)

        sut = ripio.LocalRepo(clone)
        sut.check_origin({site.name: credentials(site.name)})
        assert sut.access == 'public'
        assert sut.moved_to is None

    @pytest.mark.parametrize('site', [GITHUB], ids=['github'], indirect=True)
    def test_last_ci_run_without_runs(self, make_repo):
        assert make_repo('ripio-test/repo1').last_ci_run() is None

    def clone_renamed_repository(self, site, make_repo, new_names, clone):
        "Make clone's origin the former name of a repository just renamed"
        repo = make_repo('ripio-test/' + new_names.removable)
        repo.create()
        time.sleep(site.rename_delay)
        repo.rename(new_names.to_delete)

        origin = 'git@{}:ripio-test/{}.git'.format(site.host, new_names.removable)
        git_cmd(clone, 'remote', 'set-url', 'origin', origin)
        return ripio.LocalRepo(clone)

    @pytest.mark.parametrize('site', [GITHUB], ids=['github'], indirect=True)
    def test_check_origin_of_renamed_repository(self, site, credentials, make_repo, new_names,
                                                clone):
        sut = self.clone_renamed_repository(site, make_repo, new_names, clone)
        sut.check_origin({site.name: credentials(site.name)})
        assert sut.moved_to == ripio.RepoRef.from_parts('ripio-test', new_names.to_delete, site.name)

    # Bitbucket API answers 404 for the former name of a renamed repository
    @pytest.mark.parametrize('site', [BITBUCKET], ids=['bitbucket'], indirect=True)
    def test_check_origin_of_renamed_repository_is_not_found(self, site, credentials, make_repo,
                                                             new_names, clone):
        sut = self.clone_renamed_repository(site, make_repo, new_names, clone)
        with pytest.raises(ripio.RepositoryNotFound):
            sut.check_origin({site.name: credentials(site.name)})


def test_bitbucket_ci_is_not_supported():
    with pytest.raises(NotImplementedError):
        ripio.BitbucketRepo('ripio-test/repo1').last_ci_run()


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
        pytest.param('https://github.com/FRRouting/frr.git', 'github:FRRouting/frr',
                     id='https-dot-git'),
        pytest.param('https://github.com/faizann24/XssPy/', 'github:faizann24/XssPy',
                     id='https-trailing-slash'),
        pytest.param('https://john:secret@github.com/davidvilla/ripio.git', 'github:davidvilla/ripio',
                     id='https-credentials'),
        pytest.param('git@github.com:davidvilla/ripio', 'github:davidvilla/ripio',
                     id='git-without-dot-git'),
        pytest.param('ssh://git@bitbucket.org/DavidVilla/prego3', 'bitbucket:DavidVilla/prego3',
                     id='ssh-without-dot-git'),
    ])
    def test_from_origin(self, origin, expected):
        assert ripio.RepoRef.from_origin(origin) == ripio.RepoRef(expected)

    def test_wrong_url(self):
        with pytest.raises(ripio.BadRepositoryName):
            ripio.RepoRef.from_origin('wrong://bitbucket.org/DavidVilla/ripio.git')


class TestSafeUrl:
    def test_password_is_hidden(self):
        result = ripio.safe_url('https://john:secret@github.com/davidvilla/ripio.git')
        assert result == 'https://john:****@github.com/davidvilla/ripio.git'

    @pytest.mark.parametrize('url', [
        'https://github.com/davidvilla/ripio.git',
        'git@github.com:davidvilla/ripio.git',
        '/home/john/repos/ripio.git',
    ])
    def test_urls_without_password_are_kept(self, url):
        assert ripio.safe_url(url) == url


@pytest.mark.parametrize('seconds, expected', [
    (-5, '0s'),
    (59, '59s'),
    (61, '1m 1s'),
    (3600 + 23 * 60 + 59, '1h 23m'),
    (2 * 86400 + 3 * 3600, '2d 3h'),
    (4 * 365 * 86400 + 51 * 86400 + 3600, '4y 51d'),
])
def test_format_duration(seconds, expected):
    assert ripio.format_duration(seconds) == expected


@pytest.mark.parametrize('iso_date', ['2020-01-02T10:00:00Z', '2020-01-02T12:00:00+02:00'])
def test_parse_date(iso_date):
    utc = datetime(2020, 1, 2, 10, 0, tzinfo=timezone.utc)
    assert ripio.parse_date(iso_date) == utc.astimezone().replace(tzinfo=None)


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
    git_cmd(clone, 'push', '--set-upstream', 'origin', 'HEAD')
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

    def test_unpulled_commits(self, tmp_path, clone):
        git_cmd(tmp_path, 'clone', 'remote.git', 'other')
        commit(tmp_path / 'other', 'remote')
        git_cmd(tmp_path / 'other', 'push', 'origin', 'HEAD')
        git_cmd(clone, 'fetch')

        sut = ripio.LocalRepo(clone)
        assert sut.unpulled == 1
        assert sut.last_fetch is not None

    def test_never_fetched(self, clone):
        sut = ripio.LocalRepo(clone)
        assert sut.unpulled == 0
        assert sut.last_fetch is None


class FakeSiteRepo:
    "A repository at its site, as Repo.make() returns, maybe renamed or transferred"
    def __init__(self, canonical_name, access='public', error=None):
        self.ref = ripio.RepoRef(canonical_name)
        self.canonical_name = canonical_name
        self.access = access
        self.error = error
        self.commits = []
        # NotImplemented: a site without CI support
        self.ci_run = NotImplemented

    def check(self):
        if self.error is not None:
            raise self.error

    def info(self):
        self.check()
        return "- name:          {}\n".format(self.canonical_name)

    def last_commits(self):
        return iter(self.commits)

    def last_ci_run(self):
        if self.ci_run is NotImplemented:
            raise NotImplementedError

        if isinstance(self.ci_run, Exception):
            raise self.ci_run

        return self.ci_run


@pytest.fixture
def site_repos(monkeypatch):
    "Fake sites: FakeSiteRepo by the global name it answers to; any other is a test error"
    repos = {}

    def make(ref, credentials):
        repo = repos[ref.global_name]
        repo.ref = ref
        return repo

    monkeypatch.setattr(ripio.Repo, 'make', staticmethod(make))
    return repos


def local_clone(root, name, origin):
    git_cmd(root, 'init', name)
    git_cmd(root / name, 'remote', 'add', 'origin', origin)
    return root / name


class TestCheckOrigin:
    @pytest.fixture
    def sut(self, tmp_path):
        return ripio.LocalRepo(local_clone(tmp_path, 'repo1', 'git@github.com:ripio-test/repo1.git'))

    def test_unchanged(self, sut, site_repos):
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:ripio-test/repo1', 'private')
        sut.check_origin({})
        assert sut.access == 'private'
        assert sut.moved_to is None

    def test_case_is_ignored(self, sut, site_repos):
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:Ripio-Test/Repo1')
        sut.check_origin({})
        assert sut.moved_to is None

    def test_renamed_or_transferred(self, sut, site_repos):
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:other/renamed')
        sut.check_origin({})
        assert sut.moved_to == ripio.RepoRef('gh:other/renamed')

    def test_not_found(self, sut, site_repos):
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo(
            'github:ripio-test/repo1', error=ripio.RepositoryNotFound(sut.ref))
        with pytest.raises(ripio.RepositoryNotFound):
            sut.check_origin({})

        assert sut.access is None


class TestFindClones:
    def test_by_origin_ignoring_case_and_form(self, tmp_path):
        local_clone(tmp_path, 'a', 'git@github.com:ripio-test/repo1.git')
        local_clone(tmp_path, 'b', 'https://github.com/Ripio-Test/repo1')
        local_clone(tmp_path, 'c', 'git@github.com:ripio-test/other.git')
        git_cmd(tmp_path, 'init', 'no-origin')

        result = ripio.LocalRepo.find_clones(tmp_path, [ripio.RepoRef('gh:ripio-test/repo1')])
        assert [r.path.name for r in result] == ['a', 'b']

    def test_transferred_repository(self, tmp_path, site_repos):
        local_clone(tmp_path, 'old', 'git@github.com:oldorg/repo1.git')
        local_clone(tmp_path, 'unrelated', 'git@github.com:otherorg/repo1.git')
        site_repos['github:oldorg/repo1'] = FakeSiteRepo('github:neworg/repo1')
        site_repos['github:otherorg/repo1'] = FakeSiteRepo('github:otherorg/repo1')

        result = ripio.LocalRepo.find_clones(
            tmp_path, [ripio.RepoRef('gh:neworg/repo1')], credentials={})
        assert [r.path.name for r in result] == ['old']

    def test_no_lookup_without_credentials(self, tmp_path, site_repos):
        local_clone(tmp_path, 'old', 'git@github.com:oldorg/repo1.git')

        result = ripio.LocalRepo.find_clones(tmp_path, [ripio.RepoRef('gh:neworg/repo1')])
        assert result == []


def run_command(func, **kargs):
    config = SimpleNamespace(
        modified=False, unpushed=False, issues=False, by_date=False, offline=False,
        credentials={})
    vars(config).update(kargs)
    func(config)


class TestLocalStatusCommand:
    def test_access_and_problem_marks(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'fine', 'git@github.com:ripio-test/fine.git')
        local_clone(tmp_path, 'gone', 'git@github.com:ripio-test/gone.git')
        local_clone(tmp_path, 'moved', 'git@github.com:ripio-test/repo1.git')
        site_repos['github:ripio-test/fine'] = FakeSiteRepo('github:ripio-test/fine')
        site_repos['github:ripio-test/gone'] = FakeSiteRepo(
            'github:ripio-test/gone', error=ripio.RepositoryNotFound(ripio.RepoRef('gh:ripio-test/gone')))
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:ripio-test/renamed', 'private')

        run_command(cli.cmd_local_status, destdir=tmp_path)

        out = capsys.readouterr().out
        assert 'warning' not in out

        # index and problem, flags, date, access, directory, origin
        rows = [line.split(' - ') for line in out.splitlines() if not line.startswith('-')]
        assert [(row[0][:7], row[2].strip(), row[3].strip()) for row in rows] == [
            ('   1.  ', 'public', 'fine'),
            ('   2. !', '-', 'gone'),
            ('   3. !', 'private', 'moved')]

    def test_site_wide_errors_are_reported_once(self, tmp_path, site_repos, capsys):
        for name in ['repo1', 'repo2']:
            local_clone(tmp_path, name, 'git@github.com:ripio-test/{}.git'.format(name))
            site_repos['github:ripio-test/' + name] = FakeSiteRepo(
                'github:ripio-test/' + name, error=ripio.RateLimitExceeded('github.com'))

        run_command(cli.cmd_local_status, destdir=tmp_path)

        out = capsys.readouterr().out
        assert out.count('- warning:') == 1
        assert '- warning: 2 origins could not be checked:' in out

    def test_offline(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'repo1', 'git@github.com:ripio-test/repo1.git')

        run_command(cli.cmd_local_status, destdir=tmp_path, offline=True)

        out = capsys.readouterr().out
        assert 'checking' not in out
        assert '!' not in out
        assert ' - -       - repo1 - github:ripio-test/repo1' in out

    def test_issues_only(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'fine', 'git@github.com:ripio-test/fine.git')
        local_clone(tmp_path, 'moved', 'git@github.com:ripio-test/repo1.git')
        site_repos['github:ripio-test/fine'] = FakeSiteRepo('github:ripio-test/fine')
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:ripio-test/renamed')

        run_command(cli.cmd_local_status, destdir=tmp_path, issues=True)

        out = capsys.readouterr().out
        assert ' moved ' in out
        assert ' fine ' not in out

    def test_issues_or_unpushed(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'fine', 'git@github.com:ripio-test/fine.git')
        unpushed = local_clone(tmp_path, 'unpushed', 'git@github.com:ripio-test/unpushed.git')
        commit(unpushed, 'unpushed')
        local_clone(tmp_path, 'moved', 'git@github.com:ripio-test/repo1.git')
        site_repos['github:ripio-test/fine'] = FakeSiteRepo('github:ripio-test/fine')
        site_repos['github:ripio-test/unpushed'] = FakeSiteRepo('github:ripio-test/unpushed')
        site_repos['github:ripio-test/repo1'] = FakeSiteRepo('github:ripio-test/renamed')

        run_command(cli.cmd_local_status, destdir=tmp_path, issues=True, unpushed=True)

        out = capsys.readouterr().out
        assert ' moved ' in out
        assert ' unpushed ' in out
        assert ' fine ' not in out

    def test_issues_requires_lookups(self, tmp_path):
        with pytest.raises(ripio.error):
            run_command(cli.cmd_local_status, destdir=tmp_path, issues=True, offline=True)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2020, 1, 2, 11, 23)


class TestInfoCommand:
    def test_local_clones(self, tmp_path, clone, site_repos, monkeypatch, capsys):
        git_cmd(clone, 'remote', 'set-url', 'origin', 'git@github.com:ripio-test/repo1.git')
        commit(clone, 'unpushed')
        site_repo = FakeSiteRepo('github:ripio-test/repo1')
        monkeypatch.setattr(cli, 'get_repo', lambda config: site_repo)
        monkeypatch.setattr(cli, 'datetime', FrozenDatetime)

        run_command(cli.cmd_info, repo='repo1', destdir=tmp_path)

        assert capsys.readouterr().out.splitlines() == [
            "- name:          github:ripio-test/repo1",
            "- last commit:   none",
            "- local clone:   {}".format(clone),
            "  - last change: 2020-01-01 10:00 (1d 1h ago)",
            "  - to push:     ✘ 1 commit",
            "  - to pull:     ✔ nothing, as of last fetch"]

    def test_no_local_clones(self, tmp_path, site_repos, monkeypatch, capsys):
        site_repo = FakeSiteRepo('github:ripio-test/repo1')
        monkeypatch.setattr(cli, 'get_repo', lambda config: site_repo)

        run_command(cli.cmd_info, repo='repo1', destdir=tmp_path)

        out = capsys.readouterr().out
        assert "- local clone:   none at '{}'".format(tmp_path) in out

    def run_info(self, tmp_path, site_repo, monkeypatch, capsys):
        "Lines about the site repository, those before its local clones"
        monkeypatch.setattr(cli, 'get_repo', lambda config: site_repo)
        monkeypatch.setattr(cli, 'datetime', FrozenDatetime)
        run_command(cli.cmd_info, repo='repo1', destdir=tmp_path)
        lines = capsys.readouterr().out.splitlines()
        return lines[1:-1]

    def test_last_commit_first_line(self, tmp_path, monkeypatch, capsys):
        site_repo = FakeSiteRepo('github:ripio-test/repo1')
        site_repo.commits = [dict(message='first line\n\nbody\n')]

        lines = self.run_info(tmp_path, site_repo, monkeypatch, capsys)
        assert lines == ["- last commit:   first line"]

    @pytest.mark.parametrize('status, conclusion, expected', [
        ('completed', 'success', [
            "- CI:            ✔ SUCCESS, workflow 'tests'",
            "  - commit:      fix tests",
            "  - executed:    2020-01-02 10:00 (1h 23m ago)",
            "  - duration:    1m 30s"]),
        ('completed', 'failure', [
            "- CI:            ✘ FAILURE, workflow 'tests'",
            "  - commit:      fix tests",
            "  - executed:    2020-01-02 10:00 (1h 23m ago)",
            "  - duration:    1m 30s"]),
        ('in_progress', None, [
            "- CI:            IN PROGRESS, workflow 'tests'",
            "  - commit:      fix tests",
            "  - executed:    2020-01-02 10:00 (1h 23m ago)",
            "  - elapsed:     1h 23m"]),
    ], ids=['success', 'failure', 'in-progress'])
    def test_ci_run(self, tmp_path, monkeypatch, capsys, status, conclusion, expected):
        site_repo = FakeSiteRepo('github:ripio-test/repo1')
        site_repo.ci_run = dict(
            workflow='tests', title='fix tests', status=status, conclusion=conclusion,
            started=datetime(2020, 1, 2, 10, 0), updated=datetime(2020, 1, 2, 10, 1, 30))

        lines = self.run_info(tmp_path, site_repo, monkeypatch, capsys)
        assert lines[1:] == expected

    @pytest.mark.parametrize('ci_run, expected', [
        (None, ["- CI:            no runs"]),
        (ripio.AccessDenied('github:ripio-test/repo1'), ["- CI:            unavailable (AccessDenied)"]),
        (NotImplemented, []),
    ], ids=['no-runs', 'unavailable', 'unsupported'])
    def test_no_ci_run(self, tmp_path, monkeypatch, capsys, ci_run, expected):
        site_repo = FakeSiteRepo('github:ripio-test/repo1')
        site_repo.ci_run = ci_run

        lines = self.run_info(tmp_path, site_repo, monkeypatch, capsys)
        assert lines[1:] == expected

    def test_clone_directory_of_a_former_name(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'old', 'git@github.com:oldorg/repo1.git')
        site_repos['github:oldorg/repo1'] = FakeSiteRepo('github:neworg/repo1')

        run_command(cli.cmd_info, repo='old', destdir=tmp_path)

        assert capsys.readouterr().out.splitlines()[:4] == [
            "- name:          github:neworg/repo1",
            "- last commit:   none",
            "- local clone:   {}".format(tmp_path / 'old'),
            "  - origin:      ✘ github:oldorg/repo1 (former name)"]

    def test_origin_not_found(self, tmp_path, site_repos, capsys):
        local_clone(tmp_path, 'gone', 'git@github.com:ripio-test/gone.git')
        ref = ripio.RepoRef('gh:ripio-test/gone')
        site_repos[ref.global_name] = FakeSiteRepo(
            ref.global_name, error=ripio.RepositoryNotFound(ref))

        with pytest.raises(ripio.RepositoryNotFound):
            run_command(cli.cmd_info, repo=str(tmp_path / 'gone'), destdir=tmp_path)

        assert capsys.readouterr().out.splitlines()[:3] == [
            "- name:          github:ripio-test/gone",
            "- local clone:   {}".format(tmp_path / 'gone'),
            "  - origin:      ✘ github:ripio-test/gone (NOT found)"]


# FIXME: test "cmd: ripio site"
