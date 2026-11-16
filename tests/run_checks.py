import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import Store

MODES = ('lease_only', 'precheck', 'fenced')


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state.db')
        self.s = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT owner,token,payload FROM writes ORDER BY seq').fetchall()

    def test_acquire_exclusion(self):
        self.assertEqual(self.s.acquire('A', 0, 10), 1)
        with self.assertRaises(ValueError):
            self.s.acquire('B', 9, 10)
        self.assertEqual(self.s.acquire('B', 10, 10), 2)
        self.assertEqual(Store(self.path).acquire('C', 20, 10), 3)

    def test_clean_all_modes(self):
        token = self.s.acquire('A', 0, 10)
        for mode in MODES:
            self.assertIs(self.s.prepare(mode, 'A', token, 1), True)
            self.assertIs(self.s.commit(mode, 'A', token, {'z': 'тест', 'a': 1}, True), True)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.rows()[0][2], '{"a":1,"z":"тест"}')

    def test_precheck_and_gap(self):
        a = self.s.acquire('A', 0, 10)
        allowed = self.s.prepare('precheck', 'A', a, 1)
        b = self.s.acquire('B', 11, 10)
        self.assertTrue(self.s.commit('precheck', 'B', b, {'new': 1}, True))
        self.assertIs(self.s.prepare('precheck', 'A', a, 12), False)
        self.assertTrue(self.s.commit('precheck', 'A', a, {'old': 1}, allowed))
        self.assertEqual(self.rows()[-1][1], 1)

    def test_fence_rejects_older(self):
        a = self.s.acquire('A', 0, 10)
        b = self.s.acquire('B', 11, 10)
        self.assertTrue(self.s.commit('fenced', 'B', b, {'new': 1}, True))
        before = self.rows()
        self.assertIs(self.s.commit('fenced', 'A', a, {'old': 1}, True), False)
        self.assertEqual(self.rows(), before)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT token,payload FROM resource').fetchone(), (2, '{"new":1}'))

    def test_fencing_is_not_ttl_or_dedupe(self):
        a = self.s.acquire('A', 0, 10)
        self.assertFalse(self.s.prepare('precheck', 'A', a, 10))
        b = self.s.acquire('B', 11, 10)
        self.assertTrue(self.s.commit('fenced', 'A', a, {'old': 1}, True))
        self.assertTrue(self.s.commit('fenced', 'B', b, {'new': 1}, True))
        self.assertTrue(self.s.commit('fenced', 'B', b, {'new': 2}, True))
        self.assertEqual(len(self.rows()), 3)

    def test_rejection_noop_and_baseline(self):
        a = self.s.acquire('A', 0, 10)
        for mode in MODES:
            self.assertIs(self.s.commit(mode, 'A', a, {}, False), False)
        self.assertEqual(self.rows(), [])
        b = self.s.acquire('B', 11, 10)
        self.assertTrue(self.s.prepare('lease_only', 'A', a, 12))
        self.s.commit('lease_only', 'B', b, {'new': 1}, True)
        self.s.commit('lease_only', 'A', a, {'old': 1}, True)
        self.assertEqual(self.rows()[-1][1], a)

    def test_invalid_inputs(self):
        for args in [('', 0, 10), ('A', -1, 1), ('A', True, 1), ('A', 0, 0), ('A', 0, False)]:
            with self.assertRaises(ValueError):
                self.s.acquire(*args)
        for mode, token in [('other', 1), ('fenced', 0), ('fenced', True)]:
            with self.assertRaises(ValueError):
                self.s.prepare(mode, 'A', token, 0)
            with self.assertRaises(ValueError):
                self.s.commit(mode, 'A', token, {}, True)

    def test_independent_databases(self):
        other = Store(str(Path(self.tmp.name) / 'other.db'))
        self.assertEqual(other.acquire('B', 0, 2), 1)
        self.assertEqual(self.s.acquire('A', 0, 2), 1)
        self.assertFalse(other.prepare('precheck', 'A', 1, 1))


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    Path('check-report.json').write_text(json.dumps({'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'passed': result.wasSuccessful()}))
    raise SystemExit(not result.wasSuccessful())
