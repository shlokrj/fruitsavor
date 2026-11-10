"""Audit retained temporal samples without inferring labels or specimen identity."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from PIL import Image


def audit_samples(directory):
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    rows, seen = [], set()
    for sample in manifest['samples']:
        digest = sample['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Invalid sample digest')
        member = sample['archive_member']
        if member in seen:
            raise ValueError('Duplicate sample member')
        seen.add(member)
        path = directory / (digest + '.bin')
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != digest or len(blob) != sample['bytes']:
            raise ValueError('Retained sample differs from manifest')
        with Image.open(path) as image:
            image.load()
            if list(image.size) != sample['dimensions']:
                raise ValueError('Sample dimensions differ from manifest')
            pixel_hash = hashlib.sha256(f'{image.width}x{image.height}:'.encode() + image.convert('RGB').tobytes()).hexdigest()
            exif = image.getexif()
            # EXIF timestamps are source claims, not a verified capture chronology.
            captured = exif.get(306)
            try:
                original = exif.get_ifd(34665).get(36867)
            except (KeyError, TypeError, ValueError):
                original = None
        parts = PurePosixPath(member)
        candidates = [part for part in parts.parts[:-1] if re.fullmatch(r'Banana_ID_\d+', part)]
        day_match = re.fullmatch(r'Day_([0-7])\.[jJ][pP][gG]', parts.name)
        rows.append({'archive_member': member, 'sha256': digest, 'pixel_sha256': pixel_hash,
                     'folder_candidate': candidates[0] if len(candidates) == 1 else None,
                     'filename_day': int(day_match[1]) if day_match else None,
                     'exif_datetime': str(captured) if captured else None,
                     'exif_datetime_original': str(original) if original else None,
                     'ripeness_stage': None, 'days_remaining': None, 'group_id': None})
    groups = defaultdict(list)
    duplicates = defaultdict(list)
    for row in rows:
        groups[row['folder_candidate'] or 'unknown'].append(row)
        duplicates[row['pixel_sha256']].append(row['archive_member'])
    chronology = []
    for group, items in sorted(groups.items()):
        dated = []
        for row in items:
            try:
                stamp = datetime.strptime(row['exif_datetime_original'] or '', '%Y:%m:%d %H:%M:%S')
            except ValueError:
                continue
            dated.append((row, stamp))
        baseline = next((stamp for row, stamp in dated if row['filename_day'] == 0), None)
        if baseline is not None:
            for row, stamp in dated:
                elapsed = (stamp.date() - baseline.date()).days
                if row['filename_day'] is not None and elapsed != row['filename_day']:
                    chronology.append({'archive_member': row['archive_member'],
                                       'filename_day': row['filename_day'],
                                       'exif_calendar_days_from_day0': elapsed})
    return {'dataset_id': manifest['dataset_id'], 'images': len(rows),
            'sample_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'full_archive_checksum_verified': False, 'specimen_identity_verified': False,
            'evaluation_ready': False,
            'folders': {group: {'images': len(items),
                                'missing_filename_days': sorted(set(range(8)) - {r['filename_day'] for r in items}),
                                'duplicate_filename_days': sorted({r['filename_day'] for r in items if r['filename_day'] is not None and sum(x['filename_day'] == r['filename_day'] for x in items) > 1}),
                                'unassigned_day_members': [r['archive_member'] for r in items if r['filename_day'] is None]}
                        for group, items in sorted(groups.items())},
            'filename_exif_day_disagreements': chronology,
            'exact_pixel_duplicates': [members for members in duplicates.values() if len(members) > 1],
            'samples': rows,
            'limitations': ['Days are parsed from filenames only; missing days are not inferred.',
                           'Folder membership and EXIF do not verify specimen identity or capture chronology.',
                           'Duplicate checks cover only retained samples; no ripeness or shelf-life labels assigned.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.directory.resolve()):
        parser.error('Write the report outside the retained sample directory')
    report = audit_samples(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'samples'}, indent=2))


if __name__ == '__main__':
    main()
