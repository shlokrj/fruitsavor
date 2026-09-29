"""Transactional SQLite storage for fruit, scans and normalized image artifacts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA secure_delete = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode = WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise RuntimeError(f'Unsupported database schema version: {version}')
            db.executescript('''
                BEGIN;
                CREATE TABLE IF NOT EXISTS fruits (
                    id TEXT PRIMARY KEY,
                    fruit_type TEXT NOT NULL,
                    name TEXT,
                    storage_method TEXT NOT NULL,
                    purchased_on TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY,
                    fruit_id TEXT REFERENCES fruits(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    image BLOB NOT NULL,
                    mask BLOB NOT NULL,
                    overlay BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scans_fruit_history ON scans(fruit_id, captured_at DESC, id);
                CREATE INDEX IF NOT EXISTS scans_recent ON scans(created_at DESC, id);
                CREATE INDEX IF NOT EXISTS fruits_recent ON fruits(updated_at DESC, id);
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    fruit_id TEXT NOT NULL REFERENCES fruits(id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observations_history ON observations(fruit_id, observed_at DESC, id);
                CREATE TABLE IF NOT EXISTS observation_revisions (
                    observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (observation_id, revision)
                );
            ''' + ('ALTER TABLE fruits ADD COLUMN collection_group TEXT;' if version < 3 else '') + '''
                PRAGMA user_version = 3;
                COMMIT;
            ''')

    @staticmethod
    def _fruit(db, fruit_id):
        row = db.execute('''SELECT f.*, (SELECT count(*) FROM scans s WHERE s.fruit_id=f.id) AS scan_count
                            FROM fruits f WHERE f.id=?''', (fruit_id,)).fetchone()
        if row is None:
            raise NotFoundError('Fruit not found')
        return dict(row)

    def create_fruit(self, values):
        identifier, timestamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute('INSERT INTO fruits VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (identifier, values['fruit_type'], values.get('name'), values['storage_method'],
                        values.get('purchased_on'), timestamp, timestamp, values.get('collection_group')))
            return self._fruit(db, identifier)

    def get_fruit(self, fruit_id):
        with self.connect() as db:
            return self._fruit(db, fruit_id)

    def list_fruits(self, limit, offset):
        with self.connect() as db:
            total = db.execute('SELECT count(*) FROM fruits').fetchone()[0]
            rows = db.execute('''SELECT f.*, (SELECT count(*) FROM scans s WHERE s.fruit_id=f.id) AS scan_count
                                 FROM fruits f ORDER BY updated_at DESC, id LIMIT ? OFFSET ?''',
                              (limit, offset)).fetchall()
            return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)

    def update_fruit(self, fruit_id, values):
        allowed = {'name', 'storage_method', 'purchased_on', 'collection_group'}
        if set(values) - allowed:
            raise ValueError('Unsupported fruit update')
        with self.connect() as db:
            self._fruit(db, fruit_id)
            if values:
                values = dict(values, updated_at=now())
                assignments = ', '.join(f'{column}=?' for column in values)
                db.execute(f'UPDATE fruits SET {assignments} WHERE id=?', (*values.values(), fruit_id))
            return self._fruit(db, fruit_id)

    def delete_fruit(self, fruit_id):
        with self.connect() as db:
            if not db.execute('DELETE FROM fruits WHERE id=?', (fruit_id,)).rowcount:
                raise NotFoundError('Fruit not found')

    def save_scan(self, payload, artifacts, fruit_id=None):
        identifier, timestamp = str(uuid4()), now()
        record = dict(payload, id=identifier, fruit_id=fruit_id, created_at=timestamp)
        with self.connect() as db:
            if fruit_id is not None:
                self._fruit(db, fruit_id)
                db.execute('UPDATE fruits SET updated_at=? WHERE id=?', (timestamp, fruit_id))
            db.execute('INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (identifier, fruit_id, timestamp, record['captured_at'], json.dumps(record, allow_nan=False),
                        artifacts['image'], artifacts['mask'], artifacts['overlay']))
        return record

    def get_scan(self, scan_id):
        with self.connect() as db:
            row = db.execute('SELECT result_json FROM scans WHERE id=?', (scan_id,)).fetchone()
            if row is None:
                raise NotFoundError('Scan not found')
            return json.loads(row['result_json'])

    def list_scans(self, limit, offset, fruit_id=None):
        with self.connect() as db:
            if fruit_id is not None:
                self._fruit(db, fruit_id)
            where = 'WHERE fruit_id=?' if fruit_id is not None else ''
            params = (fruit_id,) if fruit_id is not None else ()
            total = db.execute(f'SELECT count(*) FROM scans {where}', params).fetchone()[0]
            rows = db.execute(f'''SELECT result_json FROM scans {where}
                                  ORDER BY captured_at DESC, id LIMIT ? OFFSET ?''',
                              (*params, limit, offset)).fetchall()
            return dict(items=[json.loads(row['result_json']) for row in rows],
                        total=total, limit=limit, offset=offset)

    def artifact(self, scan_id, kind):
        if kind not in {'image', 'mask', 'overlay'}:
            raise NotFoundError('Artifact not found')
        with self.connect() as db:
            row = db.execute(f'SELECT {kind} FROM scans WHERE id=?', (scan_id,)).fetchone()
            if row is None:
                raise NotFoundError('Scan not found')
            return row[0]

    def delete_scan(self, scan_id):
        with self.connect() as db:
            row = db.execute('SELECT fruit_id FROM scans WHERE id=?', (scan_id,)).fetchone()
            if row is None:
                raise NotFoundError('Scan not found')
            db.execute('DELETE FROM scans WHERE id=?', (scan_id,))
            if row['fruit_id']:
                db.execute('UPDATE fruits SET updated_at=? WHERE id=?', (now(), row['fruit_id']))

    def healthy(self):
        with self.connect() as db:
            db.execute('SELECT count(*) FROM fruits').fetchone()

    def save_observation(self, fruit_id, values):
        identifier, timestamp = str(uuid4()), now()
        values = dict(values, observed_at=datetime.fromisoformat(values['observed_at']).astimezone(
            timezone.utc).isoformat(timespec='microseconds'))
        with self.connect() as db:
            fruit = self._fruit(db, fruit_id)
            record = dict(values, id=identifier, fruit_id=fruit_id, created_at=timestamp,
                          source='user_reported', storage_method=fruit['storage_method'])
            db.execute('INSERT INTO observations VALUES (?, ?, ?, ?)',
                       (identifier, fruit_id, record['observed_at'], json.dumps(record, allow_nan=False)))
            db.execute('UPDATE fruits SET updated_at=? WHERE id=?', (timestamp, fruit_id))
            return record

    def list_observations(self, fruit_id, limit, offset):
        with self.connect() as db:
            self._fruit(db, fruit_id)
            total = db.execute('SELECT count(*) FROM observations WHERE fruit_id=?', (fruit_id,)).fetchone()[0]
            rows = db.execute('SELECT result_json FROM observations WHERE fruit_id=? '
                              'ORDER BY observed_at DESC, id LIMIT ? OFFSET ?', (fruit_id, limit, offset)).fetchall()
            return dict(items=[json.loads(row[0]) for row in rows], total=total, limit=limit, offset=offset)

    def delete_observation(self, fruit_id, observation_id):
        with self.connect() as db:
            self._fruit(db, fruit_id)
            if not db.execute('DELETE FROM observations WHERE id=? AND fruit_id=?',
                              (observation_id, fruit_id)).rowcount:
                raise NotFoundError('Observation not found')
            db.execute('UPDATE fruits SET updated_at=? WHERE id=?', (now(), fruit_id))

    def update_observation(self, fruit_id, observation_id, values):
        values = dict(values)
        expected = values.pop('expected_revision')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT result_json FROM observations WHERE id=? AND fruit_id=?',
                             (observation_id, fruit_id)).fetchone()
            if row is None:
                raise NotFoundError('Observation not found')
            previous = json.loads(row[0])
            revision = previous.get('revision', 1)
            if expected != revision:
                raise ConflictError('This check-in changed. Reopen it before editing again.')
            values['observed_at'] = datetime.fromisoformat(values['observed_at']).astimezone(
                timezone.utc).isoformat(timespec='microseconds')
            record = dict(previous, **values, revision=revision + 1, updated_at=now())
            db.execute('INSERT INTO observation_revisions VALUES (?, ?, ?)',
                       (observation_id, revision, row[0]))
            db.execute('UPDATE observations SET observed_at=?, result_json=? WHERE id=?',
                       (record['observed_at'], json.dumps(record, allow_nan=False), observation_id))
            db.execute('UPDATE fruits SET updated_at=? WHERE id=?', (record['updated_at'], fruit_id))
            return record

    def observation_revisions(self, fruit_id, observation_id):
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT result_json FROM observations WHERE id=? AND fruit_id=?',
                             (observation_id, fruit_id)).fetchone()
            if row is None:
                raise NotFoundError('Observation not found')
            history = db.execute('SELECT result_json FROM observation_revisions WHERE observation_id=? '
                                 'ORDER BY revision', (observation_id,)).fetchall()
            return [json.loads(item[0]) for item in history] + [json.loads(row[0])]

    def export_fruit(self, fruit_id):
        with self.connect() as db:
            db.execute('BEGIN')
            fruit = self._fruit(db, fruit_id)
            scans = db.execute('SELECT result_json FROM scans WHERE fruit_id=? ORDER BY captured_at, id',
                               (fruit_id,)).fetchall()
            observations = db.execute('SELECT result_json FROM observations WHERE fruit_id=? ORDER BY observed_at, id',
                                      (fruit_id,)).fetchall()
            revisions = db.execute('SELECT r.result_json FROM observation_revisions r JOIN observations o '
                                   'ON o.id=r.observation_id WHERE o.fruit_id=? ORDER BY r.observation_id,r.revision',
                                   (fruit_id,)).fetchall()
            def observation_records(rows):
                records = [json.loads(row[0]) for row in rows]
                for record in records:
                    record.setdefault('revision', 1)
                    record.setdefault('updated_at', None)
                return records
            return dict(export_version=1, exported_at=now(), fruit=fruit,
                        scans=[json.loads(row[0]) for row in scans],
                        observations=observation_records(observations),
                        previous_observation_revisions=observation_records(revisions),
                        image_files_included=False, collection_group_source='user_declared',
                        evaluation_ready=False, shelf_life_targets=None)
