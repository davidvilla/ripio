import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / 'test' / 'fixtures'

pytestmark = pytest.mark.network


def ripio(*args, expected=0, timeout=60):
    "Run the working copy's 'bin/ripio', check its exit status and return its stdout"
    # bin/ripio imports the ripio package from the current directory
    result = subprocess.run(
        ['bin/ripio'] + [str(x) for x in args], cwd=ROOT,
        capture_output=True, text=True, timeout=timeout)

    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout


class TestClone:
    def test_bitbucket_public_no_creds(self, tmp_path):
        ripio('--config', '/dev/null', 'clone', '--destdir', tmp_path, 'bb:ripio-test/repo1')
        assert (tmp_path / 'repo1' / '.git').is_dir()

    def test_bitbucket_private_no_creds(self, tmp_path):
        stdout = ripio('--config', '/dev/null', 'clone', '--destdir', tmp_path,
                       'bb:ripio-test/private', expected=1, timeout=10)
        assert 'AccessDenied' in stdout

    def test_github_public_no_creds(self, tmp_path):
        ripio('--config', '/dev/null', 'clone', '--destdir', tmp_path, 'gh:ripio-test/repo1')
        assert (tmp_path / 'repo1' / '.git').is_dir()

    def test_github_private_no_creds(self, tmp_path):
        stdout = ripio('--config', '/dev/null', 'clone', '--destdir', tmp_path,
                       'gh:ripio-test/private', expected=1, timeout=10)
        assert 'RepositoryNotFound: github:ripio-test/private' in stdout

#   FIXME: def test_show_no_credentials_warning

    def test_old_config(self):
        stdout = ripio('--config', FIXTURES / 'deprecated.conf', 'create', 'gh:ripio-test/foo',
                       expected=1)
        assert 'AccessDenied: github:ripio-test/foo' in stdout


class TestLocalStatus:
    def test_missing_origin(self, tmp_path):
        repo = tmp_path / 'gone'
        subprocess.run(['git', 'init', repo], check=True, capture_output=True)
        subprocess.run(['git', '-C', repo, 'remote', 'add', 'origin',
                        'git@github.com:ripio-test/missing.git'], check=True)

        stdout = ripio('--config', '/dev/null', 'local', 'status', '--destdir', tmp_path)
        assert '   1. !' in stdout

        stdout = ripio('--config', '/dev/null', 'info', repo, expected=1)
        assert "  - origin:      ✘ github:ripio-test/missing (NOT found)" in stdout
