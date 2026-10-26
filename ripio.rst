=====
ripio
=====

---------------------------------------------------
manage git repositories hosted on GitHub/Bitbucket
---------------------------------------------------

:Author: David Villa Alises <David.Villa@gmail.com>
:Date: 2026-09-14
:Manual section: 1

SYNOPSIS
========

``ripio`` [--config FILE] [-v] COMMAND [ARGS...]

DESCRIPTION
===========

``ripio`` manages git repositories hosted at bitbucket.org and github.com
from the command line: listing, cloning, creating, renaming, deleting and
inspecting repositories, without leaving the shell.

A repository is referenced as ``site:owner/name``, for example
``github:twitter/wordpress`` or ``bitbucket:paypal/example`` (``gh`` and
``bb`` are accepted as site abbreviations). A plain git URL
(``https://github.com/owner/name`` or ``git@github.com:owner/name.git``) is
also accepted. When workspaces are configured (see CONFIGURATION below), a
bare name such as ``example`` or ``gh:example`` may be used and ``ripio``
will look it up among the configured workspaces.

OPTIONS
=======

--config FILE   alternate config file (default: ``~/.config/ripio``)
-v, --verbosity  increase verbosity; repeat for more detail
                 (``-v``: INFO, ``-vv``: DEBUG)

COMMANDS
========

help
    Show usage help.

ls WORKSPACE
    List the repositories of a team or user. WORKSPACE follows the
    ``site:name`` format, e.g. ``github:twitter`` or ``bb:paypal``.

head REPO
    Show the last commits of a repository.

info [REPO]
    Show repository information. Without REPO, uses the repository in the
    current directory.

rename REPO NEW-NAME
    Rename a repository.

create [--public] [--clone] REPO
    Create a new repository. Private by default; use ``--public`` to
    create it public. With ``--clone``, clone it right after creation.

delete REPO
    Delete a repository.

clone [--http] [--destdir DIR] REPO
    Clone a repository. Uses SSH by default; ``--http`` uses HTTPS
    instead. ``--destdir`` sets the destination directory (default:
    ``[clone] destdir`` in the config file, or the current directory).

local status [--destdir DIR] [-m] [-u] [-t]
    List the git repositories at the clone directory (``[clone] destdir``
    in the config file, unless ``--destdir`` is given), their last
    modification date and their pending work: ``M`` marks changes in
    tracked files, ``?`` untracked files and ``+N`` N commits not pushed to
    any remote (local branches and detached HEAD). ``-m`` (``--modified``)
    lists only repositories with changes in tracked files, ``-u``
    (``--unpushed``) only those with unpushed commits; given both,
    repositories matching either are listed. ``-t`` (``--by-date``) sorts
    by last modification, newest first: the newest of the last commit in
    local branches (or detached HEAD) and the files with local changes.
    There is no network access: remote branches are the ones known since
    the last fetch.

ls-colab REPO
    List a repository's collaborators.

config
    Show the effective configuration.

site
    Open the web page of the repository in the current directory.

CONFIGURATION
=============

``ripio`` reads a TOML configuration file, given with ``--config`` or
found at ``~/.config/ripio``::

    [clone]
    destdir = "~/repos"

    [bitbucket]
    credentials = "JohnDoe:secret"
    workspaces = ["team1", "team2"]

    [github]
    credentials = "JohnDoe:secret"
    workspaces = ["org1", "org2"]

Credentials should be generated as scoped app passwords/tokens rather than
account passwords:

- https://bitbucket.org/account/settings/app-passwords/
- https://github.com/settings/tokens

EXIT STATUS
===========

``ripio`` exits with a non-zero status when a command fails (repository
not found, access denied, network error, etc).

SEE ALSO
========

git(1)

COPYRIGHT
=========

Copyright © 2020 David Villa Alises

Permission is granted to copy, distribute and/or modify this document
under the terms of the GNU General Public License, Version 3 or (at
your option) any later version published by the Free Software
Foundation.

On Debian systems, the complete text of the GNU General Public License
can be found in /usr/share/common-licenses/GPL.
