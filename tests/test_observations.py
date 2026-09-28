from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from fruitsavor.backend.app import create_app
from fruitsavor.backend.config import Settings
from fruitsavor.backend.store import Store


class ObservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(database=Path(directory.name) / 'db.sqlite3')
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        self.fruit = self.client.post('/fruit', json={'name':'One', 'storage_method':'counter'}).json()
        self.route = f"/fruit/{self.fruit['id']}/observations"
        self.values = dict(observed_at='2026-01-01T08:00:00-06:00', ripeness='overripe',
                           intended_use='cooking', acceptability='acceptable', notes='Still useful for baking')

    def test_persistence_order_storage_and_no_inferred_target(self):
        first = self.client.post(self.route, json=self.values)
        self.assertEqual(first.status_code, 201, first.text)
        record = first.json()
        self.assertEqual(record['source'], 'user_reported')
        self.assertEqual(record['observed_at'], '2026-01-01T14:00:00Z')
        self.assertEqual(record['acceptability'], 'acceptable')
        self.assertNotIn('days_remaining', record)
        later = self.client.post(self.route, json=dict(self.values, observed_at='2026-01-02T00:00:00Z')).json()
        self.client.patch(f"/fruit/{self.fruit['id']}", json={'storage_method':'refrigerator'})
        with TestClient(create_app(self.settings)) as restarted:
            page = restarted.get(self.route + '?limit=1').json()
            self.assertEqual(page['total'], 2)
            self.assertEqual(page['items'][0]['id'], later['id'])
            self.assertEqual(restarted.get(self.route + '?limit=1&offset=1').json()['items'][0], record)
            self.assertEqual(page['items'][0]['storage_method'], 'counter')

    def test_invalid_reports_and_missing_fruit(self):
        for update in ({'observed_at':'2026-01-01T12:00:00'},
                       {'observed_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
                       {'ripeness':'spoiled'}, {'intended_use':'anything'}, {'acceptability':'safe'},
                       {'notes':'x'*501}, {'source':'model'}, {'days_remaining':2}):
            with self.subTest(update=update):
                self.assertEqual(self.client.post(self.route, json=dict(self.values, **update)).status_code, 422)
        self.assertEqual(self.client.get(self.route).json()['total'], 0)
        self.assertEqual(self.client.post(f'/fruit/{uuid4()}/observations', json=self.values).status_code, 404)
        self.assertEqual(self.client.get(self.route+'?limit=101').status_code, 422)

    def test_auth_and_scoped_deletion_and_cascade(self):
        record = self.client.post(self.route, json=self.values).json()
        other = self.client.post('/fruit', json={}).json()
        self.assertEqual(self.client.delete(f"/fruit/{other['id']}/observations/{record['id']}").status_code, 404)
        with TestClient(create_app(Settings(database=self.settings.database, api_token='test-only'))) as secured:
            self.assertEqual(secured.get(self.route).status_code, 401)
            self.assertEqual(secured.post(self.route, json=self.values).status_code, 401)
            self.assertEqual(secured.delete(self.route+'/'+record['id']).status_code, 401)
        self.assertEqual(self.client.delete(self.route+'/'+record['id']).status_code, 204)
        self.assertEqual(self.client.delete(self.route+'/'+record['id']).status_code, 404)
        self.client.post(self.route, json=self.values)
        self.client.delete(f"/fruit/{self.fruit['id']}")
        with Store(self.settings.database).connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 0)

    def test_migrates_v1_without_losing_scans(self):
        store = Store(self.settings.database)
        scan = store.save_scan({'captured_at':'2026-01-01T00:00:00Z'},
                               {'image':b'image', 'mask':b'mask', 'overlay':b'overlay'}, self.fruit['id'])
        with store.connect() as db:
            db.execute('DROP TABLE observations')
            db.execute('PRAGMA user_version = 1')
        store.initialize()
        store.initialize()
        self.assertEqual(store.artifact(scan['id'], 'image'), b'image')
        self.assertEqual(store.get_fruit(self.fruit['id'])['scan_count'], 1)
        self.assertEqual(self.client.post(self.route, json=self.values).status_code, 201)
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
