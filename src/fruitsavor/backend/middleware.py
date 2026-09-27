"""Enforce total body bounds before multipart parsing, including chunked uploads."""
import asyncio
import hmac

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, max_bytes, api_token=None):
        self.app = app
        self.max_bytes = max_bytes
        self.api_token = api_token

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        public = scope['path'] in {'/', '/app/app.js', '/app/style.css', '/health', '/docs', '/docs/oauth2-redirect', '/redoc', '/openapi.json'}
        if self.api_token and not public:
            authorization = headers.get(b'authorization', b'').decode('latin1').split(' ', 1)
            if (len(authorization) != 2 or authorization[0].lower() != 'bearer'
                    or not hmac.compare_digest(authorization[1].encode(), self.api_token.encode())):
                return await JSONResponse({'detail': 'Valid bearer token required'}, 401,
                                          headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        if b'content-length' in headers:
            try:
                length = int(headers[b'content-length'])
            except ValueError:
                return await JSONResponse({'detail': 'Invalid Content-Length'}, 400)(scope, receive, send)
            if length < 0:
                return await JSONResponse({'detail': 'Invalid Content-Length'}, 400)(scope, receive, send)
            if length > self.max_bytes:
                return await JSONResponse({'detail': 'Request body exceeds the upload limit'}, 413)(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(30):
                while True:
                    event = await receive()
                    if event['type'] == 'http.disconnect':
                        return
                    chunk = event.get('body', b'')
                    if len(body) + len(chunk) > self.max_bytes:
                        return await JSONResponse({'detail': 'Request body exceeds the upload limit'}, 413)(scope, receive, send)
                    body.extend(chunk)
                    if not event.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'detail': 'Request body timed out'}, 408)(scope, receive, send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        async def private_send(event):
            if event['type'] == 'http.response.start':
                event['headers'] = list(event['headers']) + [(b'cache-control', b'no-store'),
                                                            (b'x-content-type-options', b'nosniff')]
            await send(event)

        await self.app(scope, bounded_receive, private_send)
