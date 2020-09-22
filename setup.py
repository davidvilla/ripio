#!/usr/bin/env python3

# Copyright (C) 2020 David Villa Alises
#
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import os
from setuptools import setup, find_packages


def local_open(fname):
    return open(os.path.join(os.path.dirname(__file__), fname))


with open("README.md", "r") as readme:
    long_description = readme.read()


exec(open('version.py').read())

setup(name         = 'ripio',
      version      = __version__,
      description  = 'git repositories from command line',
      author       = 'David Villa Alises',
      author_email = 'David.Villa@gmail.com',
      url          = 'https://bitbucket.org/DavidVilla/ripio',
      license      = 'GPL v2 or later',
      scripts      = ['bin/ripio'],
      packages     = find_packages(),
      install_requires = local_open('requirements.txt').readlines(),
      long_description=long_description,
      long_description_content_type="text/markdown",
      classifiers=[
        "Programming Language :: Python :: 3",
      ]
)
