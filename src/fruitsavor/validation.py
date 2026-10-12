"""Audit a curated manifest before independent-fruit ripeness evaluation."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


SPLITS = {'train', 'validation', 'test'}


def audit_manifest(rows):
    issues = defaultdict(list)
    selected = [r for r in rows if r.get('curation_status') == 'eligible_for_exploration']
    excluded = len(rows) - len(selected)
    groups, hashes, members = defaultdict(set), defaultdict(list), set()
    counts = defaultdict(Counter)
    for row in selected:
        member = (row.get('dataset_id'), row.get('archive_member'))
        name = ':'.join(str(part or '') for part in member)
        if not all(isinstance(part, str) and part.strip() for part in member):
            issues['missing_image_identity'].append(name)
        if member in members:
            issues['duplicate_manifest_entry'].append(name)
        members.add(member)
        group = row.get('group_id')
        evidence = row.get('group_evidence')
        if not isinstance(group, str) or not group.strip():
            issues['missing_group'].append(name)
        elif not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ('source', 'reviewer', 'reviewed_at')
        ) or evidence.get('basis') not in {'specimen', 'collection_batch'}:
            issues['missing_group_evidence'].append(name)
        split = row.get('split')
        if split not in SPLITS:
            issues['unassigned_split'].append(name)
        if isinstance(group, str) and group.strip():
            groups[group].add(split)
        label = row.get('ripeness_stage')
        if not label:
            issues['missing_ripeness_label'].append(name)
        else:
            counts[str(split)][label] += 1
        if row.get('is_augmented') is not False:
            issues['original_image_status_unverified'].append(name)
        for field in ('sha256', 'pixel_sha256'):
            digest = row.get(field)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                issues['missing_or_invalid_hash'].append(f'{name}:{field}')
            else:
                hashes[field, digest].append((name, group, split, label))
    for group, splits in groups.items():
        if len(splits) > 1:
            issues['group_crosses_splits'].append(group)
    for (field, digest), items in hashes.items():
        if len({item[1] for item in items}) > 1:
            issues['duplicate_crosses_groups'].append(f'{field}:{digest}')
        if len({item[2] for item in items}) > 1:
            issues['duplicate_crosses_splits'].append(f'{field}:{digest}')
        if len({item[3] for item in items}) > 1:
            issues['conflicting_duplicate_labels'].append(f'{field}:{digest}')
    labels = {label for counter in counts.values() for label in counter}
    for split in sorted(SPLITS):
        if not counts[split]:
            issues['empty_split'].append(split)
        for label in sorted(labels - set(counts[split])):
            issues['missing_class_in_split'].append(f'{split}:{label}')
    if not selected:
        issues['no_eligible_images'].append('manifest')
    return {
        'schema_version': 1,
        'images': len(rows), 'eligible_images': len(selected), 'excluded_images': excluded,
        'groups': len(groups), 'class_counts_by_split': dict(counts),
        'structural_checks_passed': not issues,
        'evaluation_ready': False,
        'limitations': ['Evidence fields require human verification; this audit does not establish specimen independence.',
                       'Class coverage does not establish adequate sample size or representative evaluation.',
                       'No shelf-life targets are derived or validated.'],
        'issue_counts': {key: len(value) for key, value in sorted(issues.items())},
        'issues': dict(sorted(issues.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('data/processed/bananaimagebd/curated.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('reports/dataset-validation.json'))
    args = parser.parse_args(argv)
    content = args.manifest.read_bytes()
    rows = [json.loads(line) for line in content.splitlines() if line.strip()]
    report = audit_manifest(rows)
    report['manifest_sha256'] = hashlib.sha256(content).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'issues'}, indent=2))
    return 0 if report['structural_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
