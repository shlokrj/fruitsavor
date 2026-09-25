import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from fruitsavor.data import audit_bananaimagebd, download, grouped_split, safe_member


class DataTests(unittest.TestCase):
    def test_group_split_is_disjoint_and_order_independent(self):
        rows = [dict(group_id=f'banana:{group}', day=day)
                for group in range(20) for day in range(8)]
        result = grouped_split(rows)
        assignment = {row['group_id']: row['split'] for row in result}
        self.assertEqual(set(assignment.values()), {'train', 'validation', 'test'})
        self.assertTrue(all(row['split'] == assignment[row['group_id']] for row in result))
        reversed_assignment = {row['group_id']: row['split']
                               for row in grouped_split(list(reversed(rows)))}
        self.assertEqual(assignment, reversed_assignment)
        self.assertNotIn('split', rows[0])

    def test_unknown_identity_blocks_split(self):
        with self.assertRaises(ValueError):
            grouped_split([dict(group_id=None)])
        with self.assertRaises(ValueError):
            grouped_split([dict(group_id='a'), dict(group_id='b')])

    def test_duplicate_pixels_across_groups_block_split(self):
        with self.assertRaisesRegex(ValueError, 'Identical'):
            grouped_split([dict(group_id=str(i), pixel_sha256='same') for i in range(3)])

    def test_unsafe_paths(self):
        for name in ('../outside.jpg', '/outside.jpg', 'a/../../x', 'a\\b.jpg', 'C:/x'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_member(name)

    def test_audit_preserves_labels_and_missing_targets(self):
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'yellow').save(image, format='PNG')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'images.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for label in ('Green', 'Semi-ripe', 'Ripe', 'Overripe'):
                    archive.writestr(f'originals/{label}/1.png', image.getvalue())
            rows, report = audit_bananaimagebd(path)
        self.assertEqual(report['images'], 4)
        self.assertEqual(report['duplicate_images'], 3)
        self.assertEqual(report['conflicting_duplicate_groups'], 1)
        self.assertFalse(report['evaluation_ready'])
        self.assertTrue(all(row['days_remaining'] is None and row['fruit_id'] is None
                            and row['split'] == 'unassigned' for row in rows))
        self.assertEqual({row['ripeness_stage'] for row in rows},
                         {'unripe', 'early_ripe', 'ripe', 'overripe'})

    def test_download_integrity_and_cache(self):
        blob = b'archive payload'
        source = dict(archive_url='https://example.invalid/data', archive_bytes=len(blob),
                      sha256=hashlib.sha256(blob).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data.zip'
            with patch('fruitsavor.data.urlopen', return_value=io.BytesIO(blob)) as request:
                download(source, target)
                download(source, target)
                self.assertEqual(request.call_count, 1)
            target.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Checksum'):
                download(source, target)
            target.unlink()
            with patch('fruitsavor.data.urlopen', return_value=io.BytesIO(b'x' * len(blob))):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    download(source, target)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_corrupt_image_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Ripe/bad.jpg', b'not an image')
            with self.assertRaises(OSError):
                audit_bananaimagebd(path)


if __name__ == '__main__':
    unittest.main()
