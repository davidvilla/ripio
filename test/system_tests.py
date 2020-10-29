from prego import TestCase, Task, command
import hamcrest


class CloneTests(TestCase):
    def test_bitbucket_public_no_creds(self):
        command('rm -rf repo1')
        command('bin/ripio --config /dev/null clone bb:ripio-test/repo1')

    def test_bitbucket_private_no_creds(self):
        ripio = Task()
        ripio.command('ripio --config /dev/null clone bb:ripio-test/private', expected=1)
        ripio.assert_that(ripio.lastcmd.stdout.content, 
                          hamcrest.contains_string('Access denied'))

    def test_github_public_no_creds(self):
        command('rm -rf repo1')
        command('ripio --config /dev/null clone gh:ripio-test/repo1')

    def test_github_private_no_creds(self):
        ripio = Task()
        ripio.command('ripio --config /dev/null clone gh:ripio-test/private', expected=1)
        ripio.assert_that(ripio.lastcmd.stdout.content, 
                          hamcrest.contains_string('ERROR 404: Not Found'))


