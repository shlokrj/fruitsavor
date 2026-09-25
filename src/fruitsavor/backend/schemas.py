"""Public API contracts. Unknown predictions stay null, never heuristic guesses."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

FruitType = Literal['banana']
StorageMethod = Literal['counter', 'refrigerator', 'other', 'unknown']
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Temperature = Annotated[float, Field(ge=-50, le=60, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class FruitCreate(Contract):
    fruit_type: FruitType = 'banana'
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    storage_method: StorageMethod = 'unknown'
    purchased_on: date | None = None

    @field_validator('purchased_on')
    @classmethod
    def valid_purchase_date(cls, value):
        if value and value > datetime.now(timezone.utc).date():
            raise ValueError('Purchase date cannot be in the future')
        return value


class FruitPatch(Contract):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    storage_method: StorageMethod | None = None
    purchased_on: date | None = None

    @field_validator('storage_method')
    @classmethod
    def nonnull_storage(cls, value):
        if value is None:
            raise ValueError('Use unknown instead of null for storage_method')
        return value

    @field_validator('purchased_on')
    @classmethod
    def valid_purchase_date(cls, value):
        return FruitCreate.valid_purchase_date(value)


class FruitRecord(FruitCreate):
    id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    scan_count: int = Field(ge=0)


class CaptureMetadata(Contract):
    captured_at: AwareDatetime | None = None
    temperature_c: Temperature | None = None
    storage_method: StorageMethod | None = None

    @field_validator('captured_at')
    @classmethod
    def valid_capture_time(cls, value):
        if value is not None:
            if value > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError('Capture time cannot be more than five minutes in the future')
            return value.astimezone(timezone.utc)
        return value


class Features(Contract):
    green_like_ratio: Ratio
    yellow_like_ratio: Ratio
    brown_like_ratio: Ratio
    black_like_ratio: Ratio
    other_ratio: Ratio
    foreground_fraction: Ratio
    foreground_pixels: int = Field(ge=1)
    mean_saturation: Ratio
    mean_brightness: Ratio


class Prediction(Contract):
    status: Literal['unavailable'] = 'unavailable'
    reason: Literal['no_validated_model'] = 'no_validated_model'
    ripeness_stage: None = None
    freshness_score: None = None
    days_remaining: None = None
    confidence: None = None


class AnalysisResult(Contract):
    fruit_type: FruitType
    method: str
    status: Literal['unvalidated', 'review_required', 'insufficient_image']
    features: Features | None
    warnings: list[str]
    prediction: Prediction = Field(default_factory=Prediction)


class ImageInfo(Contract):
    source_width: int
    source_height: int
    width: int
    height: int
    resized: bool
    source_sha256: str
    normalized_sha256: str


class ArtifactURLs(Contract):
    image: str
    mask: str
    overlay: str


class ScanRecord(Contract):
    id: UUID
    fruit_id: UUID | None
    created_at: AwareDatetime
    captured_at: AwareDatetime
    storage_method: StorageMethod
    temperature_c: Temperature | None
    image: ImageInfo
    analysis: AnalysisResult
    artifacts: ArtifactURLs


class FruitPage(Contract):
    items: list[FruitRecord]
    total: int
    limit: int
    offset: int


class ScanPage(Contract):
    items: list[ScanRecord]
    total: int
    limit: int
    offset: int
