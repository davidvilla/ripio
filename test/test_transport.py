"""Unit tests for the git-transport ('ls-remote') fast path.

None of them touch the network: the git command is faked, and every use of the
HTTP API is recorded so a test can assert the fast path did not spend quota.
"""

from unittest import TestCase

import git

import ripio


class FakeGitCmd:
    "stands in for git.cmd.Git, recording what was asked of the server"

    def __init__(self, out='', stderr=None, status=128, error=None):
        self.out = out
        self.stderr = stderr
        self.status = status
        self.error = error
        self.asked = []

    def __call__(self):
        return self

    def ls_remote(self, url, **kargs):
        self.asked.append(url)
        if self.error is not None:
            raise self.error

        if self.stderr is not None:
            raise git.exc.GitCommandError(
                ['git', 'ls-remote', url], self.status, self.stderr)

        return self.out


class FakeGit(TestCase):
    "a TestCase whose git command line is a FakeGitCmd"

    def fake_git(self, **kargs):
        self.git_cmd = FakeGitCmd(**kargs)
        real = git.cmd.Git
        git.cmd.Git = self.git_cmd
        self.addCleanup(setattr, git.cmd, 'Git', real)
        return self.git_cmd


class ApiSpy(ripio.GithubRepo):
    """a repo whose API requests are recorded instead of performed.

    It is credentialed by default: that is the case the git fast path is
    reserved for (see Repo.exists), and 'credentials=None' exercises the
    unconfigured one.
    """

    def __init__(self, name='github:acme/widget', in_api=True, commits=(),
                 credentials=ripio.Credentials('user:secret')):
        super().__init__(name, credentials)
        self.api_calls = []
        self.in_api = in_api
        self.commits = list(commits)

    def check(self):
        self.api_calls.append('check')
        if not self.in_api:
            raise ripio.RepositoryNotFound(self.ref)

        return True

    def last_commits(self, max_=3):
        self.api_calls.append('last_commits')
        if not self.in_api:
            raise ripio.RepositoryNotFound(self.ref)

        return iter(self.commits)


REFS = (
    "2820207b47be28d36c843d133e0fa9700ecc6fcf\tHEAD\n"
    "2820207b47be28d36c843d133e0fa9700ecc6fcf\trefs/heads/master\n")


class SshOrigin(TestCase):
    def test_github(self):
        sut = ripio.GithubRepo('github:UCLM-ARCO/iris2-svn')
        self.assertEqual(sut.ssh_origin, 'git@github.com:UCLM-ARCO/iris2-svn.git')

    def test_bitbucket(self):
        sut = ripio.BitbucketRepo('bitbucket:arco_group/iris2-svn')
        self.assertEqual(sut.ssh_origin, 'git@bitbucket.org:arco_group/iris2-svn.git')

    def test_matches_the_url_the_api_would_report(self):
        # the whole point of building it ourselves: the sites publish exactly
        # this, so no request is needed to learn it
        sut = ripio.GithubRepo('gh:acme/widget')
        self.assertEqual(sut.ssh_origin, 'git@github.com:acme/widget.git')


class LsRemote(FakeGit):
    def test_refs_are_parsed_as_name_to_sha(self):
        self.fake_git(out=REFS)
        result = ripio.git_ls_remote('git@github.com:acme/widget.git')

        self.assertEqual(result, {
            'HEAD': '2820207b47be28d36c843d133e0fa9700ecc6fcf',
            'refs/heads/master': '2820207b47be28d36c843d133e0fa9700ecc6fcf'})

    def test_no_refs_means_an_empty_repository(self):
        self.fake_git(out='')
        self.assertEqual(ripio.git_ls_remote('git@github.com:acme/widget.git'), {})

    def test_github_missing_repo(self):
        self.fake_git(stderr='ERROR: Repository not found.')
        with self.assertRaises(ripio.GitRemoteMissing):
            ripio.git_ls_remote('git@github.com:acme/widget.git')

    def test_bitbucket_denied_repo(self):
        self.fake_git(stderr='conq: repository access denied.')
        with self.assertRaises(ripio.GitRemoteMissing):
            ripio.git_ls_remote('git@bitbucket.org:acme/widget.git')

    def test_unusable_ssh_is_not_a_missing_repo(self):
        self.fake_git(stderr='git@github.com: Permission denied (publickey).')
        with self.assertRaises(ripio.NoGitTransport):
            ripio.git_ls_remote('git@github.com:acme/widget.git')

    def test_no_git_binary_is_not_a_missing_repo(self):
        self.fake_git(error=git.exc.GitCommandNotFound('git', 'not found'))
        with self.assertRaises(ripio.NoGitTransport):
            ripio.git_ls_remote('git@github.com:acme/widget.git')

    def test_timeout_is_not_a_missing_repo(self):
        # a killed process reports no stderr at all
        self.fake_git(stderr='', status=-9)
        with self.assertRaises(ripio.NoGitTransport):
            ripio.git_ls_remote('git@github.com:acme/widget.git')


class Exists(FakeGit):
    def test_reachable_repo_spends_no_api_quota(self):
        self.fake_git(out=REFS)
        sut = ApiSpy()

        self.assertTrue(sut.exists())
        self.assertEqual(sut.api_calls, [])
        self.assertEqual(self.git_cmd.asked, ['git@github.com:acme/widget.git'])

    def test_empty_repo_still_exists(self):
        self.fake_git(out='')
        sut = ApiSpy()

        self.assertTrue(sut.exists())
        self.assertEqual(sut.api_calls, [])

    def test_a_repo_the_ssh_key_can_not_read_falls_back_to_the_api(self):
        # the sites answer 'not found' for a private repo our key can not read,
        # and our API credentials may have more access, so this must not be
        # taken as a final answer
        self.fake_git(stderr='ERROR: Repository not found.')
        sut = ApiSpy(in_api=True)

        self.assertTrue(sut.exists())
        self.assertEqual(sut.api_calls, ['check'])

    def test_missing_everywhere_is_false(self):
        self.fake_git(stderr='ERROR: Repository not found.')
        sut = ApiSpy(in_api=False)

        self.assertFalse(sut.exists())
        self.assertEqual(sut.api_calls, ['check'])

    def test_unusable_ssh_falls_back_to_the_api(self):
        self.fake_git(stderr='git@github.com: Permission denied (publickey).')
        sut = ApiSpy(in_api=True)

        self.assertTrue(sut.exists())
        self.assertEqual(sut.api_calls, ['check'])

    def test_without_credentials_the_api_has_the_only_say(self):
        # our ssh key may reach a private repo the (absent) credentials can
        # not, and an unconfigured ripio must still report it unreachable
        self.fake_git(out=REFS)
        sut = ApiSpy(in_api=False, credentials=None)

        self.assertFalse(sut.exists())
        self.assertEqual(sut.api_calls, ['check'])
        self.assertEqual(self.git_cmd.asked, [])


class IsEmpty(FakeGit):
    def test_no_refs(self):
        self.fake_git(out='')
        sut = ApiSpy()

        self.assertTrue(sut.is_empty())
        self.assertEqual(sut.api_calls, [])

    def test_some_refs(self):
        self.fake_git(out=REFS)
        sut = ApiSpy()

        self.assertFalse(sut.is_empty())
        self.assertEqual(sut.api_calls, [])

    def test_a_repo_the_ssh_key_can_not_read_falls_back_to_the_api(self):
        # 'not found' is also the answer for a repo our key may not read, so
        # the API, which may have more access, has the last word
        self.fake_git(stderr='ERROR: Repository not found.')
        sut = ApiSpy(commits=[dict(hash='2820207')])

        self.assertFalse(sut.is_empty())
        self.assertEqual(sut.api_calls, ['last_commits'])

    def test_missing_repo_is_an_error_not_an_empty_repo(self):
        # missing for the git protocol *and* for the API: no answer at all,
        # which must never be rendered as 'empty'
        self.fake_git(stderr='ERROR: Repository not found.')
        sut = ApiSpy(in_api=False)

        with self.assertRaises(ripio.RepositoryNotFound):
            sut.is_empty()

        self.assertEqual(sut.api_calls, ['last_commits'])

    def test_without_credentials_the_api_has_the_only_say(self):
        # same reason as exists(): our key may reach a private repo the
        # (absent) credentials can not, and an unconfigured ripio must not
        # answer for a repository it has to report as unreachable
        self.fake_git(out='')
        sut = ApiSpy(commits=[dict(hash='2820207')], credentials=None)

        self.assertFalse(sut.is_empty())
        self.assertEqual(sut.api_calls, ['last_commits'])
        self.assertEqual(self.git_cmd.asked, [])

    def test_without_ssh_it_asks_the_api_for_commits(self):
        self.fake_git(stderr='git@github.com: Permission denied (publickey).')
        sut = ApiSpy(commits=[dict(hash='2820207')])

        self.assertFalse(sut.is_empty())
        self.assertEqual(sut.api_calls, ['last_commits'])


class Clone(FakeGit):
    def fake_clone_from(self):
        cloned = []
        real = git.Repo.clone_from
        git.Repo.clone_from = staticmethod(
            lambda url, destdir, **kargs: cloned.append((url, str(destdir))))
        self.addCleanup(setattr, git.Repo, 'clone_from', real)
        return cloned

    def test_ssh_clone_spends_no_api_quota(self):
        cloned = self.fake_clone_from()
        sut = ApiSpy()

        sut.clone('/tmp/widget', proto='ssh')

        self.assertEqual(cloned, [('git@github.com:acme/widget.git', '/tmp/widget')])
        self.assertEqual(sut.api_calls, [])
