from collections import namedtuple

# https://stackoverflow.com/a/46813147/722624
def dictToObject(d):
    for k,v in d.items():
        if isinstance(v, dict):
            d[k] = dictToObject(v)
    return namedtuple('object', d.keys())(*d.values())