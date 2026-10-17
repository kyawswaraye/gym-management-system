from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from typing import IO, cast

from PIL import Image, UnidentifiedImageError

from apps.files.constants import ALLOWED_CONTENT_TYPES, MAX_IMAGE_PIXELS
from apps.files.exceptions import ImageRejected

_DECODE_LOCK = threading.Lock()
_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


@dataclass(frozen=True)
class CanonicalImage:
    content_type: str
    body: bytes


def sniff_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _has_alpha(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA"}:
        return True
    return image.mode == "P" and "transparency" in image.info


def _reject_animation(image: Image.Image) -> None:
    frames = getattr(image, "n_frames", 1)
    animated = getattr(image, "is_animated", False)
    if frames != 1 or animated:
        raise ImageRejected("FILE_MALFORMED")
    try:
        image.seek(1)
    except EOFError:
        image.seek(0)
        return
    raise ImageRejected("FILE_MALFORMED")


def _bitmap(image: Image.Image, content_type: str) -> Image.Image:
    if content_type == "image/jpeg":
        converted = image if image.mode == "RGB" else image.convert("RGB")
    else:
        mode = "RGBA" if _has_alpha(image) else "RGB"
        converted = image if image.mode == mode else image.convert(mode)
    return Image.frombytes(converted.mode, converted.size, converted.tobytes())


def _save(image: Image.Image, content_type: str) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    if content_type == "image/jpeg":
        image.save(
            buffer,
            format="JPEG",
            quality=90,
            optimize=False,
            progressive=False,
            subsampling="4:2:0",
        )
    elif content_type == "image/png":
        image.save(buffer, format="PNG", optimize=False, compress_level=9)
    else:
        image.save(
            buffer,
            format="WEBP",
            lossless=True,
            quality=100,
            method=4,
            exact=True,
        )
    return buffer.getvalue()


def canonicalize(
    data: bytes,
    declared_type: str,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> CanonicalImage:
    """Decode one still image and return deterministic metadata-free bytes."""

    if declared_type not in ALLOWED_CONTENT_TYPES:
        raise ImageRejected("FILE_TYPE_MISMATCH")
    sniffed = sniff_content_type(data)
    if sniffed is None:
        raise ImageRejected("FILE_MALFORMED")
    if sniffed != declared_type:
        raise ImageRejected("FILE_TYPE_MISMATCH")
    with _DECODE_LOCK:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                try:
                    with Image.open(cast(IO[bytes], BytesReader(data))) as image:
                        if image.format != _FORMATS[declared_type]:
                            raise ImageRejected("FILE_TYPE_MISMATCH")
                        _reject_animation(image)
                        width, height = image.size
                        if (
                            not isinstance(width, int)
                            or not isinstance(height, int)
                            or width < 1
                            or height < 1
                        ):
                            raise ImageRejected("FILE_INVALID_DIMENSIONS")
                        if width * height > max_pixels:
                            raise ImageRejected("FILE_IMAGE_TOO_LARGE")
                        image.load()
                        clean = _bitmap(image, declared_type)
                except ImageRejected:
                    raise
                except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                    raise ImageRejected("FILE_IMAGE_TOO_LARGE") from None
                except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
                    raise ImageRejected("FILE_MALFORMED") from None
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit
    return CanonicalImage(content_type=declared_type, body=_save(clean, declared_type))


class BytesReader:
    """File-like view Pillow can seek without copying the upload."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def readline(self, size: int = -1) -> bytes:
        rest = self._data[self._offset :]
        newline = rest.find(b"\n")
        line = rest if newline < 0 else rest[: newline + 1]
        if size >= 0:
            line = line[:size]
        self._offset += len(line)
        return line

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        start = self._offset
        self._offset = min(len(self._data), start + size)
        return self._data[start : self._offset]

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset += self._offset
        elif whence == 2:
            offset += len(self._data)
        self._offset = max(0, min(len(self._data), offset))
        return self._offset

    def tell(self) -> int:
        return self._offset

    def seekable(self) -> bool:
        return True
