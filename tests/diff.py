import datetime
import decimal
import re
import uuid
from collections.abc import Mapping


try:
    from bson import decimal128
    from bson import Regex

    _HAVE_PYMONGO = True
except ImportError:
    _HAVE_PYMONGO = False


class _NO_VALUE:  # noqa: N801
    pass


# we don't use NOTHING because it might be returned from various APIs
NO_VALUE = _NO_VALUE()

_SUPPORTED_BASE_TYPES = (
    float,
    bool,
    str,
    datetime.datetime,
    type(None),
    uuid.UUID,
    int,
    bytes,
    type,
    type(re.compile('')),
)

if _HAVE_PYMONGO:
    _SUPPORTED_TYPES = (*_SUPPORTED_BASE_TYPES, decimal.Decimal, decimal128.Decimal128)
else:
    _SUPPORTED_TYPES = _SUPPORTED_BASE_TYPES


def diff(a, b, path=None):
    path = _make_path(path)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return _diff_sequences(a, b, path)
    if type(a).__name__ == 'SON':
        a = dict(a)
    if type(b).__name__ == 'SON':
        b = dict(b)
    if type(a).__name__ == 'DBRef':
        a = a.as_doc()
    if type(b).__name__ == 'DBRef':
        b = b.as_doc()
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return _diff_dicts(a, b, path)
    if type(a).__name__ == 'ObjectId':
        a = str(a)
    if type(b).__name__ == 'ObjectId':
        b = str(b)
    if type(a).__name__ == 'Int64':
        a = int(a)
    if type(b).__name__ == 'Int64':
        b = int(b)
    if _HAVE_PYMONGO and isinstance(a, Regex):
        a = a.try_compile()
    if _HAVE_PYMONGO and isinstance(b, Regex):
        b = b.try_compile()
    if isinstance(a, (Mapping, list, tuple)):
        return [(path[:], a, b)]
    if not isinstance(a, _SUPPORTED_TYPES):
        raise NotImplementedError(f'Unsupported diff type: {type(a)}')  # pragma: no cover
    if not isinstance(b, _SUPPORTED_TYPES):
        raise NotImplementedError(f'Unsupported diff type: {type(b)}')  # pragma: no cover
    if a != b:
        return [(path[:], a, b)]
    return []


def _diff_dicts(a, b, path):
    if not isinstance(a, type(b)):
        return [(path[:], type(a), type(b))]
    returned = []
    for key in set(a) | set(b):
        a_value = a.get(key, NO_VALUE)
        b_value = b.get(key, NO_VALUE)
        path.append(key)
        if a_value is NO_VALUE or b_value is NO_VALUE:
            returned.append((path[:], a_value, b_value))
        else:
            returned.extend(diff(a_value, b_value, path))
        path.pop()
    return returned


def _diff_sequences(a, b, path):
    if len(a) != len(b):
        return [(path[:], a, b)]
    returned = []
    for i, a_i in enumerate(a):
        path.append(i)
        returned.extend(diff(a_i, b[i], path))
        path.pop()
    return returned


def _make_path(path):
    if path is None:
        return []
    return path
