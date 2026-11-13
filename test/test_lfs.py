"""Unit tests for the LFS mark of 'ripio ls'.

None of them touch the network: HTTP is faked, and every request is recorded
so a test can assert which URL was asked of each site.
"""

import io
from contextlib import redirect_stdout
from unittest import TestCase

import ripio
from ripio import cli


LFS_ATTRIBUTES = """\
*.png filter=lfs diff=lfs merge=lfs -text
*.txt text
"""

PLAIN_ATTRIBUTES = """\
*.txt text eol=lf
*.sh text eol=lf
"""

COMMENTED_ATTRIBUTES = """\
# *.png filter=lfs diff=lfs merge=lfs -text
*.txt text
"""


class FakeReply:
    def __init__(self, status_code=200, text=''):
        self.status_code = status_code
        self.text = text
        self.headers = {}

    def json(self):
        return {}


class FakeHttp(TestCase):
    "a TestCase whose http_get is faked, recording the asked URLs"

    def fake_http(self, status_code=200, text=''):
        self.asked = []

        def http_get(url, *args, **kargs):
            self.asked.append(url)
            return FakeReply(status_code, text)

        real = ripio.http_get
        ripio.http_get = http_get
        self.addCleanup(setattr, ripio, 'http_get', real)

    def github(self):
        return ripio.GithubRepo('github:acme/widget')

    def bitbucket(self):
        return ripio.BitbucketRepo('bitbucket:acme/widget')


class UsesLfs(FakeHttp):
    def test_github_tracks_lfs(self):
        self.fake_http(text=LFS_ATTRIBUTES)
        self.assertTrue(self.github().uses_lfs())

    def test_bitbucket_tracks_lfs(self):
        self.fake_http(text=LFS_ATTRIBUTES)
        self.assertTrue(self.bitbucket().uses_lfs())

    def test_attributes_without_lfs(self):
        self.fake_http(text=PLAIN_ATTRIBUTES)
        self.assertFalse(self.github().uses_lfs())

    def test_no_attributes_file_at_all(self):
        # by far the common case: the sites answer 404, which here means 'no
        # such file', not 'no such repository'
        self.fake_http(status_code=404)
        self.assertFalse(self.github().uses_lfs())
        self.assertFalse(self.bitbucket().uses_lfs())

    def test_a_commented_out_rule_does_not_count(self):
        self.fake_http(text=COMMENTED_ATTRIBUTES)
        self.assertFalse(self.github().uses_lfs())

    def test_github_asks_for_the_raw_attributes_file(self):
        self.fake_http(text=LFS_ATTRIBUTES)
        self.github().uses_lfs()
        self.assertEqual(
            self.asked,
            ['https://api.github.com/repos/acme/widget/contents/.gitattributes'])

    def test_bitbucket_asks_for_the_attributes_file_at_head(self):
        self.fake_http(text=LFS_ATTRIBUTES)
        self.bitbucket().uses_lfs()
        self.assertEqual(
            self.asked,
            ['https://api.bitbucket.org/2.0/repositories/acme/widget/src/HEAD/.gitattributes'])


class FakeRepo:
    "the little a repository listing shows of a repo"

    def __init__(self, full_name, lfs=False, size=1):
        self.full_name = full_name
        self.access = 'public'
        self.size = size
        self.lfs = lfs

        self.asked = 0

    def uses_lfs(self):
        self.asked += 1
        return self.lfs


class LsColumn(TestCase):
    "'ripio ls' shows the LFS column only when some listed repo uses LFS"

    def ls(self, repos, lfs=True):
        class FakeWorkspace:
            def __init__(self, *args, **kargs):
                pass

            def ls_repos(self):
                return iter(repos)

        real = ripio.GithubWorkspace
        ripio.GithubWorkspace = FakeWorkspace
        self.addCleanup(setattr, ripio, 'GithubWorkspace', real)

        class FakeConfig:
            owner = 'github:acme'
            credentials = {}

        FakeConfig.lfs = lfs

        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_ls_repos(FakeConfig())

        return out.getvalue()

    def test_the_version_control_system_is_not_shown(self):
        # both sites are git only: Bitbucket dropped mercurial and Github
        # never had it, so the field said 'git' on every single row
        out = self.ls([FakeRepo('acme/widget')], lfs=False)

        self.assertNotIn('git', out)
        self.assertIn('public', out)
        self.assertIn('acme/widget', out)

    def test_without_the_option_nothing_is_asked(self):
        # the column costs one request per repository, so it is opt-in
        repos = [FakeRepo('acme/widget', lfs=True), FakeRepo('acme/gadget')]
        out = self.ls(repos, lfs=False)

        self.assertNotIn('LFS', out)
        self.assertEqual([r.asked for r in repos], [0, 0])

    def test_no_repo_uses_lfs(self):
        out = self.ls([FakeRepo('acme/widget'), FakeRepo('acme/gadget')])

        self.assertNotIn('LFS', out)
        self.assertIn('acme/widget', out)
        self.assertIn('acme/gadget', out)

    def test_some_repo_uses_lfs(self):
        out = self.ls([FakeRepo('acme/widget', lfs=True), FakeRepo('acme/gadget')])

        self.assertIn('LFS', out)
        widget, gadget = [l for l in out.splitlines() if 'acme/' in l]
        self.assertIn('LFS', widget)
        self.assertNotIn('LFS', gadget)
