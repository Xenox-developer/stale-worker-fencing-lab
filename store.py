"""SQLite lease experiment with deliberately unsafe baseline write modes."""

from contextlib import contextmanager
import json
import sqlite3


class Store:
    def __init__(self, path):
        self.path = path
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS lease '
                       '(id INTEGER PRIMARY KEY, generation INTEGER, '
                       'owner TEXT, expires INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS resource '
                       '(id INTEGER PRIMARY KEY, token INTEGER, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS writes '
                       '(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, '
                       'token INTEGER, payload TEXT)')
            db.execute("INSERT OR IGNORE INTO lease VALUES (1, 0, '', 0)")
            db.execute('INSERT OR IGNORE INTO resource VALUES (1, 0, NULL)')
            db.commit()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            # Closing also rolls back any transaction interrupted by an error.
            db.close()

    @staticmethod
    def _validate(mode, token):
        if mode not in ('lease_only', 'precheck', 'fenced'):
            raise ValueError('unknown mode')
        if type(token) is not int or token <= 0:
            raise ValueError('token must be a positive integer')

    def acquire(self, owner, now, ttl):
        if not isinstance(owner, str) or not owner:
            raise ValueError('owner must be a nonempty string')
        if type(now) is not int or now < 0:
            raise ValueError('now must be a nonnegative integer')
        if type(ttl) is not int or ttl <= 0:
            raise ValueError('ttl must be a positive integer')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            generation, expires = db.execute(
                'SELECT generation, expires FROM lease WHERE id=1'
            ).fetchone()
            if expires > now:
                raise ValueError('lease is still active')
            token = generation + 1
            db.execute('UPDATE lease SET generation=?, owner=?, expires=? '
                       'WHERE id=1', (token, owner, now + ttl))
            db.commit()
            return token

    def prepare(self, mode, owner, token, now):
        self._validate(mode, token)
        if mode != 'precheck':
            return True
        with self._connection() as db:
            generation, current_owner, expires = db.execute(
                'SELECT generation, owner, expires FROM lease WHERE id=1'
            ).fetchone()
            return (owner == current_owner and token == generation
                    and now < expires)

    def commit(self, mode, owner, token, payload, allowed):
        self._validate(mode, token)
        if not allowed:
            return False
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'))
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if mode == 'fenced':
                stored_token = db.execute(
                    'SELECT token FROM resource WHERE id=1'
                ).fetchone()[0]
                if token < stored_token:
                    db.rollback()
                    return False
            # Baselines intentionally permit stale writes and token regression.
            db.execute('UPDATE resource SET token=?, payload=? WHERE id=1',
                       (token, encoded))
            db.execute('INSERT INTO writes (owner, token, payload) VALUES (?, ?, ?)',
                       (owner, token, encoded))
            db.commit()
            return True
