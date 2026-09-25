"""Fixed image-level segmentation audit subsets, without inferred specimen IDs."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

from PIL import Image


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def select_samples(rows, pairs, features, excluded, per_class=4, seed=42):
    if per_class < 1:
        raise ValueError('At least one image per class per partition is required')
    eligible = {r['archive_member']: r for r in rows if r['curation_status'] == 'eligible_for_exploration'}
    parent = {name: name for name in eligible}

    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for pair in pairs:
        if pair['left'] in parent and pair['right'] in parent:
            a, b = find(pair['left']), find(pair['right'])
            parent[max(a, b)] = min(a, b)
    components = {name: find(name) for name in parent}
    excluded_components = {components[name] for name in excluded if name in components}
    selected, evaluation_components = [], set()
    labels = sorted({row['source_label'] for row in eligible.values()})
    # Evaluation is selected first, excluding previously viewed candidate families.
    for partition in ('evaluation', 'development'):
        for label in labels:
            pool = [r for name, r in eligible.items() if r['source_label'] == label
                    and (components[name] not in excluded_components if partition == 'evaluation'
                         else components[name] not in evaluation_components)]
            pool.sort(key=lambda r: digest(f"{seed}:{r['sha256']}".encode()))
            chosen = []
            used = set()
            # Include different image difficulties where available, then fill deterministically.
            for category in ('background_outside_plain_light_assumption', 'foreground_near_image_edge',
                             'small', 'large', 'any'):
                ordered = list(pool)
                if category in {'small', 'large'}:
                    ordered.sort(key=lambda r: features.get(r['archive_member'], {}).get('features', {}).get('foreground_fraction', 0),
                                 reverse=category == 'large')
                for row in ordered:
                    name = row['archive_member']
                    if name in used or len(chosen) >= per_class:
                        continue
                    if category not in {'small', 'large', 'any'} and category not in features.get(name, {}).get('warnings', []):
                        continue
                    chosen.append(dict(row, partition=partition, selection_reason=category,
                                       similarity_component=components[name]))
                    used.add(name)
                    if category != 'any':
                        break
                if len(chosen) == per_class:
                    break
            if len(chosen) != per_class:
                raise ValueError(f'Not enough eligible images for {partition}/{label}')
            selected.extend(chosen)
            if partition == 'evaluation':
                evaluation_components.update(components[r['archive_member']] for r in chosen)
    return sorted(selected, key=lambda r: (r['partition'], r['source_label'], r['sha256']))


def prepare(data_dir: Path, output: Path, protocol: Path):
    if output.exists():
        raise ValueError('Project already exists; use a new output path to preserve annotations')
    rules = json.loads(protocol.read_text())
    base = data_dir / 'processed/bananaimagebd'
    rows = [json.loads(line) for line in (base / 'curated.jsonl').read_text().splitlines()]
    pairs = json.loads((base / 'review.json').read_text())['candidates']
    feature_path = Path(rules['feature_manifest'])
    features = {r['archive_member']: r for r in
                (json.loads(line) for line in feature_path.read_text().splitlines())}
    selected = select_samples(rows, pairs, features, rules['exclude_from_evaluation'],
                              rules['per_class_per_partition'], rules['seed'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        staging = Path(staging)
        (staging / 'images').mkdir()
        items = []
        archive_path = data_dir / 'raw/bananaimagebd/originals.zip'
        with zipfile.ZipFile(archive_path) as archive:
            for row in selected:
                blob = archive.read(row['archive_member'])
                if digest(blob) != row['sha256']:
                    raise ValueError('Source image differs from curated manifest')
                with Image.open(io.BytesIO(blob)) as source:
                    image = source.convert('RGB')
                    clean = Image.frombytes('RGB', image.size, image.tobytes())
                image_path = staging / 'images' / f"{row['sha256']}.png"
                clean.save(image_path)
                items.append(dict(id=row['sha256'], image_sha256=digest(image_path.read_bytes()),
                                  width=clean.width, height=clean.height,
                                  archive_member=row['archive_member'], source_label=row['source_label'],
                                  partition=row['partition'], selection_reason=row['selection_reason'],
                                  similarity_component=row['similarity_component']))
        manifest = dict(schema_version=1, protocol_sha256=digest(protocol.read_bytes()),
                        source_manifest_sha256=digest((base / 'curated.jsonl').read_bytes()),
                        feature_manifest_sha256=digest(feature_path.read_bytes()),
                        seed=rules['seed'], items=items,
                        scope='Selected image-level segmentation audit; specimen independence is unknown.',
                        selection='Class-balanced challenge subset, not a random population sample.')
        (staging / 'project.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staging.rename(output)
    return manifest
