"""Bounded HTTP-range access for exploratory ZIP inspection, not archive verification."""
import io
from urllib.request import Request, urlopen


class RemoteZipReader(io.RawIOBase):
    def __init__(self, url, size, max_read=16 * 1024 * 1024):
        super().__init__()
        if size <= 0 or max_read <= 0:
            raise ValueError('Size and read limit must be positive')
        self.url, self.size, self.max_read = url, size, max_read
        self.position = 0
        self.etag = None

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence not in (io.SEEK_SET, io.SEEK_CUR, io.SEEK_END):
            raise ValueError('Invalid seek mode')
        target = offset + (self.position if whence == io.SEEK_CUR else self.size if whence == io.SEEK_END else 0)
        if target < 0:
            raise ValueError('Negative seek')
        self.position = target
        return target

    def read(self, size=-1):
        if self.closed:
            raise ValueError('Reader is closed')
        size = max(0, min(self.size - self.position, self.size if size is None or size < 0 else size))
        if size > self.max_read:
            raise ValueError('Remote read exceeds configured limit')
        if not size:
            return b''
        start, end = self.position, self.position + size - 1
        headers = {'User-Agent': 'FruitSavor/0.2 (dataset audit)', 'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'}
        if self.etag:
            headers['If-Match'] = self.etag
        with urlopen(Request(self.url, headers=headers), timeout=30) as response:
            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{self.size}':
                raise ValueError('Server did not return the exact requested range')
            etag = response.headers.get('ETag')
            if self.etag and etag != self.etag:
                raise ValueError('Remote archive changed during inspection')
            if etag and not etag.startswith('W/'):
                self.etag = etag
            data = response.read(size + 1)
            if len(data) != size:
                raise ValueError('Incomplete or oversized range response')
        self.position += size
        return data


def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path
    import zipfile
    from PIL import Image
    from .data import safe_member

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('datasets/catalog.json'))
    parser.add_argument('--dataset', default='banana-day0-7')
    parser.add_argument('--member', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = next(row for row in json.loads(args.catalog.read_text())['datasets'] if row['id'] == args.dataset)
    # A new directory prevents mixing partial samples or overwriting earlier evidence.
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'dataset_id': args.dataset, 'source_url': source['source_url'],
              'full_archive_checksum_verified': False, 'evaluation_ready': False, 'samples': []}
    try:
        with RemoteZipReader(source['archive_url'], source['archive_bytes']) as reader, zipfile.ZipFile(reader) as archive:
            names = archive.namelist()
            for member in args.member:
                safe_member(member)
                if names.count(member) != 1:
                    raise ValueError('Member missing or ambiguous')
                info = archive.getinfo(member)
                if info.file_size > 25 * 1024 * 1024 or info.compress_size > reader.max_read:
                    raise ValueError('Sample exceeds size limit')
                blob = archive.read(info)  # zipfile checks member CRC.
                with Image.open(io.BytesIO(blob)) as image:
                    image.load()
                    dimensions = list(image.size)
                digest = hashlib.sha256(blob).hexdigest()
                (args.output / (digest + '.bin')).write_bytes(blob)
                report['samples'].append({'archive_member': member, 'sha256': digest,
                                           'bytes': len(blob), 'dimensions': dimensions,
                                           'zip_crc_verified': True, 'decoded': True})
            report['etag'] = reader.etag
    finally:
        (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
