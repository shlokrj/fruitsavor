import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
from fruitsavor.remote_zip import retrieve_samples
from test_remote_zip import Response


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'samples'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            for day in range(2):
                image = io.BytesIO()
                Image.new('RGB', (8, 8), ('yellow', 'green')[day]).save(image, format='PNG')
                z.writestr(f'Banana_ID_001/Day_{day}.png', image.getvalue())
        self.data = archive.getvalue()
        self.source = dict(id='test', source_url='https://example.org', archive_url='https://example.org/data.zip',
                           archive_bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())
        self.members = [f'Banana_ID_001/Day_{day}.png' for day in range(2)]

    def request(self, req, timeout):
        start, end = map(int, req.get_header('Range')[6:].split('-'))
        return Response(self.data[start:end+1], f'bytes {start}-{end}/{len(self.data)}')

    def test_resume_preserves_previous_sample_and_skips_completed_download(self):
        with patch('fruitsavor.remote_zip.urlopen', side_effect=self.request):
            first = retrieve_samples(self.source, self.members[:1], self.output)
            report = retrieve_samples(self.source, self.members, self.output, resume=True)
        self.assertEqual(len(report['samples']), 2)
        self.assertEqual(report['samples'][0], first['samples'][0])
        with patch('fruitsavor.remote_zip.urlopen') as network:
            retrieve_samples(self.source, self.members, self.output, resume=True)
            network.assert_not_called()

    def test_corruption_and_source_change_block_resume(self):
        with patch('fruitsavor.remote_zip.urlopen', side_effect=self.request):
            report = retrieve_samples(self.source, self.members[:1], self.output)
        with self.assertRaises(ValueError):
            retrieve_samples(dict(self.source, sha256='changed'), self.members, self.output, True)
        (self.output / (report['samples'][0]['sha256'] + '.bin')).write_bytes(b'bad')
        with self.assertRaises(ValueError):
            retrieve_samples(self.source, self.members, self.output, True)

    def test_failure_keeps_checkpoint(self):
        original = zipfile.ZipFile.read
        def fail_second(archive, member, *args, **kwargs):
            if 'Day_1' in member:
                raise OSError('interrupted')
            return original(archive, member, *args, **kwargs)
        with patch('fruitsavor.remote_zip.urlopen', side_effect=self.request), patch.object(zipfile.ZipFile, 'read', fail_second):
            with self.assertRaises(OSError):
                retrieve_samples(self.source, self.members, self.output)
        report = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(len(report['samples']), 1)
        with patch('fruitsavor.remote_zip.urlopen', side_effect=self.request):
            self.assertEqual(len(retrieve_samples(self.source, self.members, self.output, True)['samples']), 2)
