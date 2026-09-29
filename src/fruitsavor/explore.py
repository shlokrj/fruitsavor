"""Run visual feature exploration on curated originals; no predictive metrics."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

from fruitsavor.vision import METHOD, analyze, overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--output', type=Path, default=Path('reports/color-baseline'))
    args = parser.parse_args()
    rows = [json.loads(line) for line in
            (args.data_dir / 'processed/bananaimagebd/curated.jsonl').read_text().splitlines()]
    rows = [row for row in rows if row['curation_status'] == 'eligible_for_exploration']
    if not rows:
        raise ValueError('No eligible images; run dataset preparation and review first')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'masks').mkdir(exist_ok=True)
    cv2.setNumThreads(1)
    results = []
    with zipfile.ZipFile(args.data_dir / 'raw/bananaimagebd/originals.zip') as archive:
        for row in rows:
            blob = archive.read(row['archive_member'])
            if hashlib.sha256(blob).hexdigest() != row['sha256']:
                raise ValueError('Image differs from the curated manifest')
            with Image.open(io.BytesIO(blob)) as source:
                image = source.convert('RGB')
            result = analyze(image)
            identifier = row['sha256']
            Image.fromarray(result.mask.astype('uint8') * 255).save(args.output / 'masks' / f'{identifier}.png')
            results.append(dict(archive_member=row['archive_member'], sha256=identifier,
                                source_label=row['source_label'], features=result.features,
                                warnings=result.warnings, status='review_required' if result.warnings else 'unvalidated',
                                mask_file=f'masks/{identifier}.png'))
        # Fixed class representatives, plus flagged low-area cases; no test-set selection.
        selected = []
        for label in sorted({row['source_label'] for row in results}):
            group = [row for row in results if row['source_label'] == label]
            selected.extend([group[0], group[len(group)//2], group[-1]])
        flagged = sorted([row for row in results if row['warnings']],
                         key=lambda row: row['features'].get('foreground_fraction', 0))
        selected.extend(flagged[:4])
        canvas = Image.new('RGB', (1024, ((len(selected)+1)//2) * 290), 'white')
        draw = ImageDraw.Draw(canvas)
        for index, row in enumerate(selected):
            with Image.open(io.BytesIO(archive.read(row['archive_member']))) as source:
                image = source.convert('RGB')
            mask = np.asarray(Image.open(args.output / row['mask_file'])) > 0
            x, y = (index % 2) * 512, (index // 2) * 290
            for offset, photo in [(0, image), (256, overlay(image, mask))]:
                photo.thumbnail((256, 250))
                canvas.paste(photo, (x + offset, y + 20))
            draw.text((x+3, y+3), f"{row['source_label']} / {Path(row['archive_member']).name}", fill='black')
            draw.text((x+3, y+272), ', '.join(row['warnings']) or 'unvalidated mask', fill='black')
        canvas.save(args.output / 'overlays.jpg')
    (args.output / 'features.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in results))
    summary = dict(images=len(results), images_with_warnings=sum(bool(row['warnings']) for row in results),
                   warning_counts=dict(Counter(warning for row in results for warning in row['warnings'])),
                   images_without_features=sum(not row['features'] for row in results),
                   method=METHOD,
                   versions=dict(opencv=cv2.__version__, numpy=np.__version__),
                   segmentation_accuracy=None, classification_accuracy=None, shelf_life_model=None,
                   limitations=['No annotated reference masks: segmentation accuracy is unknown.',
                                'HSV color bins are heuristics, not calibrated browning measurements.',
                                'All results are exploratory; no specimen-independent evaluation.'])
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
