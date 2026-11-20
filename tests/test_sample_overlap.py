import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from fruitsavor.sample_overlap import audit_overlap


class SampleOverlapTests(unittest.TestCase):
    def make_sample(self, path, color, compression):
        path.mkdir()
        stream = io.BytesIO()
        Image.new('RGB', (8, 8), color).save(stream, format='PNG', compress_level=compression)
        blob = stream.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        (path / (digest + '.bin')).write_bytes(blob)
        (path / 'manifest.json').write_text(json.dumps({'dataset_id': path.name, 'samples': [
            dict(archive_member='banana/day.png', sha256=digest, bytes=len(blob), dimensions=[8, 8])]}))

    def test_different_encodings_of_same_pixels_overlap(self):
        with tempfile.TemporaryDirectory() as folder:
            left, right = Path(folder)/'left', Path(folder)/'right'
            self.make_sample(left, 'yellow', 0)
            self.make_sample(right, 'yellow', 9)
            report = audit_overlap([left, right])
            self.assertEqual(report['overlapping_pixel_groups'], 1)
            items = report['cross_collection_duplicate_groups'][0]['occurrences']
            self.assertNotEqual(items[0]['sha256'], items[1]['sha256'])

    def test_distinct_pixels_do_not_establish_independence(self):
        with tempfile.TemporaryDirectory() as folder:
            left, right = Path(folder)/'left', Path(folder)/'right'
            self.make_sample(left, 'yellow', 0)
            self.make_sample(right, 'green', 0)
            report = audit_overlap([left, right])
            self.assertEqual(report['overlapping_pixel_groups'], 0)
            self.assertFalse(report['evaluation_ready'])
            with self.assertRaises(ValueError):
                audit_overlap([left, left])
