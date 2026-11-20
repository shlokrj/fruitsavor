"""Find overlapping retained samples before combining dataset collections."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from .temporal import audit_samples


def audit_overlap(directories):
    paths = [Path(path).resolve() for path in directories]
    if len(paths) < 2 or len(paths) != len(set(paths)):
        raise ValueError('Provide at least two distinct sample directories')
    groups = defaultdict(list)
    sources = []
    for index, path in enumerate(paths):
        report = audit_samples(path)  # Reverify actual bytes and decoded pixels, not just metadata.
        sources.append({'index': index, 'directory': str(path), 'dataset_id': report['dataset_id'],
                        'manifest_sha256': report['sample_manifest_sha256'], 'images': report['images']})
        for row in report['samples']:
            item = {'collection': index, 'dataset_id': report['dataset_id'],
                    'archive_member': row['archive_member'], 'sha256': row['sha256']}
            groups[row['pixel_sha256']].append(item)
    overlap = [{'pixel_sha256': digest, 'occurrences': items}
               for digest, items in sorted(groups.items()) if len({item['collection'] for item in items}) > 1]
    return {'collections': sources, 'cross_collection_duplicate_groups': overlap,
            'overlapping_pixel_groups': len(overlap), 'evaluation_ready': False,
            'limitation': 'Exact decoded-pixel overlap only. No overlap does not prove distinct specimens or independence.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if any(args.output.resolve().is_relative_to(path.resolve()) for path in args.directories):
        parser.error('Output must be outside sample directories')
    report = audit_overlap(args.directories)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{report['overlapping_pixel_groups']} decoded-pixel groups overlap across collections")
    return 1 if report['overlapping_pixel_groups'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
