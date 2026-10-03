from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fruitsavor.backend.backup import backup_database, main
from fruitsavor.backend.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.sqlite3'
        self.destination = self.root / 'backup.sqlite3'
        self.store = Store(self.source)
        self.store.initialize()

    def test_live_wal_backup_preserves_all_records_and_blobs(self):
        fruit = self.store.create_fruit({'fruit_type': 'banana', 'storage_method': 'counter'})
        with self.store.connect() as db:
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute('INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       ('scan', fruit['id'], 'now', 'now', '{}', b'image', b'mask', b'overlay'))
            db.execute('INSERT INTO observations VALUES (?, ?, ?, ?)', ('obs', fruit['id'], 'now', '{}'))
            db.execute('INSERT INTO observation_revisions VALUES (?, ?, ?)', ('obs', 1, '{"old":true}'))
            db.commit()
            self.assertTrue(Path(str(self.source) + '-wal').exists())
            backup_database(self.source, self.destination)
            with closing(sqlite3.connect(self.destination)) as restored:
                for table in ('fruits', 'scans', 'observations', 'observation_revisions'):
                    self.assertEqual([tuple(r) for r in db.execute(f'SELECT * FROM {table}')],
                                     restored.execute(f'SELECT * FROM {table}').fetchall())
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone(), ('ok',))
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.store.create_fruit({'fruit_type': 'banana', 'storage_method': 'counter'})
        self.assertEqual(Store(self.destination).get_fruit(fruit['id'])['id'], fruit['id'])
        with closing(sqlite3.connect(self.destination)) as restored:
            self.assertEqual(restored.execute('SELECT count(*) FROM fruits').fetchone()[0], 1)

    def test_existing_destination_is_never_overwritten(self):
        self.destination.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            backup_database(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b'keep')
        with self.assertRaises(FileExistsError):
            backup_database(self.source, self.source)

    def test_invalid_source_leaves_no_backup(self):
        invalid = self.root / 'invalid.sqlite3'
        with closing(sqlite3.connect(invalid)):
            pass
        with self.assertRaises(ValueError):
            backup_database(invalid, self.destination)
        self.assertFalse(self.destination.exists())
        with self.assertRaises(FileNotFoundError):
            backup_database(self.root / 'missing', self.destination)
        self.assertFalse((self.root / 'missing').exists())

    def test_publish_failure_cleans_temporary_file(self):
        with patch('fruitsavor.backend.backup.os.link', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                backup_database(self.source, self.destination)
        self.assertFalse(list(self.root.glob('.fruitsavor-backup-*')))
        self.assertFalse(self.destination.exists())

    def test_cli_uses_configured_database(self):
        with patch.dict(os.environ, {'FRUITSAVOR_DATABASE': str(self.source)}):
            main([str(self.destination)])
        self.assertTrue(self.destination.exists())
