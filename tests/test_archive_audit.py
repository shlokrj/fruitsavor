from pathlib import Path
import tempfile
import unittest
import zipfile

from fruitsavor.archive_audit import inventory


class ArchiveAuditTests(unittest.TestCase):
    def test_inventory_does_not_infer_labels_or_require_decodable_images(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'example.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('root/Banana_ID_001/day0.JPG', b'not an image')
                bundle.writestr('root/day7.png', b'also not an image')
                bundle.writestr('root/notes.txt', b'notes')
            report = inventory(archive)
            self.assertEqual(report['images'], 2)
            self.assertEqual(report['specimen_folder_candidates'], ['Banana_ID_001'])
            self.assertEqual(report['images_without_specimen_folder'], 1)
            self.assertFalse(report['evaluation_ready'])
            self.assertEqual(len(report['archive_sha256']), 64)
            self.assertEqual(list(Path(folder).iterdir()), [archive])

    def test_unsafe_paths_rejected(self):
        for name in ('../bad.jpg', '/bad.jpg', 'folder\\bad.jpg'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(name, b'x')
                with self.assertRaises(ValueError):
                    inventory(archive)
