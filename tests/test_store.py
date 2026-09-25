from pathlib import Path
import tempfile
import unittest

from fruitsavor.backend.store import NotFoundError, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'data.sqlite3'
        self.store = Store(self.path)
        self.store.initialize()
        self.fruit = self.store.create_fruit(dict(fruit_type='banana', name='One', storage_method='counter'))

    def save(self, day, fruit_id=None):
        return self.store.save_scan(dict(captured_at=f'2026-01-{day:02d}T12:00:00+00:00',
                                         storage_method='counter'),
                                    dict(image=b'image', mask=b'mask', overlay=b'overlay'), fruit_id)

    def test_history_order_pagination_and_restart(self):
        first = self.save(1, self.fruit['id'])
        last = self.save(3, self.fruit['id'])
        self.save(2, self.fruit['id'])
        restarted = Store(self.path)
        restarted.initialize()
        page = restarted.list_scans(1, 0, self.fruit['id'])
        self.assertEqual(page['total'], 3)
        self.assertEqual(page['items'][0]['id'], last['id'])
        self.assertEqual(restarted.list_scans(1, 2, self.fruit['id'])['items'][0]['id'], first['id'])
        self.assertEqual(restarted.get_fruit(self.fruit['id'])['scan_count'], 3)
        self.assertEqual(restarted.artifact(first['id'], 'mask'), b'mask')

    def test_delete_fruit_cascades_only_its_scans(self):
        linked = self.save(1, self.fruit['id'])
        standalone = self.save(2)
        self.store.delete_fruit(self.fruit['id'])
        with self.assertRaises(NotFoundError):
            self.store.get_scan(linked['id'])
        with self.assertRaises(NotFoundError):
            self.store.artifact(linked['id'], 'image')
        self.assertEqual(self.store.get_scan(standalone['id'])['id'], standalone['id'])

    def test_failed_scan_transaction_rolls_back(self):
        with self.assertRaises(KeyError):
            self.store.save_scan(dict(captured_at='2026-01-01T00:00:00+00:00'),
                                 dict(image=b'image'), self.fruit['id'])
        self.assertEqual(self.store.get_fruit(self.fruit['id']), self.fruit)
        self.assertEqual(self.store.list_scans(10, 0)['total'], 0)

    def test_missing_parent_and_unsafe_artifact(self):
        with self.assertRaises(NotFoundError):
            self.save(1, 'missing')
        scan = self.save(1)
        with self.assertRaises(NotFoundError):
            self.store.artifact(scan['id'], 'image FROM scans; --')
        self.assertEqual(self.store.get_scan(scan['id'])['id'], scan['id'])

    def test_updates_do_not_change_historical_storage(self):
        scan = self.save(1, self.fruit['id'])
        self.store.update_fruit(self.fruit['id'], dict(storage_method='refrigerator', name=None))
        self.assertEqual(self.store.get_scan(scan['id'])['storage_method'], 'counter')
        self.assertIsNone(self.store.get_fruit(self.fruit['id'])['name'])
        self.store.delete_scan(scan['id'])
        self.assertEqual(self.store.get_fruit(self.fruit['id'])['scan_count'], 0)

    def test_newer_schema_is_rejected(self):
        with self.store.connect() as db:
            db.execute('PRAGMA user_version = 99')
        with self.assertRaises(RuntimeError):
            self.store.initialize()
