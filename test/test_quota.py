"""Unit tests for the warning about a low API request quota.

None of them touch the network: replies are faked, and the clock is fixed so
the 'renewed in' part of the message is predictable.
"""

import io
import time
from contextlib import redirect_stdout
from unittest import TestCase

import ripio
from ripio import cli


NOW = 1700000000


class FakeReply:
    def __init__(self, url, **headers):
        self.url = url
        self.headers = {k.replace('_', '-'): v for k, v in headers.items()}


def github_reply(remaining, limit=5000, reset=NOW + 2700):
    return FakeReply(
        'https://api.github.com/repos/acme/widget',
        X_RateLimit_Remaining=str(remaining),
        X_RateLimit_Limit=str(limit),
        X_RateLimit_Reset=str(reset))


class Clock(TestCase):
    "a TestCase whose 'now' is fixed, and whose quota starts empty"

    def setUp(self):
        real = time.time
        time.time = lambda: NOW
        self.addCleanup(setattr, time, 'time', real)

        previous = ripio.quota
        ripio.quota = ripio.Quota()
        self.addCleanup(setattr, ripio, 'quota', previous)


class LowQuota(Clock):
    def test_plenty_left_says_nothing(self):
        ripio.quota.update(github_reply(4941))
        self.assertEqual(list(ripio.quota.warnings()), [])

    def test_just_above_the_level_says_nothing(self):
        ripio.quota.update(github_reply(1000))     # exactly 20%
        self.assertEqual(list(ripio.quota.warnings()), [])

    def test_below_the_level_warns(self):
        ripio.quota.update(github_reply(999))
        warning, = ripio.quota.warnings()

        self.assertIn('github.com', warning)
        self.assertIn('999', warning)
        self.assertIn('5,000', warning)
        self.assertIn('20%', warning)
        self.assertIn('45m', warning)

    def test_an_exhausted_quota_warns(self):
        ripio.quota.update(github_reply(0, limit=60))
        warning, = ripio.quota.warnings()

        self.assertIn('0 of 60', warning)

    def test_a_reply_without_quota_headers_is_ignored(self):
        # Bitbucket says nothing while the quota lasts, so there is nothing
        # to report about it
        ripio.quota.update(FakeReply('https://api.bitbucket.org/2.0/repositories/acme'))
        self.assertEqual(list(ripio.quota.warnings()), [])

    def test_the_last_reply_of_a_site_is_the_one_that_counts(self):
        ripio.quota.update(github_reply(999))
        ripio.quota.update(github_reply(998))
        warning, = ripio.quota.warnings()

        self.assertIn('998', warning)
        self.assertNotIn('999', warning)

    def test_an_unknown_host_is_ignored(self):
        ripio.quota.update(FakeReply(
            'https://example.com/x', X_RateLimit_Remaining='1', X_RateLimit_Limit='100'))
        self.assertEqual(list(ripio.quota.warnings()), [])


class AfterEachCommand(Clock):
    "the warning is shown when the command ends, whether it worked or not"

    def main(self, fails=False):
        def run():
            if fails:
                raise ripio.RemoteError('boom')

        real = cli.run
        cli.run = run
        self.addCleanup(setattr, cli, 'run', real)

        out = io.StringIO()
        with redirect_stdout(out):
            if fails:
                with self.assertRaises(SystemExit):
                    cli.main_production()
            else:
                cli.main_production()

        return out.getvalue()

    def test_a_command_that_spent_little_says_nothing(self):
        ripio.quota.update(github_reply(4941))
        out = self.main()

        self.assertNotIn('quota', out)
        self.assertIn('-- ok', out)

    def test_a_command_that_left_the_quota_low_warns(self):
        ripio.quota.update(github_reply(11, limit=60))
        out = self.main()

        self.assertIn('quota', out)
        self.assertIn('11 of 60', out)
        # the status line stays the last one
        self.assertTrue(out.rstrip().endswith('-- ok'), out)

    def test_a_failed_command_warns_too(self):
        ripio.quota.update(github_reply(11, limit=60))
        out = self.main(fails=True)

        self.assertIn('11 of 60', out)
        self.assertTrue(out.rstrip().endswith('-- fail'), out)
