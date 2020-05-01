#!/usr/bin/make -f
# -*- mode:makefile -*-

.PHONY: test
test:
	nosetests3 test

release:
	python3 setup.py sdist upload

clean:
	$(RM) -r build *.egg-info venv dist
	$(RM) -f .pybuild
