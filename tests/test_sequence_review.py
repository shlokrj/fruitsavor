import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from fruitsavor.sequence_review import create_review


class SequenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'samples'
        self.source.mkdir()
        data = io.BytesIO()
        Image.new('RGB', (20, 40), 'yellow').save(data, format='PNG')
        blob = data.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        (self.source / (digest + '.bin')).write_bytes(blob)
        self.samples = [dict(archive_member=f'root/Banana_ID_001/Day_{day}.jpg', sha256=digest,
                             bytes=len(blob), dimensions=[20, 40]) for day in reversed(range(8))]
        self.samples.append(dict(self.samples[0], archive_member='root/Banana_ID_001/IMG123.jpg'))
        (self.source / 'manifest.json').write_text(json.dumps(dict(dataset_id='test', samples=self.samples)))

    def test_pagination_order_and_traceability_without_source_edits(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        output = self.root / 'review'
        result = create_review(self.source, output)
        self.assertEqual(len(result['pages']), 2)
        self.assertTrue(result['pages'][0]['members'][0].endswith('Day_0.jpg'))
        self.assertTrue(result['pages'][1]['members'][0].endswith('IMG123.jpg'))
        self.assertEqual(result['review_status'], 'pending')
        self.assertFalse(result['evaluation_ready'])
        for page in result['pages']:
            file = output / page['file']
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), page['sha256'])
            with Image.open(file) as image:
                self.assertEqual(image.width, 1440)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        with self.assertRaises(FileExistsError):
            create_review(self.source, output)

    def test_source_output_and_corrupt_original_are_rejected(self):
        with self.assertRaises(ValueError):
            create_review(self.source, self.source / 'review')
        next(self.source.glob('*.bin')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            create_review(self.source, self.root / 'review')
        self.assertFalse((self.root / 'review').exists())
