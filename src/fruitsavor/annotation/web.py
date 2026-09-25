"""Loopback-only reference-mask workbench; no predictions are shown during labeling."""
import base64
import binascii
from importlib.resources import files
import secrets
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fruitsavor.backend.middleware import RequestLimits
from .store import ConflictError


class SaveAnnotation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    image_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    mask_png_base64: str = Field(max_length=1_000_000)
    status: Literal['draft', 'reviewed']
    annotator: str = Field(min_length=1, max_length=100)
    origin: Literal['human_manual', 'assistant_manual']
    notes: str = Field(default='', max_length=2000)


def create_app(project):
    token = secrets.token_urlsafe(32)
    app = FastAPI(title='FruitSavor mask review', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestLimits, max_bytes=1_100_000)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':'Sample not found'}, 404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from starlette.responses import JSONResponse
        return JSONResponse({'detail':str(exc)}, 409 if isinstance(exc, ConflictError) else 422)

    @app.get('/', response_class=HTMLResponse)
    def index():
        return files('fruitsavor.annotation').joinpath('workbench.html').read_text().replace('__SAVE_TOKEN__', token)

    @app.get('/api/project')
    def manifest():
        return dict(scope=project.manifest['scope'], items=[dict(item, annotation=project.latest(item['id']))
                                                            for item in project.items.values()])

    @app.get('/api/samples/{identifier}/image')
    def source_image(identifier: str):
        return Response(project.image(identifier), media_type='image/png')

    @app.get('/api/samples/{identifier}/mask')
    def reference_mask(identifier: str):
        annotation = project.latest(identifier, include_mask=True)
        if annotation is None:
            raise HTTPException(404, 'No annotation yet')
        return Response(annotation['mask'], media_type='image/png')

    @app.put('/api/samples/{identifier}')
    def save(identifier: str, annotation: SaveAnnotation,
             x_review_token: Annotated[str | None, Header()] = None):
        if x_review_token is None or not secrets.compare_digest(x_review_token, token):
            raise HTTPException(403, 'Reload the workbench before saving')
        try:
            blob = base64.b64decode(annotation.mask_png_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(422, 'Invalid mask encoding') from exc
        return project.save(identifier, blob, annotation.expected_revision, annotation.status,
                            annotation.annotator, annotation.origin, annotation.notes, annotation.image_sha256)

    return app
