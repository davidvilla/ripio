import os
from collections import namedtuple


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
