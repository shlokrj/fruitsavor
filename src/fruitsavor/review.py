"""Conservative dataset curation; similarity candidates are not specimen IDs."""
import hashlib
import io
from collections import defaultdict

from PIL import Image, ImageOps


def difference_hash(image: Image.Image) -> int:
    """64-bit horizontal difference hash for review candidate retrieval only."""
    pixels = list(ImageOps.grayscale(image).resize((9, 8)).tobytes())
    bits = [pixels[y * 9 + x] > pixels[y * 9 + x + 1]
            for y in range(8) for x in range(8)]
    return sum(int(bit) << index for index, bit in enumerate(bits))


def review_dataset(rows, bundle, max_distance=4):
    if not 0 <= max_distance <= 64:
        raise ValueError('Hash distance must be between 0 and 64')
    groups = defaultdict(list)
    hashes = []
    for row in rows:
        blob = bundle.read(row['archive_member'])
        if hashlib.sha256(blob).hexdigest() != row['sha256']:
            raise ValueError('Image differs from its manifest')
        groups[row['pixel_sha256']].append(row)
        with Image.open(io.BytesIO(blob)) as image:
            hashes.append(difference_hash(image))
    conflicts = {digest for digest, members in groups.items()
                 if len({row['source_label'] for row in members}) > 1}
    candidates = []
    for i, left in enumerate(rows):
        for j in range(i + 1, len(rows)):
            right = rows[j]
            distance = (hashes[i] ^ hashes[j]).bit_count()
            if distance <= max_distance and left['pixel_sha256'] != right['pixel_sha256']:
                candidates.append(dict(left=left['archive_member'], right=right['archive_member'],
                                       hash_distance=distance))
    curated = []
    seen = set()
    for row in rows:
        digest = row['pixel_sha256']
        status = ('quarantined_label_conflict' if digest in conflicts else
                  'excluded_exact_duplicate' if digest in seen else 'eligible_for_exploration')
        seen.add(digest)
        curated.append(dict(row, curation_status=status))
    return curated, sorted(candidates, key=lambda pair: (pair['hash_distance'], pair['left'], pair['right']))


def main():
    import argparse
    import json
    from collections import Counter
    from pathlib import Path
    import zipfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    args = parser.parse_args()
    output = args.data_dir / 'processed' / 'bananaimagebd'
    rows = [json.loads(line) for line in (output / 'manifest.jsonl').read_text().splitlines()]
    with zipfile.ZipFile(args.data_dir / 'raw' / 'bananaimagebd' / 'originals.zip') as bundle:
        curated, candidates = review_dataset(rows, bundle)
    (output / 'curated.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in curated))
    report = dict(status_counts=dict(Counter(row['curation_status'] for row in curated)),
                  similarity_method='64-bit difference hash, Hamming distance <= 4',
                  candidate_pairs=len(candidates), candidates=candidates,
                  specimen_identity_verified=False,
                  limitation='Similarity is a review cue, not evidence of independent specimens.')
    (output / 'review.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'candidates'}, indent=2))


if __name__ == '__main__':
    main()
