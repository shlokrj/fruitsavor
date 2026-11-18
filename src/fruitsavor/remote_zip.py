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


def save_manifest(directory, report):
    import json
    temporary = directory / 'manifest.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(directory / 'manifest.json')


def retrieve_samples(source, members, output, resume=False):
    import hashlib
    import json
    import re
    import zipfile
    from PIL import Image
    from .data import safe_member

    identity = {key: source[key] for key in ('id', 'archive_url', 'archive_bytes', 'sha256')}
    if resume:
        report = json.loads((output / 'manifest.json').read_text())
        if report.get('archive_identity') != identity:
            raise ValueError('Resume source differs from the saved archive identity')
        seen = set()
        for sample in report['samples']:
            digest = sample['sha256']
            if not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Invalid retained sample digest')
            blob = (output / (digest + '.bin')).read_bytes()
            if hashlib.sha256(blob).hexdigest() != digest or len(blob) != sample['bytes']:
                raise ValueError('Retained sample differs from manifest')
            if sample['archive_member'] in seen:
                raise ValueError('Duplicate retained member')
            seen.add(sample['archive_member'])
    else:
        output.mkdir(parents=True, exist_ok=False)
        report = {'dataset_id': source['id'], 'source_url': source['source_url'],
                  'archive_identity': identity, 'full_archive_checksum_verified': False,
                  'evaluation_ready': False, 'samples': []}
        seen = set()
        save_manifest(output, report)
    pending = list(dict.fromkeys(member for member in members if member not in seen))
    if not pending:
        return report
    with RemoteZipReader(source['archive_url'], source['archive_bytes']) as reader:
        reader.etag = report.get('etag')
        with zipfile.ZipFile(reader) as archive:
            names = archive.namelist()
            # Validate the full selection before downloading image bodies.
            for member in pending:
                safe_member(member)
                if names.count(member) != 1:
                    raise ValueError('Member missing or ambiguous')
                info = archive.getinfo(member)
                if info.file_size > 25 * 1024 * 1024 or info.compress_size > reader.max_read:
                    raise ValueError('Sample exceeds size limit')
            for member in pending:
                blob = archive.read(member)
                with Image.open(io.BytesIO(blob)) as image:
                    image.load()
                    dimensions = list(image.size)
                digest = hashlib.sha256(blob).hexdigest()
                destination = output / (digest + '.bin')
                temporary = output / (digest + '.part')
                temporary.write_bytes(blob)
                temporary.replace(destination)
                report['samples'].append({'archive_member': member, 'sha256': digest,
                                          'bytes': len(blob), 'dimensions': dimensions,
                                          'zip_crc_verified': True, 'decoded': True})
                report['etag'] = reader.etag
                save_manifest(output, report)
    return report


def main(argv=None):
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path('datasets/catalog.json'))
    parser.add_argument('--dataset', default='banana-day0-7')
    parser.add_argument('--member', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    source = next(row for row in json.loads(args.catalog.read_text())['datasets'] if row['id'] == args.dataset)
    retrieve_samples(source, args.member, args.output, args.resume)


if __name__ == '__main__':
    main()
