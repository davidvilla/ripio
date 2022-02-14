#!/usr/bin/make -f
# -*- mode:makefile -*-

.PHONY: test
test:
	nosetests3 test

pypi-release:
	$(RM) -r dist
	python3 setup.py sdist
	twine upload dist/*

push:
	git push git@github.com:davidvilla/ripio.git
	git push git@bitbucket.org:DavidVilla/ripio.git

clean:
	$(RM) -r build *.egg-info venv dist
	$(RM) -f .pybuild
