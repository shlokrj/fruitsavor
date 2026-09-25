import unittest
from fruitsavor.backend.middleware import RequestLimits


class BodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, chunks, headers=()):
        received = []
        responses = []
        pending = [{'type':'http.request', 'body':chunk, 'more_body':i < len(chunks)-1}
                   for i, chunk in enumerate(chunks)]

        async def receive():
            return pending.pop(0)

        async def send(message):
            responses.append(message)

        async def endpoint(scope, receive, send):
            received.append((await receive())['body'])
            await send({'type':'http.response.start', 'status':204, 'headers':[]})
            await send({'type':'http.response.body', 'body':b''})

        app = RequestLimits(endpoint, max_bytes=8)
        await app({'type':'http', 'path':'/analyze', 'method':'POST', 'headers':headers}, receive, send)
        return received, responses

    async def test_chunked_upload_rejected_without_content_length(self):
        received, response = await self.request([b'12345', b'6789'])
        self.assertEqual(received, [])
        self.assertEqual(response[0]['status'], 413)

    async def test_false_content_length_cannot_bypass_limit(self):
        received, response = await self.request([b'123456789'], [(b'content-length', b'1')])
        self.assertEqual(received, [])
        self.assertEqual(response[0]['status'], 413)

    async def test_exact_limit_replayed_once_with_security_headers(self):
        received, response = await self.request([b'1234', b'5678'])
        self.assertEqual(received, [b'12345678'])
        self.assertEqual(response[0]['status'], 204)
        self.assertIn((b'cache-control', b'no-store'), response[0]['headers'])

    async def test_invalid_length_rejected(self):
        for value in (b'bad', b'-1'):
            _, response = await self.request([], [(b'content-length', value)])
            self.assertEqual(response[0]['status'], 400)
