from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from django.conf import settings
from django.utils.module_loading import import_string

from apps.files.decode import bounded_canonicalize
from apps.files.images import CanonicalImage
from apps.files.scanner import ClamAVScanner
from apps.files.storage import MinioObjectStore


def object_store() -> Any:
    dotted = str(getattr(settings, "FILE_OBJECT_STORE", "") or "")
    if dotted:
        return import_string(dotted)()
    return MinioObjectStore.from_settings()


def virus_scanner() -> Any:
    dotted = str(getattr(settings, "FILE_SCANNER", "") or "")
    if dotted:
        return import_string(dotted)()
    return ClamAVScanner.from_settings()


def image_processor() -> Callable[[bytes, str], CanonicalImage]:
    dotted = str(getattr(settings, "FILE_IMAGE_PROCESSOR", "") or "")
    if dotted:
        processor = import_string(dotted)()
        return cast(Callable[[bytes, str], CanonicalImage], processor)
    return bounded_canonicalize
