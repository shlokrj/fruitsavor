"""Create traceable visual review sheets from verified retained image samples."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .temporal import audit_samples


CELL_WIDTH, CELL_HEIGHT = 360, 390
COLUMNS, PAGE_SIZE = 4, 8
HEADER_HEIGHT = 86


def clipped(draw, value, font, width):
    value = str(value)
    if draw.textlength(value, font=font) <= width:
        return value
    while value and draw.textlength(value + '...', font=font) > width:
        value = value[:-1]
    return value + '...'


def create_review(directory, output):
    directory, output = Path(directory), Path(output)
    if output.resolve().is_relative_to(directory.resolve()):
        raise ValueError('Review output must be outside the source directory')
    report = audit_samples(directory)
    if not report['samples']:
        raise ValueError('No samples to review')
    # Never overwrite an earlier review or mix outputs from different manifests.
    output.mkdir(parents=True, exist_ok=False)
    groups = defaultdict(list)
    for row in report['samples']:
        groups[row['folder_candidate'] or 'unknown'].append(row)
    font = ImageFont.load_default(size=15)
    title_font = ImageFont.load_default(size=24)
    flagged = {row['archive_member'] for row in report['filename_exif_day_disagreements']}
    package = {'dataset_id': report['dataset_id'],
               'sample_manifest_sha256': report['sample_manifest_sha256'],
               'evaluation_ready': False, 'review_status': 'pending', 'pages': [],
               'limitation': 'Visual review aid only. Source dates and folder identities remain unverified; no labels are assigned.'}
    for group, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (row['filename_day'] is None,
                                   row['filename_day'] if row['filename_day'] is not None else 0,
                                   row['archive_member']))
        for page_index, offset in enumerate(range(0, len(rows), PAGE_SIZE), 1):
            page_rows = rows[offset:offset + PAGE_SIZE]
            grid_rows = (len(page_rows) + COLUMNS - 1) // COLUMNS
            sheet = Image.new('RGB', (CELL_WIDTH * COLUMNS, HEADER_HEIGHT + CELL_HEIGHT * grid_rows), '#f5f5f3')
            draw = ImageDraw.Draw(sheet)
            draw.text((18, 14), f'{group} / page {page_index}', fill='#202820', font=title_font)
            draw.text((18, 47), 'Review pending. Filename days and camera times are source claims; specimen identity is unverified.', fill='#505750', font=font)
            for index, row in enumerate(page_rows):
                x, y = (index % COLUMNS) * CELL_WIDTH, HEADER_HEIGHT + (index // COLUMNS) * CELL_HEIGHT
                with Image.open(directory / (row['sha256'] + '.bin')) as original:
                    image = ImageOps.exif_transpose(original).convert('RGB')
                    image.thumbnail((CELL_WIDTH - 24, 274), Image.Resampling.LANCZOS)
                    sheet.paste(image, (x + (CELL_WIDTH - image.width) // 2, y + (274 - image.height) // 2))
                name = PurePosixPath(row['archive_member']).name
                lines = [clipped(draw, name, font, CELL_WIDTH - 24),
                         'Filename day: ' + (str(row['filename_day']) if row['filename_day'] is not None else 'unassigned'),
                         'Camera: ' + (row['exif_datetime_original'] or 'not recorded'),
                         'SHA-256: ' + row['sha256'][:16]]
                for line_index, line in enumerate(lines):
                    draw.text((x + 12, y + 282 + line_index * 21), clipped(draw, line, font, CELL_WIDTH - 24), fill='#303830', font=font)
                if row['archive_member'] in flagged:
                    draw.text((x + 12, y + 367), 'Filename / camera day mismatch', fill='#9b3c23', font=font)
            filename = f'{group}-{page_index:02d}.png'
            sheet.save(output / filename)
            package['pages'].append({'file': filename, 'sha256': hashlib.sha256((output / filename).read_bytes()).hexdigest(),
                                     'members': [row['archive_member'] for row in page_rows]})
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'review.json').write_text(json.dumps(package, indent=2) + '\n')
    return package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = create_review(args.directory, args.output)
    print(f"Created {len(result['pages'])} review sheets; review remains pending")


if __name__ == '__main__':
    main()
