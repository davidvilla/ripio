import os
import sys
from collections import namedtuple
from pathlib import Path


def to_kB(bytes):
    "to kilobytes, not kibibytes"
    k = float(bytes) / 1000
    return "{:.0f} KB".format(k)


def canceled():
    print('-- canceled')
    sys.exit(1)


def user_confirm(text, valid_answers):
    try:
        answer = input(text)
    except KeyboardInterrupt:
        canceled()

    if answer not in valid_answers:
        canceled()

    return answer


def confirm_irrecoverable_operation():
    user_confirm(
        "This is an UNRECOVERABLE operation!!\nAre you sure? (write uppercase 'yes'): ",
        valid_answers=['YES'])


def pretty_path(path):
    return str(path).replace(str(Path.home()), '~')


# https://stackoverflow.com/a/46813147/722624
def dictToObject(d):
    for k, v in d.items():
        if isinstance(v, dict):
            d[k] = dictToObject(v)
    return namedtuple('object', d.keys())(*d.values())


def resolve_path(fname, paths, find_all=False):
    retval = []
    for p in paths:
        path = os.path.join(p, fname)
        if os.path.exists(path):
            if not find_all:
                return [path]

            retval.append(path)

    return retval


def resolve_path_ancestors(fname, path, find_all=False):
    ancestors = []
    while path != os.sep:
        ancestors.append(path)
        path = os.path.dirname(path)

    return resolve_path(fname, ancestors, find_all)


def find_in_ancestors(fname, path):
    try:
        path = os.path.abspath(path)
        return os.path.dirname(resolve_path_ancestors(fname, path)[0])
    except IndexError:
        return None
