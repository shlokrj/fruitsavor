import io
import hashlib
import unittest
import zipfile
from PIL import Image
from fruitsavor.review import review_dataset


class ReviewTests(unittest.TestCase):
    def fixture(self):
        buffer = io.BytesIO()
        photo = io.BytesIO()
        Image.new('RGB', (32, 32), 'yellow').save(photo, format='PNG')
        blob = photo.getvalue()
        rows = [dict(archive_member=f'{i}.png', source_label=label,
                     sha256=hashlib.sha256(blob).hexdigest(), pixel_sha256='same',
                     fruit_id=None, group_id=None, split='unassigned')
                for i, label in enumerate(('Ripe', 'Semi-ripe'))]
        with zipfile.ZipFile(buffer, 'w') as bundle:
            for row in rows:
                bundle.writestr(row['archive_member'], blob)
        buffer.seek(0)
        return rows, buffer

    def test_conflicting_duplicates_quarantine_both_without_relabeling(self):
        rows, buffer = self.fixture()
        with zipfile.ZipFile(buffer) as bundle:
            result, _ = review_dataset(rows, bundle)
        self.assertTrue(all(row['curation_status'] == 'quarantined_label_conflict' for row in result))
        for before, after in zip(rows, result):
            self.assertTrue(all(after[key] == value for key, value in before.items()))
        self.assertNotIn('curation_status', rows[0])

    def test_same_label_duplicates_keep_one(self):
        rows, buffer = self.fixture()
        rows[1]['source_label'] = 'Ripe'
        with zipfile.ZipFile(buffer) as bundle:
            result, _ = review_dataset(rows, bundle)
        self.assertEqual([row['curation_status'] for row in result],
                         ['eligible_for_exploration', 'excluded_exact_duplicate'])

    def test_modified_image_is_rejected(self):
        rows, buffer = self.fixture()
        rows[0]['sha256'] = 'wrong'
        with zipfile.ZipFile(buffer) as bundle, self.assertRaises(ValueError):
            review_dataset(rows, bundle)
