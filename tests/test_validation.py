import contextlib
import io
import json
from pathlib import Path
import tempfile
import hashlib
import unittest

from fruitsavor.validation import audit_manifest, main


def fixture():
    return [dict(dataset_id='test', archive_member=f'{i}.jpg',
                 curation_status='eligible_for_exploration', group_id=f'test:{i}',
                 group_evidence=dict(source='collection log', reviewer='human', reviewed_at='2026-10-11', basis='specimen'),
                 split=split, ripeness_stage='ripe', is_augmented=False,
                 sha256=hashlib.sha256(str(i).encode()).hexdigest(),
                 pixel_sha256=hashlib.sha256(str(i).encode()).hexdigest())
            for i, split in enumerate(('train', 'validation', 'test'))]


class ValidationTests(unittest.TestCase):
    def test_structural_pass_does_not_claim_evaluation_readiness(self):
        report = audit_manifest(fixture())
        self.assertTrue(report['structural_checks_passed'])
        self.assertFalse(report['evaluation_ready'])

    def test_group_and_duplicate_leakage(self):
        rows = fixture()
        rows[1]['group_id'] = rows[0]['group_id']
        rows[2]['pixel_sha256'] = rows[0]['pixel_sha256']
        rows[2]['ripeness_stage'] = 'unripe'
        report = audit_manifest(rows)
        for issue in ('group_crosses_splits', 'duplicate_crosses_groups', 'duplicate_crosses_splits',
                      'conflicting_duplicate_labels', 'missing_class_in_split'):
            self.assertIn(issue, report['issues'])

    def test_unknown_evidence_and_augmentation_block_checks(self):
        rows = fixture()
        rows[0]['group_evidence']['basis'] = 'filename_guess'
        rows[1]['is_augmented'] = True
        rows[2].pop('is_augmented')
        report = audit_manifest(rows)
        self.assertEqual(report['issue_counts']['missing_group_evidence'], 1)
        self.assertEqual(report['issue_counts']['original_image_status_unverified'], 2)

    def test_exclusions_and_empty_manifest(self):
        rows = fixture()
        rows[0]['curation_status'] = 'quarantined_label_conflict'
        report = audit_manifest(rows)
        self.assertEqual(report['excluded_images'], 1)
        self.assertIn('train', report['issues']['empty_split'])
        self.assertFalse(audit_manifest([])['structural_checks_passed'])

    def test_duplicate_entries_and_missing_hashes(self):
        rows = fixture()
        rows.append(dict(rows[0]))
        rows[1]['sha256'] = None
        report = audit_manifest(rows)
        self.assertIn('duplicate_manifest_entry', report['issues'])
        self.assertIn('missing_or_invalid_hash', report['issues'])

    def test_cli_records_input_hash_and_returns_failure_without_changing_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'manifest.jsonl'
            output = Path(folder) / 'report.json'
            rows = fixture()
            rows[0]['group_id'] = None
            content = ''.join(json.dumps(row) + '\n' for row in rows)
            source.write_text(content)
            with contextlib.redirect_stdout(io.StringIO()):
                status = main(['--manifest', str(source), '--output', str(output)])
            self.assertEqual(status, 1)
            self.assertEqual(source.read_text(), content)
            self.assertEqual(json.loads(output.read_text())['manifest_sha256'],
                             hashlib.sha256(content.encode()).hexdigest())
