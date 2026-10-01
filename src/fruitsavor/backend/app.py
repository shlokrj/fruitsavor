"""FastAPI application factory; importing this module does not create a database."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import logging
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.responses import FileResponse, JSONResponse

from .config import Settings
from fruitsavor.vision import METHOD
from .middleware import RequestLimits
from .schemas import (CaptureMetadata, FruitCreate, FruitPage, FruitPatch, FruitRecord,
                      FruitType, ScanPage, ScanRecord, StorageMethod)
from .service import AnalysisBusyError, AnalysisService, ImageInputError
from .store import ConflictError, NotFoundError, Store
from .schemas import ObservationCreate, ObservationPage, ObservationRecord, ObservationUpdate

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    service = AnalysisService(settings)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(title='FruitSavor', version='0.2.0', lifespan=lifespan,
                  description='Single-user fruit tracking and exploratory banana image analysis. '
                              'No validated ripeness or shelf-life model is currently available.')
    app.state.store = store
    app.state.analysis_service = service
    app.state.settings = settings

    @app.get('/', include_in_schema=False)
    def mobile_app():
        return FileResponse(Path(__file__).with_name('web') / 'index.html', headers={
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            'Referrer-Policy': 'no-referrer',
        })

    @app.get('/app/{asset}', include_in_schema=False)
    def mobile_asset(asset: Literal['app.js', 'style.css', 'fredoka.ttf', 'OFL.txt', 'banana-glossy.png']):
        return FileResponse(Path(__file__).with_name('web') / asset)
    app.add_middleware(RequestLimits, max_bytes=settings.max_upload_bytes + 65536,
                       api_token=settings.api_token)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_credentials=False,
                           allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
                           allow_headers=['Authorization', 'Content-Type'])
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if settings.api_token and (credentials is None or not hmac.compare_digest(
                credentials.credentials.encode(), settings.api_token.encode())):
            raise HTTPException(401, 'Valid bearer token required', headers={'WWW-Authenticate': 'Bearer'})

    router = APIRouter(dependencies=[Depends(authorize)])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not reflect uploaded values or non-finite numbers into error JSON.
        return JSONResponse({'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()
        ]}, status_code=422)

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflicting_update(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(ImageInputError)
    async def invalid_image(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=exc.status)

    @app.exception_handler(AnalysisBusyError)
    async def busy(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503, headers={'Retry-After': '1'})

    @app.exception_handler(sqlite3.Error)
    async def unavailable_database(request, exc):
        logger.error('Database operation failed: %s', type(exc).__name__)
        return JSONResponse({'detail': 'Storage temporarily unavailable'}, status_code=503,
                            headers={'Retry-After': '1'})

    @app.get('/health', tags=['system'])
    def health():
        store.healthy()
        return {'status': 'ok', 'api_version': '0.2.0'}

    @router.get('/capabilities', tags=['system'])
    def capabilities():
        return dict(supported_fruits=list(service.handlers),
                    analysis_method=METHOD,
                    fruit_detection=False, visual_features=True,
                    ripeness_prediction=False, shelf_life_prediction=False,
                    freshness_score=False, validation_status='exploratory',
                    supported_image_formats=['JPEG', 'PNG', 'WEBP'],
                    max_upload_bytes=settings.max_upload_bytes,
                    max_image_pixels=settings.max_image_pixels,
                    analysis_max_edge=settings.analysis_edge)

    @router.post('/fruit', response_model=FruitRecord, status_code=201, tags=['fruit'])
    def create_fruit(fruit: FruitCreate):
        return store.create_fruit(fruit.model_dump(mode='json'))

    @router.get('/fruit', response_model=FruitPage, tags=['fruit'])
    def list_fruit(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                   offset: Annotated[int, Query(ge=0)] = 0):
        return store.list_fruits(limit, offset)

    @router.get('/fruit/{fruit_id}', response_model=FruitRecord, tags=['fruit'])
    def get_fruit(fruit_id: UUID):
        return store.get_fruit(str(fruit_id))

    @router.get('/fruit/{fruit_id}/export', tags=['fruit'])
    def export_fruit(fruit_id: UUID):
        return JSONResponse(store.export_fruit(str(fruit_id)), headers={
            'Content-Disposition': f'attachment; filename="fruitsavor-{fruit_id}.json"'})

    @router.patch('/fruit/{fruit_id}', response_model=FruitRecord, tags=['fruit'])
    def update_fruit(fruit_id: UUID, patch: FruitPatch):
        return store.update_fruit(str(fruit_id), patch.model_dump(mode='json', exclude_unset=True))

    @router.delete('/fruit/{fruit_id}', status_code=204, tags=['fruit'])
    def delete_fruit(fruit_id: UUID):
        store.delete_fruit(str(fruit_id))
        return Response(status_code=204)

    def present(record):
        identifier = record['id']
        return dict(record, artifacts={kind: f'/scans/{identifier}/artifacts/{kind}'
                                       for kind in ('image', 'mask', 'overlay')})

    def scan(file, captured_at, temperature_c, storage_method, fruit_type, fruit_id=None):
        try:
            metadata = CaptureMetadata(captured_at=captured_at, temperature_c=temperature_c,
                                       storage_method=storage_method)
        except ValidationError as exc:
            raise HTTPException(422, 'Invalid capture metadata: ' + '; '.join(
                error['msg'] for error in exc.errors())) from exc
        parent = store.get_fruit(fruit_id) if fruit_id else None
        blob = file.file.read(settings.max_upload_bytes + 1)
        payload, artifacts = service.process(blob, parent['fruit_type'] if parent else fruit_type)
        payload.update(captured_at=(metadata.captured_at or datetime.now(timezone.utc)).isoformat(),
                       temperature_c=metadata.temperature_c,
                       storage_method=metadata.storage_method or (parent['storage_method'] if parent else 'unknown'))
        return present(store.save_scan(payload, artifacts, fruit_id))

    @router.post('/analyze', response_model=ScanRecord, status_code=201, tags=['analysis'],
                 description='Analyze and save a standalone scan. Returns exploratory features and artifact URLs; '
                             'prediction fields are unavailable. Only one analysis runs per worker at a time.')
    def analyze_image(file: Annotated[UploadFile, File()], fruit_type: Annotated[FruitType, Form()] = 'banana',
                      captured_at: Annotated[str | None, Form()] = None,
                      temperature_c: Annotated[float | None, Form()] = None,
                      storage_method: Annotated[StorageMethod | None, Form()] = None):
        return scan(file, captured_at, temperature_c, storage_method, fruit_type)

    @router.post('/fruit/{fruit_id}/scan', response_model=ScanRecord, status_code=201, tags=['analysis'])
    def scan_fruit(fruit_id: UUID, file: Annotated[UploadFile, File()],
                   captured_at: Annotated[str | None, Form()] = None,
                   temperature_c: Annotated[float | None, Form()] = None,
                   storage_method: Annotated[StorageMethod | None, Form()] = None):
        return scan(file, captured_at, temperature_c, storage_method, 'banana', str(fruit_id))

    @router.get('/fruit/{fruit_id}/history', response_model=ScanPage, tags=['scans'])
    def fruit_history(fruit_id: UUID, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                      offset: Annotated[int, Query(ge=0)] = 0):
        page = store.list_scans(limit, offset, str(fruit_id))
        return dict(page, items=[present(row) for row in page['items']])

    @router.get('/scans', response_model=ScanPage, tags=['scans'])
    def recent_scans(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     offset: Annotated[int, Query(ge=0)] = 0):
        page = store.list_scans(limit, offset)
        return dict(page, items=[present(row) for row in page['items']])

    @router.post('/fruit/{fruit_id}/observations', response_model=ObservationRecord,
                 status_code=201, tags=['observations'])
    def add_observation(fruit_id: UUID, observation: ObservationCreate):
        return store.save_observation(str(fruit_id), observation.model_dump(mode='json'))

    @router.get('/fruit/{fruit_id}/observations', response_model=ObservationPage, tags=['observations'])
    def observations(fruit_id: UUID, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     offset: Annotated[int, Query(ge=0)] = 0):
        return store.list_observations(str(fruit_id), limit, offset)

    @router.delete('/fruit/{fruit_id}/observations/{observation_id}', status_code=204, tags=['observations'])
    def delete_observation(fruit_id: UUID, observation_id: UUID):
        store.delete_observation(str(fruit_id), str(observation_id))
        return Response(status_code=204)

    @router.patch('/fruit/{fruit_id}/observations/{observation_id}', response_model=ObservationRecord,
                  tags=['observations'])
    def update_observation(fruit_id: UUID, observation_id: UUID, observation: ObservationUpdate):
        return store.update_observation(str(fruit_id), str(observation_id), observation.model_dump(mode='json'))

    @router.get('/fruit/{fruit_id}/observations/{observation_id}/revisions',
                response_model=list[ObservationRecord], tags=['observations'])
    def observation_revisions(fruit_id: UUID, observation_id: UUID):
        return store.observation_revisions(str(fruit_id), str(observation_id))

    @router.get('/scans/{scan_id}', response_model=ScanRecord, tags=['scans'])
    def get_scan(scan_id: UUID):
        return present(store.get_scan(str(scan_id)))

    @router.delete('/scans/{scan_id}', status_code=204, tags=['scans'])
    def delete_scan(scan_id: UUID):
        store.delete_scan(str(scan_id))
        return Response(status_code=204)

    @router.get('/scans/{scan_id}/artifacts/{kind}', tags=['scans'], response_class=Response,
                responses={200: {'content': {'image/png': {}}}})
    def scan_artifact(scan_id: UUID, kind: Literal['image', 'mask', 'overlay']):
        return Response(store.artifact(str(scan_id), kind), media_type='image/png')

    app.include_router(router)
    return app
