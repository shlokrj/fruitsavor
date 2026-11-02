"""Inventory a ZIP without extracting it or inferring specimen identities."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import zipfile

from .data import safe_member, sha256


IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


def inventory(archive):
    members = []
    names = set()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            path = safe_member(item.filename)
            if item.filename in names:
                raise ValueError('Duplicate archive member name')
            names.add(item.filename)
            if item.is_dir():
                continue
            members.append({'path': item.filename, 'bytes': item.file_size,
                            'compressed_bytes': item.compress_size,
                            'is_image': path.suffix.lower() in IMAGE_SUFFIXES,
                            'specimen_folder_candidate': next((part for part in path.parts[:-1]
                                if re.fullmatch(r'Banana_ID_\d+', part, re.IGNORECASE)), None)})
    images = [item for item in members if item['is_image']]
    return {'archive_sha256': sha256(archive), 'archive_bytes': Path(archive).stat().st_size,
            'files': len(members), 'images': len(images),
            'uncompressed_bytes': sum(item['bytes'] for item in members),
            'extensions': dict(sorted(Counter(Path(item['path']).suffix.lower() for item in members).items())),
            'specimen_folder_candidates': sorted({item['specimen_folder_candidate'] for item in images
                                                  if item['specimen_folder_candidate']}),
            'images_without_specimen_folder': sum(item['specimen_folder_candidate'] is None for item in images),
            'evaluation_ready': False,
            'limitations': ['ZIP directory inventory only; image decoding and CRC validation are not performed.',
                           'Folder names are candidates, not verified specimen identities.',
                           'No ripeness or shelf-life labels are inferred.'],
            'members': members}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.archive.resolve():
        parser.error('Output must not overwrite the archive')
    report = inventory(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'members'}, indent=2))


if __name__ == '__main__':
    main()
