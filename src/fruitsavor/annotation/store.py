"""Versioned, optimistic-concurrency reference annotations with explicit provenance."""
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3

import numpy as np
from PIL import Image, UnidentifiedImageError

from .project import digest


class ConflictError(ValueError):
    pass


class Project:
    def __init__(self, root: Path):
        self.root = root
        blob = (root / 'project.json').read_bytes()
        self.manifest = json.loads(blob)
        if self.manifest['schema_version'] != 1:
            raise ValueError('Unsupported annotation project version')
        self.manifest_hash = digest(blob)
        self.items = {item['id']: item for item in self.manifest['items']}
        if len(self.items) != len(self.manifest['items']):
            raise ValueError('Duplicate sample IDs')
        for item in self.items.values():
            if len(item['id']) != 64 or any(c not in '0123456789abcdef' for c in item['id']):
                raise ValueError('Invalid sample identifier')
            image = self.image(item['id'])
            with Image.open(io.BytesIO(image)) as im:
                if im.size != (item['width'], item['height']):
                    raise ValueError('Sample dimensions do not match project')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS annotations (
                    sample_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    status TEXT NOT NULL, annotator TEXT NOT NULL, origin TEXT NOT NULL,
                    notes TEXT NOT NULL, created_at TEXT NOT NULL, image_sha256 TEXT NOT NULL,
                    mask_sha256 TEXT NOT NULL, mask BLOB NOT NULL,
                    PRIMARY KEY (sample_id, revision)
                );
            ''')
            stored = db.execute("SELECT value FROM metadata WHERE key='project_sha256'").fetchone()
            if stored and stored[0] != self.manifest_hash:
                raise ValueError('Project manifest changed; existing annotations cannot be reused')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('project_sha256', ?)", (self.manifest_hash,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'annotations.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def item(self, identifier):
        if identifier not in self.items:
            raise KeyError('Sample not found')
        return self.items[identifier]

    def image(self, identifier):
        item = self.item(identifier)
        blob = (self.root / 'images' / f'{identifier}.png').read_bytes()
        if digest(blob) != item['image_sha256']:
            raise ValueError('Source image changed')
        return blob

    def latest(self, identifier, include_mask=False):
        self.item(identifier)
        with self.connect() as db:
            row = db.execute('SELECT * FROM annotations WHERE sample_id=? ORDER BY revision DESC LIMIT 1',
                             (identifier,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if digest(result['mask']) != result['mask_sha256']:
            raise ValueError('Saved mask checksum mismatch')
        if not include_mask:
            result.pop('mask')
        return result

    def save(self, identifier, mask_blob, expected_revision, status, annotator, origin, notes, image_sha256):
        item = self.item(identifier)
        self.image(identifier)  # Recheck source bytes before accepting annotations.
        if image_sha256 != item['image_sha256']:
            raise ConflictError('Source image checksum differs from the client')
        if status not in {'draft', 'reviewed'} or origin not in {'human_manual', 'assistant_manual'}:
            raise ValueError('Only explicitly attributed manual references are accepted')
        if not annotator.strip() or len(annotator) > 100 or len(notes) > 2000:
            raise ValueError('Provide an annotator name (up to 100 characters) and notes up to 2000 characters')
        try:
            with Image.open(io.BytesIO(mask_blob)) as im:
                if im.format != 'PNG' or im.size != (item['width'], item['height']):
                    raise ValueError('Reference must be a PNG with the source image dimensions')
                im.load()
                rgb = np.asarray(im.convert('RGBA'))
                if not np.all(rgb[:, :, 3] == 255) or not np.all(rgb[:, :, :3] == rgb[:, :, :1]):
                    raise ValueError('Reference mask must be opaque grayscale')
                gray = rgb[:, :, 0]
                if not np.all((gray == 0) | (gray == 255)):
                    raise ValueError('Reference mask pixels must be exactly 0 or 255')
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('Invalid mask image') from exc
        if status == 'reviewed' and (not np.any(gray) or np.all(gray)):
            raise ValueError('Reviewed references must include both fruit and background')
        output = io.BytesIO()
        Image.fromarray(gray).save(output, format='PNG')
        normalized = output.getvalue()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = db.execute('SELECT coalesce(max(revision), 0) FROM annotations WHERE sample_id=?',
                                  (identifier,)).fetchone()[0]
            if revision:
                previous_origin = db.execute('SELECT origin FROM annotations WHERE sample_id=? AND revision=?',
                                             (identifier, revision)).fetchone()[0]
                if previous_origin != origin:
                    raise ConflictError('Annotation origin is fixed; use a new project for independently drawn references')
            if revision != expected_revision:
                raise ConflictError('A newer annotation exists; reload before saving')
            db.execute('INSERT INTO annotations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (identifier, revision+1, status, annotator.strip(), origin, notes,
                        datetime.now(timezone.utc).isoformat(), image_sha256, digest(normalized), normalized))
        return self.latest(identifier)
