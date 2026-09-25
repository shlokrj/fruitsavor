import base64
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image, ImageDraw

from fruitsavor.annotation.evaluate import evaluate, overlap
from fruitsavor.annotation.project import digest, select_samples
from fruitsavor.annotation.store import ConflictError, Project
from fruitsavor.annotation.web import create_app


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def fixture_project(root):
    root.mkdir()
    (root / 'images').mkdir()
    items = []
    for index, partition in enumerate(('development', 'evaluation')):
        image = Image.new('RGB', (64, 64), 'white')
        ImageDraw.Draw(image).ellipse((16+index, 10, 48, 55), fill=(240, 220, 20))
        blob = png(image)
        identifier = digest(blob)
        (root / 'images' / f'{identifier}.png').write_bytes(blob)
        items.append(dict(id=identifier, image_sha256=identifier, width=64, height=64,
                          partition=partition, source_label='Ripe', archive_member=f'Ripe/{index}.png'))
    (root / 'project.json').write_text(json.dumps(dict(schema_version=1, scope='synthetic test only', items=items)))
    return Project(root)


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = fixture_project(Path(temporary.name) / 'project')
        self.item = next(i for i in self.project.items.values() if i['partition'] == 'evaluation')
        with Image.open(io.BytesIO(self.project.image(self.item['id']))) as image:
            self.truth = np.any(np.asarray(image) != 255, axis=2)
        self.mask = png(Image.fromarray(self.truth.astype('uint8')*255))

    def save(self, revision=0, status='reviewed', origin='human_manual', mask=None):
        return self.project.save(self.item['id'], self.mask if mask is None else mask, revision,
                                 status, 'Synthetic test annotator', origin, 'synthetic fixture', self.item['image_sha256'])

    def test_versioning_prevents_lost_edits_and_survives_restart(self):
        first = self.save(status='draft')
        self.assertEqual(first['revision'], 1)
        with self.assertRaises(ConflictError):
            self.save()
        self.save(revision=1)
        restarted = Project(self.project.root)
        self.assertEqual(restarted.latest(self.item['id'])['status'], 'reviewed')
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM annotations').fetchone()[0], 2)

    def test_gate_blocks_missing_draft_and_unapproved_origin(self):
        self.assertIsNone(evaluate(self.project)['metrics'])
        self.save(status='draft', origin='assistant_manual')
        self.assertIsNone(evaluate(self.project)['metrics'])
        self.save(revision=1, origin='assistant_manual')
        self.assertIsNone(evaluate(self.project)['metrics'])
        report = evaluate(self.project, allow_assistant=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['reference_origins'], {'assistant_manual':1})
        self.assertGreater(report['metrics']['mean_iou'], .95)
        self.assertEqual(report['per_image'][0]['reference_revision'], 2)


    def test_reference_origin_cannot_be_silently_reclassified(self):
        self.save(origin='assistant_manual')
        with self.assertRaisesRegex(ConflictError, 'origin is fixed'):
            self.save(revision=1, origin='human_manual')
        self.assertEqual(self.project.latest(self.item['id'])['revision'], 1)

    def test_complete_human_reference_and_empty_prediction_metrics(self):
        self.save()
        report = evaluate(self.project)
        self.assertEqual(report['metrics']['images'], 1)
        self.assertEqual(report['by_source_label']['Ripe']['images'], 1)
        scores = overlap(np.zeros((64, 64), bool), self.truth)
        self.assertEqual(scores['iou'], 0)
        self.assertEqual(scores['foreground_precision'], 0)
        with self.assertRaises(ValueError):
            overlap(np.ones((32, 32)), self.truth)

    def test_invalid_masks_and_source_changes_rejected(self):
        invalid = [b'bad', png(Image.new('L',(32,32),0)), png(Image.new('L',(64,64),127)),
                   png(Image.new('RGBA',(64,64),(255,255,255,0))),
                   png(Image.new('L',(64,64),0)), png(Image.new('L',(64,64),255))]
        for blob in invalid:
            with self.subTest(length=len(blob)), self.assertRaises(ValueError):
                self.save(mask=blob)
        (self.project.root / 'images' / f"{self.item['id']}.png").write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Source image changed'):
            self.save()

    def test_manifest_and_mask_tampering_detected(self):
        self.save()
        with self.project.connect() as db:
            db.execute('UPDATE annotations SET mask=?', (b'tampered',))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            evaluate(self.project)
        path = self.project.root / 'project.json'
        manifest = json.loads(path.read_text())
        manifest['scope'] = 'changed'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            Project(self.project.root)

    def test_web_save_requires_session_token_and_valid_revision(self):
        with TestClient(create_app(self.project), base_url='http://127.0.0.1') as client:
            html = client.get('/').text
            token = re.search("const TOKEN='([^']+)'", html).group(1)
            body = dict(mask_png_base64=base64.b64encode(self.mask).decode(), expected_revision=0,
                        image_sha256=self.item['image_sha256'], status='draft', annotator='Synthetic test',
                        origin='assistant_manual', notes='')
            path = f"/api/samples/{self.item['id']}"
            self.assertEqual(client.put(path,json=body).status_code, 403)
            headers = {'X-Review-Token':token}
            self.assertEqual(client.put(path,json=body,headers=headers).status_code, 200)
            self.assertEqual(client.put(path,json=body,headers=headers).status_code, 409)
            self.assertEqual(client.get(path+'/mask').status_code, 200)
            self.assertEqual(client.get('/api/samples/not-found/image').status_code, 404)
            self.assertEqual(client.get('/api/project',headers={'Host':'attacker.example'}).status_code, 400)


class SelectionTests(unittest.TestCase):
    def test_deterministic_disjoint_candidate_families_and_prior_view_exclusion(self):
        rows=[dict(archive_member=str(i), source_label='Ripe', sha256=f'{i:064x}',
                   curation_status='eligible_for_exploration') for i in range(20)]
        pairs=[dict(left='0',right='1'),dict(left='1',right='2'),dict(left='3',right='4')]
        selected=select_samples(rows,pairs,{},['0'],per_class=3)
        again=select_samples(list(reversed(rows)),list(reversed(pairs)),{},['0'],per_class=3)
        self.assertEqual(selected,again)
        development=[r for r in selected if r['partition']=='development']
        evaluation=[r for r in selected if r['partition']=='evaluation']
        self.assertEqual(len(development),3)
        self.assertEqual(len(evaluation),3)
        self.assertFalse({r['similarity_component'] for r in development}&{r['similarity_component'] for r in evaluation})
        self.assertFalse({'0','1','2'}&{r['archive_member'] for r in evaluation})
        with self.assertRaises(ValueError):
            select_samples(rows,pairs,{},[],per_class=30)
