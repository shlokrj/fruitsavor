import io
import unittest
from unittest.mock import patch
import zipfile

from fruitsavor.remote_zip import RemoteZipReader


class Response(io.BytesIO):
    status = 206

    def __init__(self, content, content_range, etag='"version1"'):
        super().__init__(content)
        self.headers = {'Content-Range': content_range, 'ETag': etag}


class RemoteZipTests(unittest.TestCase):
    def test_reads_zip_with_exact_ranges(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('fruit/day0.jpg', b'image fixture')
        data = content.getvalue()
        def request(req, timeout):
            start, end = map(int, req.get_header('Range')[6:].split('-'))
            return Response(data[start:end+1], f'bytes {start}-{end}/{len(data)}')
        with patch('fruitsavor.remote_zip.urlopen', side_effect=request):
            with RemoteZipReader('https://example.org/data.zip', len(data)) as reader:
                with zipfile.ZipFile(reader) as archive:
                    self.assertEqual(archive.read('fruit/day0.jpg'), b'image fixture')

    def test_rejects_ignored_range_truncation_and_changed_version(self):
        for response in (Response(b'ab', 'bytes 0-1/99'), Response(b'a', 'bytes 0-1/2'),
                         Response(b'ab', 'bytes 0-1/2', '"version2"')):
            reader = RemoteZipReader('https://example.org', 2)
            reader.etag = '"version1"'
            with patch('fruitsavor.remote_zip.urlopen', return_value=response), self.assertRaises(ValueError):
                reader.read(2)
            self.assertEqual(reader.tell(), 0)

    def test_bounds_prevent_large_download(self):
        reader = RemoteZipReader('https://example.org', 100, max_read=10)
        with self.assertRaises(ValueError):
            reader.read()
        with self.assertRaises(ValueError):
            reader.seek(-1)
        reader.seek(200)
        self.assertEqual(reader.read(), b'')
