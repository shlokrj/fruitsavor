import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from fruitsavor.temporal import audit_samples


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stream = io.BytesIO()
        Image.new('RGB', (10, 12), 'yellow').save(stream, format='PNG')
        self.blob = stream.getvalue()
        self.digest = hashlib.sha256(self.blob).hexdigest()
        (self.root / (self.digest + '.bin')).write_bytes(self.blob)

    def manifest(self, names):
        samples = [dict(archive_member=n, sha256=self.digest, bytes=len(self.blob), dimensions=[10, 12]) for n in names]
        (self.root / 'manifest.json').write_text(json.dumps(dict(dataset_id='test', samples=samples)))

    def test_ambiguous_day_is_not_filled_and_duplicates_reported(self):
        self.manifest(['Banana_ID_009/Day_0.jpg', 'Banana_ID_009/IMG20260123112616.jpg'])
        report = audit_samples(self.root)
        self.assertEqual(report['folders']['Banana_ID_009']['missing_filename_days'], list(range(1, 8)))
        self.assertIsNone(report['samples'][1]['filename_day'])
        self.assertEqual(len(report['exact_pixel_duplicates']), 1)
        self.assertFalse(report['evaluation_ready'])
        self.assertTrue(all(r['group_id'] is None and r['days_remaining'] is None for r in report['samples']))

    def test_modified_original_is_rejected(self):
        self.manifest(['Banana_ID_001/Day_0.jpg'])
        (self.root / (self.digest + '.bin')).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            audit_samples(self.root)

    def test_duplicate_member_is_rejected(self):
        self.manifest(['Banana_ID_001/Day_0.jpg'] * 2)
        with self.assertRaises(ValueError):
            audit_samples(self.root)

    def test_complete_filenames_do_not_establish_identity(self):
        self.manifest([f'Banana_ID_001/Day_{day}.jpg' for day in range(8)])
        report = audit_samples(self.root)
        self.assertEqual(report['folders']['Banana_ID_001']['missing_filename_days'], [])
        self.assertFalse(report['specimen_identity_verified'])

    def test_exif_calendar_disagreement_is_reported(self):
        self.manifest(['Banana_ID_001/Day_0.jpg', 'Banana_ID_001/Day_7.jpg'])
        with patch('PIL.Image.Exif.get_ifd', side_effect=[{36867: '2026:01:15 12:00:00'}, {36867: '2026:01:23 12:00:00'}]):
            report = audit_samples(self.root)
        self.assertEqual(report['filename_exif_day_disagreements'][0]['exif_calendar_days_from_day0'], 8)
        self.assertIsNone(report['samples'][1]['days_remaining'])
