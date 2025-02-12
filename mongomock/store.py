import os
import collections
import datetime
import functools
import weakref
import bson

import mongomock
from mongomock.thread import RWLock


class ServerStore:
    """Object holding the data for a whole server (many databases)."""

    def __init__(self, filename=None):
        self._filename = os.environ.get('MONGOMOCK_SERVERSTORE_FILE', filename)
        if self._filename:
            with open(self._filename, 'r', encoding='utf-8') as fh:
                dct = bson.json_util.loads(fh.read())
            self._databases = {k: DatabaseStore.from_dict(v) for k, v in dct.items()}
            self._finalizer = weakref.finalize(self, self._to_file)
        else:
            self._databases = {}

    def __getitem__(self, db_name):
        try:
            return self._databases[db_name]
        except KeyError:
            db = self._databases[db_name] = DatabaseStore()
            return db

    def __contains__(self, db_name):
        return self[db_name].is_created

    def list_created_database_names(self):
        return [name for name, db in self._databases.items() if db.is_created]

    def to_dict(self):
        return {k: v.to_dict() for k, v in self._databases.items()}

    def _to_file(self):
        with open(self._filename, 'w', encoding='utf-8') as fh:
            fh.write(bson.json_util.dumps(self.to_dict()))


class DatabaseStore:
    """Object holding the data for a database (many collections)."""

    def __init__(self, _collections=None):
        self._collections = _collections or {}

    def __getitem__(self, col_name):
        try:
            return self._collections[col_name]
        except KeyError:
            col = self._collections[col_name] = CollectionStore(col_name)
            return col

    def __contains__(self, col_name):
        return self[col_name].is_created

    def list_created_collection_names(self):
        return [name for name, col in self._collections.items() if col.is_created]

    def create_collection(self, name):
        col = self[name]
        col.create()
        return col

    def rename(self, name, new_name):
        col = self._collections.pop(name, CollectionStore(new_name))
        col.name = new_name
        self._collections[new_name] = col

    @property
    def is_created(self):
        return any(col.is_created for col in self._collections.values())

    def to_dict(self):
        return {k: v.to_dict() for k, v in self._collections.items()}

    @classmethod
    def from_dict(cls, dct):
        return cls({k: CollectionStore.from_dict(v) for k, v in dct.items()})


class CollectionStore:
    """Object holding the data for a collection."""

    def __init__(self, name, documents=None):
        self._documents = documents or collections.OrderedDict()
        self.indexes = {}
        self._is_force_created = False
        self.name = name
        self._ttl_indexes = {}

        # 694 - Lock for safely iterating and mutating OrderedDicts
        self._rwlock = RWLock()

    def create(self):
        self._is_force_created = True

    @property
    def is_created(self):
        return self._documents or self.indexes or self._is_force_created

    def drop(self):
        self._documents = collections.OrderedDict()
        self.indexes = {}
        self._ttl_indexes = {}
        self._is_force_created = False

    def create_index(self, index_name, index_dict):
        self.indexes[index_name] = index_dict
        if index_dict.get('expireAfterSeconds') is not None:
            self._ttl_indexes[index_name] = index_dict

    def drop_index(self, index_name):
        self._remove_expired_documents()

        # The main index object should raise a KeyError, but the
        # TTL indexes have no meaning to the outside.
        del self.indexes[index_name]
        self._ttl_indexes.pop(index_name, None)

    @property
    def is_empty(self):
        self._remove_expired_documents()
        return not self._documents

    def __contains__(self, key):
        self._remove_expired_documents()
        with self._rwlock.reader():
            return key in self._documents

    def __getitem__(self, key):
        self._remove_expired_documents()
        with self._rwlock.reader():
            return self._documents[key]

    def __setitem__(self, key, val):
        with self._rwlock.writer():
            self._documents[key] = val

    def __delitem__(self, key):
        with self._rwlock.writer():
            del self._documents[key]

    def __len__(self):
        self._remove_expired_documents()
        with self._rwlock.reader():
            return len(self._documents)

    @property
    def documents(self):
        self._remove_expired_documents()
        with self._rwlock.reader():
            yield from self._documents.values()

    def _remove_expired_documents(self):
        for index in self._ttl_indexes.values():
            self._expire_documents(index)

    def _expire_documents(self, index):
        # TODO(juannyg): use a caching mechanism to avoid re-expiring the documents if
        # we just did and no document was added / updated

        # Ignore non-integer values
        try:
            expiry = int(index['expireAfterSeconds'])
        except ValueError:
            return

        # Ignore commpound keys
        if len(index['key']) > 1:
            return

        # "key" structure = list of (field name, direction) tuples
        ttl_field_name = next(iter(index['key']))[0]
        ttl_now = mongomock.utcnow()

        with self._rwlock.reader():
            expired_ids = [
                doc['_id']
                for doc in self._documents.values()
                if self._value_meets_expiry(doc.get(ttl_field_name), expiry, ttl_now)
            ]

        for exp_id in expired_ids:
            del self[exp_id]

    def _value_meets_expiry(self, val, expiry, ttl_now):
        val_to_compare = _get_min_datetime_from_value(val)
        try:
            return (ttl_now - val_to_compare).total_seconds() >= expiry
        except TypeError:
            return False

    def to_dict(self):
        return {'name': self.name, 'documents': list(self._documents.items())}

    @classmethod
    def from_dict(cls, dct):
        return cls(dct['name'], collections.OrderedDict(dct['documents']))


def _get_min_datetime_from_value(val):
    if not val:
        return datetime.datetime.max
    if isinstance(val, list):
        return functools.reduce(_min_dt, [datetime.datetime.max, *val])
    return val


def _min_dt(dt1, dt2):
    try:
        return dt1 if dt1 < dt2 else dt2
    except TypeError:
        return dt1
