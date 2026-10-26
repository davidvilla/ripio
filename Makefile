#!/usr/bin/make -f
# -*- mode:makefile -*-

.PHONY: test test-offline
test:
	python3 -m pytest

test-offline:
	python3 -m pytest -m "not network"

pypi-release:
	$(RM) -r dist
	python3 setup.py sdist
	twine upload dist/*

push:
	git push git@github.com:davidvilla/ripio.git

clean:
	$(RM) -r build *.egg-info venv dist
	$(RM) -f .pybuild
