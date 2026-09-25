"""Verified data acquisition and conservative evaluation preparation."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from urllib.request import Request, urlopen
import zipfile

from PIL import Image


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(source: dict, target: Path) -> Path:
    """Cache only archives matching the version-pinned publisher checksum."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256(target) != source['sha256']:
            raise ValueError(f'Checksum mismatch for existing archive: {target}')
        return target
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            request = Request(source['archive_url'], headers={'User-Agent': 'FruitSavor/0.1 (dataset research)'})
            with urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, temp)
        if temp_path.stat().st_size != source['archive_bytes']:
            raise ValueError('Archive size differs from publisher metadata')
        if sha256(temp_path) != source['sha256']:
            raise ValueError('Archive checksum differs from publisher metadata')
        temp_path.replace(target)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return target


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe archive member: {name}')
    return path


LABELS = {'green': 'unripe', 'semi-ripe': 'early_ripe',
          'ripe': 'ripe', 'overripe': 'overripe'}


def audit_bananaimagebd(archive: Path) -> tuple[list[dict], dict]:
    """Read originals without extraction; preserve source labels and unknown IDs."""
    rows = []
    names = set()
    with zipfile.ZipFile(archive) as bundle:
        for member in sorted(bundle.infolist(), key=lambda item: item.filename):
            path = safe_member(member.filename)
            if member.is_dir() or path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
                continue
            if '__MACOSX' in path.parts or path.name.startswith('._'):
                continue
            if member.filename in names:
                raise ValueError('Duplicate archive member name')
            names.add(member.filename)
            if member.file_size > 50_000_000:
                raise ValueError('Unexpectedly large image')
            label = path.parent.name
            if label.lower() not in LABELS:
                raise ValueError(f'Unrecognized label directory: {label}')
            blob = bundle.read(member)
            with Image.open(io.BytesIO(blob)) as image:
                image.verify()
            with Image.open(io.BytesIO(blob)) as image:
                image.load()
                width, height = image.size
                pixel_hash = hashlib.sha256(
                    f'{width}x{height}:'.encode() + image.convert('RGB').tobytes()
                ).hexdigest()
            rows.append(dict(
                dataset_id='bananaimagebd', fruit_type='banana',
                archive_member=member.filename, source_label=label,
                ripeness_stage=LABELS[label.lower()], fruit_id=None,
                group_id=None, captured_at=None, days_remaining=None,
                split='unassigned', sha256=hashlib.sha256(blob).hexdigest(),
                pixel_sha256=pixel_hash, width=width, height=height,
            ))
    if not rows:
        raise ValueError('No supported images found')
    counts = Counter(row['pixel_sha256'] for row in rows)
    duplicate_groups = [
        [dict(archive_member=row['archive_member'], source_label=row['source_label'])
         for row in rows if row['pixel_sha256'] == digest]
        for digest, count in counts.items() if count > 1
    ]
    report = dict(
        dataset_id='bananaimagebd', images=len(rows),
        classes=dict(sorted(Counter(row['source_label'] for row in rows).items())),
        unique_decoded_images=len(counts),
        duplicate_images=sum(count - 1 for count in counts.values()),
        duplicate_groups=duplicate_groups,
        conflicting_duplicate_groups=sum(
            len({row['source_label'] for row in group}) > 1 for group in duplicate_groups
        ),
        evaluation_ready=False,
        blockers=['Specimen identities not verified.',
                  'Near-duplicate and collection-group review pending.',
                  'No observed usable-life endpoints.'],
    )
    return rows, report


def grouped_split(rows: list[dict], seed: int = 42) -> list[dict]:
    """Deterministic ~70/15/15 group split; require externally audited IDs.

    Groups must include dataset namespace, or link shared specimens across sources.
    Run on originals before augmentation. This is not a near-duplicate detector.
    """
    if not rows or any(not row.get('group_id') for row in rows):
        raise ValueError('Every image needs a verified group_id')
    groups = sorted({row['group_id'] for row in rows}, key=lambda group:
                    hashlib.sha256(f'{seed}:{group}'.encode()).hexdigest())
    if len(groups) < 3:
        raise ValueError('At least three independent groups are required')
    seen = {}
    for row in rows:
        for field in ('sha256', 'pixel_sha256'):
            if row.get(field):
                key = (field, row[field])
                if key in seen and seen[key] != row['group_id']:
                    raise ValueError('Identical images cross group boundaries; audit group IDs')
                seen[key] = row['group_id']
    holdout = max(1, round(len(groups) * 0.15))
    mapping = {group: ('test' if index < holdout else
                      'validation' if index < 2 * holdout else 'train')
               for index, group in enumerate(groups)}
    return [dict(row, split=mapping[row['group_id']]) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare-bananaimagebd'])
    parser.add_argument('--catalog', type=Path, default=Path('datasets/catalog.json'))
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    args = parser.parse_args()
    sources = json.loads(args.catalog.read_text())['datasets']
    source = next(item for item in sources if item['id'] == 'bananaimagebd')
    archive = download(source, args.data_dir / 'raw' / 'bananaimagebd' / 'originals.zip')
    rows, report = audit_bananaimagebd(archive)
    report.update(archive_sha256=sha256(archive), source_url=source['source_url'])
    output = args.data_dir / 'processed' / 'bananaimagebd'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'manifest.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
