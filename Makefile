#!/usr/bin/make -f
# -*- mode:makefile -*-

.PHONY: test
test:
	nosetests3 test

pypi-release:
	$(RM) -r dist
	python3 setup.py sdist
	twine upload dist/*

clean:
	$(RM) -r build *.egg-info venv dist
	$(RM) -f .pybuild
