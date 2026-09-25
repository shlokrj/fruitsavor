"""Local defaults; secrets and runtime data are supplied outside source control."""
from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path = field(default_factory=lambda: Path('data/fruitsavor.sqlite3'))
    api_token: str | None = field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()
    max_upload_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 20_000_000
    analysis_edge: int = 512

    def __post_init__(self):
        if self.max_upload_bytes < 1 or self.max_image_pixels < 1024:
            raise ValueError('Image limits must be positive and allow at least 32x32 pixels')
        if not 32 <= self.analysis_edge <= 1024:
            raise ValueError('Analysis edge must be between 32 and 1024 pixels')
        if self.api_token is not None and not self.api_token.strip():
            raise ValueError('API token must not be empty')
        if '*' in self.allowed_origins:
            raise ValueError('List explicit CORS origins instead of a wildcard')

    @classmethod
    def from_env(cls):
        return cls(database=Path(os.getenv('FRUITSAVOR_DATABASE', 'data/fruitsavor.sqlite3')),
                   api_token=os.getenv('FRUITSAVOR_API_TOKEN'),
                   allowed_origins=tuple(origin.strip() for origin in
                       os.getenv('FRUITSAVOR_ALLOWED_ORIGINS', '').split(',') if origin.strip()))
