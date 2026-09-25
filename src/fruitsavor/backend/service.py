"""Bounded image decoding and versioned, fruit-specific analysis dispatch."""
import hashlib
import io
import threading
import warnings

import cv2
from PIL import Image, ImageOps, UnidentifiedImageError

from fruitsavor.vision import analyze, overlay
from .schemas import AnalysisResult, ImageInfo


class ImageInputError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class AnalysisBusyError(Exception):
    pass


def png_bytes(image):
    output = io.BytesIO()
    # Rebuild the image to discard EXIF, GPS, ICC and other input metadata.
    Image.frombytes(image.mode, image.size, image.tobytes()).save(output, format='PNG')
    return output.getvalue()


class AnalysisService:
    def __init__(self, settings):
        self.settings = settings
        self.handlers = {'banana': analyze}
        self.gate = threading.BoundedSemaphore(1)

    def process(self, blob, fruit_type):
        if fruit_type not in self.handlers:
            raise ImageInputError('Unsupported fruit type')
        if not self.gate.acquire(blocking=False):
            raise AnalysisBusyError('An analysis is already running; retry shortly')
        try:
            return self._process(blob, fruit_type)
        finally:
            self.gate.release()

    def _process(self, blob, fruit_type):
        if not blob:
            raise ImageInputError('Upload an image; the file is empty', 400)
        if len(blob) > self.settings.max_upload_bytes:
            raise ImageInputError('Image exceeds the upload limit', 413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(blob)) as source:
                    if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                        raise ImageInputError('Use a JPEG, PNG or WebP image', 415)
                    if getattr(source, 'n_frames', 1) != 1:
                        raise ImageInputError('Animated images are not supported', 415)
                    if source.width * source.height > self.settings.max_image_pixels:
                        raise ImageInputError('Image dimensions exceed the pixel limit', 413)
                    if min(source.size) < 32:
                        raise ImageInputError('Image must be at least 32 pixels on each side')
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    original_size = oriented.size
                    # Composite alpha onto the assumed background, not black.
                    rgba = oriented.convert('RGBA')
                    background = Image.new('RGBA', rgba.size, 'white')
                    image = Image.alpha_composite(background, rgba).convert('RGB')
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ImageInputError('Image dimensions exceed the pixel limit', 413) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ImageInputError('Image is invalid or incomplete', 400) from exc
        image.thumbnail((self.settings.analysis_edge, self.settings.analysis_edge), Image.Resampling.LANCZOS)
        if min(image.size) < 32:
            raise ImageInputError('Image is too narrow after resizing; use a tighter crop')
        try:
            result = self.handlers[fruit_type](image)
        except cv2.error as exc:
            raise ImageInputError('Unable to segment this image; try a plain light background') from exc
        status = ('insufficient_image' if not result.features else
                  'review_required' if result.warnings else 'unvalidated')
        analysis = AnalysisResult(fruit_type=fruit_type,
                                  method='banana-grabcut-hsv-v1', status=status,
                                  features=result.features or None, warnings=result.warnings)
        artifacts = dict(image=png_bytes(image),
                         mask=png_bytes(Image.fromarray(result.mask.astype('uint8') * 255)),
                         overlay=png_bytes(overlay(image, result.mask)))
        info = ImageInfo(source_width=original_size[0], source_height=original_size[1],
                         width=image.width, height=image.height, resized=image.size != original_size,
                         source_sha256=hashlib.sha256(blob).hexdigest(),
                         normalized_sha256=hashlib.sha256(artifacts['image']).hexdigest())
        return dict(image=info.model_dump(), analysis=analysis.model_dump()), artifacts
