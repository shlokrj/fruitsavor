from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from fruitsavor.backend.app import create_app
from fruitsavor.backend.config import Settings


def photo(size=(128, 128), blank=False, format='PNG', exif=None):
    image = Image.new('RGB', size, 'white')
    if not blank:
        ImageDraw.Draw(image).ellipse((size[0]*.25, size[1]*.15, size[0]*.75, size[1]*.85),
                                      fill=(240, 220, 20))
    stream = io.BytesIO()
    kwargs = {'exif': exif} if exif else {}
    image.save(stream, format=format, **kwargs)
    return stream.getvalue()


class APITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(database=Path(directory.name) / 'db.sqlite3')
        self.app = create_app(self.settings)
        self.client = self.enterContext(TestClient(self.app))

    def upload(self, route='/analyze', blob=None, **data):
        return self.client.post(route, files={'file': ('banana.png', photo() if blob is None else blob, 'image/png')},
                                data=data)

    def fruit(self, **values):
        response = self.client.post('/fruit', json=dict(name='Kitchen banana', storage_method='counter', **values))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_full_lifecycle_and_artifact_cleanup(self):
        fruit = self.fruit()
        response = self.upload(f"/fruit/{fruit['id']}/scan", captured_at='2026-01-01T12:00:00-06:00', temperature_c='21')
        self.assertEqual(response.status_code, 201, response.text)
        scan = response.json()
        self.assertEqual(scan['captured_at'], '2026-01-01T18:00:00Z')
        self.assertEqual(scan['storage_method'], 'counter')
        self.assertEqual(scan['analysis']['status'], 'unvalidated')
        self.assertEqual(scan['analysis']['method'], 'banana-grabcut-hsv-v2')
        self.assertEqual(scan['analysis']['method'], self.client.get('/capabilities').json()['analysis_method'])
        self.assertEqual(scan['analysis']['prediction']['status'], 'unavailable')
        for key in ('days_remaining', 'ripeness_stage', 'confidence', 'freshness_score'):
            self.assertIsNone(scan['analysis']['prediction'][key])
        for path in scan['artifacts'].values():
            artifact = self.client.get(path)
            self.assertEqual(artifact.status_code, 200)
            self.assertEqual(artifact.headers['content-type'], 'image/png')
            self.assertEqual(artifact.headers['cache-control'], 'no-store')
            with Image.open(io.BytesIO(artifact.content)) as im:
                self.assertEqual(im.size, (128, 128))
        self.assertEqual(self.client.get(f"/fruit/{fruit['id']}/history").json()['total'], 1)
        self.assertEqual(self.client.get('/fruit').json()['items'][0]['scan_count'], 1)
        self.client.patch(f"/fruit/{fruit['id']}", json={'storage_method': 'refrigerator', 'name': None})
        self.assertEqual(self.client.get(f"/scans/{scan['id']}").json()['storage_method'], 'counter')
        self.assertEqual(self.client.delete(f"/fruit/{fruit['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/scans/{scan['id']}").status_code, 404)
        self.assertEqual(self.client.get(scan['artifacts']['mask']).status_code, 404)

    def test_standalone_scan_and_restart(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        scan = response.json()
        self.assertIsNone(scan['fruit_id'])
        with TestClient(create_app(self.settings)) as restarted:
            self.assertEqual(restarted.get(f"/scans/{scan['id']}").json(), scan)
            self.assertEqual(restarted.get('/scans?limit=1&offset=0').json()['total'], 1)
            self.assertEqual(restarted.delete(f"/scans/{scan['id']}").status_code, 204)
            self.assertEqual(restarted.get('/scans').json()['total'], 0)

    def test_blank_image_has_no_features_or_predictions(self):
        response = self.upload(blob=photo(blank=True))
        self.assertEqual(response.status_code, 201, response.text)
        analysis = response.json()['analysis']
        self.assertEqual(analysis['status'], 'insufficient_image')
        self.assertIsNone(analysis['features'])
        self.assertIn('no_foreground_found', analysis['warnings'])
        self.assertIsNone(analysis['prediction']['days_remaining'])

    def test_bad_input_never_creates_scan(self):
        for blob, status in [(b'', 400), (b'not an image', 400), (photo((10, 10)), 422),
                             (photo(format='GIF'), 415), (photo()[:30], 400)]:
            with self.subTest(status=status, length=len(blob)):
                self.assertEqual(self.upload(blob=blob).status_code, status)
        self.assertEqual(self.upload(fruit_type='apple').status_code, 422)
        self.assertEqual(self.upload(captured_at='2026-01-01T12:00:00').status_code, 422)
        self.assertEqual(self.upload(captured_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()).status_code, 422)
        for temperature in ('NaN', 'Infinity', '90'):
            self.assertEqual(self.upload(temperature_c=temperature).status_code, 422)
        self.assertEqual(self.client.get('/scans').json()['total'], 0)

    def test_json_and_pagination_validation(self):
        for payload in ({'fruit_type': 'apple'}, {'name': '  '}, {'invented': True},
                        {'purchased_on': '2999-01-01'}):
            self.assertEqual(self.client.post('/fruit', json=payload).status_code, 422)
        fruit = self.fruit()
        for payload in ({'fruit_type': 'apple'}, {'storage_method': None}):
            self.assertEqual(self.client.patch(f"/fruit/{fruit['id']}", json=payload).status_code, 422)
        for path in ('/fruit?limit=101', '/scans?offset=-1', '/fruit/not-a-uuid'):
            self.assertEqual(self.client.get(path).status_code, 422)
        self.assertEqual(self.client.get(f'/fruit/{uuid4()}').status_code, 404)
        self.assertEqual(self.upload(f'/fruit/{uuid4()}/scan').status_code, 404)

    def test_dimensions_orientation_and_metadata_stripping(self):
        exif = Image.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise.
        exif[270] = 'private test metadata'
        response = self.upload(blob=photo((800, 400), format='JPEG', exif=exif))
        self.assertEqual(response.status_code, 201, response.text)
        scan = response.json()
        self.assertEqual((scan['image']['source_width'], scan['image']['source_height']), (400, 800))
        self.assertEqual((scan['image']['width'], scan['image']['height']), (256, 512))
        self.assertTrue(scan['image']['resized'])
        with Image.open(io.BytesIO(self.client.get(scan['artifacts']['image']).content)) as image:
            self.assertFalse(image.getexif())
            self.assertEqual(image.info, {})

    def test_limits_and_busy_response(self):
        with TestClient(create_app(Settings(database=self.settings.database, max_upload_bytes=500))) as small:
            self.assertEqual(small.post('/analyze', files={'file': ('a', b'x'*501)}).status_code, 413)
            self.assertEqual(small.post('/analyze', content=b'x'*70000).status_code, 413)
        with TestClient(create_app(Settings(database=self.settings.database, max_image_pixels=1024))) as small:
            self.assertEqual(small.post('/analyze', files={'file': ('a', photo())}).status_code, 413)
        self.app.state.analysis_service.gate.acquire()
        try:
            response = self.upload()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers['retry-after'], '1')
        finally:
            self.app.state.analysis_service.gate.release()
        self.assertEqual(self.upload().status_code, 201)

    def test_token_protects_records_and_images_and_cors(self):
        settings = Settings(database=self.settings.database, api_token='test-token-only',
                            allowed_origins=('http://localhost:3000',))
        with TestClient(create_app(settings)) as secured:
            shell = secured.get('/')
            self.assertEqual(shell.status_code, 200)
            self.assertIn("frame-ancestors 'none'", shell.headers['content-security-policy'])
            self.assertNotIn('test-token-only', shell.text)
            for asset in ('app.js', 'style.css', 'fredoka.ttf', 'OFL.txt'):
                self.assertEqual(secured.get('/app/' + asset).status_code, 200)
            self.assertEqual(secured.get('/app/anything-else').status_code, 401)
            self.assertEqual(secured.get('/health').status_code, 200)
            self.assertEqual(secured.get('/docs').status_code, 200)
            self.assertEqual(secured.get('/fruit').status_code, 401)
            self.assertEqual(secured.get('/scans', headers={'Authorization':'Bearer wrong'}).status_code, 401)
            secured.headers['Authorization'] = 'Bearer test-token-only'
            scan = secured.post('/analyze', files={'file': ('a', photo())}).json()
            self.assertEqual(secured.get(scan['artifacts']['image']).status_code, 200)
            secured.headers.pop('Authorization')
            self.assertEqual(secured.get(scan['artifacts']['image']).status_code, 401)
            preflight = secured.options('/analyze', headers={'Origin':'http://localhost:3000',
                                        'Access-Control-Request-Method':'POST',
                                        'Access-Control-Request-Headers':'Authorization'})
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(preflight.headers['access-control-allow-origin'], 'http://localhost:3000')
            untrusted = secured.options('/analyze', headers={'Origin':'https://other.example',
                                        'Access-Control-Request-Method':'POST'})
            self.assertEqual(untrusted.status_code, 400)

    def test_openapi_exposes_contract_and_system_capabilities(self):
        document = self.client.get('/openapi.json').json()
        self.assertIn('/fruit/{fruit_id}/scan', document['paths'])
        self.assertIn('ScanRecord', document['components']['schemas'])
        self.assertFalse(self.client.get('/capabilities').json()['shelf_life_prediction'])
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_transparency_composites_to_white(self):
        image = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
        blob = io.BytesIO()
        image.save(blob, format='PNG')
        response = self.upload(blob=blob.getvalue())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['analysis']['status'], 'insufficient_image')

    def test_animated_webp_rejected(self):
        first = Image.new('RGB', (64, 64), 'red')
        second = Image.new('RGB', (64, 64), 'green')
        stream = io.BytesIO()
        first.save(stream, format='WEBP', save_all=True, append_images=[second], duration=100, loop=0)
        self.assertEqual(self.upload(blob=stream.getvalue()).status_code, 415)

    def test_nonfinite_json_validation_does_not_echo_values_or_crash(self):
        response = self.client.post('/fruit', content='{"name":NaN}', headers={'Content-Type':'application/json'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('input', response.json()['detail'][0])

    def test_database_errors_are_sanitized(self):
        from unittest.mock import patch
        import sqlite3
        with patch.object(self.app.state.store, 'healthy', side_effect=sqlite3.OperationalError('private path')):
            response = self.client.get('/health')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private path', response.text)
