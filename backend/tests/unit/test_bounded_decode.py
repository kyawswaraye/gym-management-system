from __future__ import annotations

import multiprocessing
from io import BytesIO

import pytest
from PIL import Image

from apps.files.constants import MAX_UPLOAD_BYTES
from apps.files.decode import bounded_canonicalize, run_bounded_decode
from apps.files.exceptions import ImageRejected
from apps.files.images import canonicalize
from tests.decode_workers import (
    crash_worker,
    invalid_worker,
    oversized_worker,
    sleep_worker,
    unknown_code_worker,
)


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (4, 5, 6)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pids() -> set[int]:
    return {process.pid for process in multiprocessing.active_children() if process.pid}


def test_bounded_decode_returns_canonical_bytes() -> None:
    original = _png()
    before = _pids()
    decoded = bounded_canonicalize(original, "image/png")
    assert decoded.content_type == "image/png"
    assert decoded.body == canonicalize(original, "image/png").body
    assert _pids() == before


def test_bounded_decode_returns_a_stable_rejection_code() -> None:
    before = _pids()
    with pytest.raises(ImageRejected) as caught:
        bounded_canonicalize(_png(), "image/jpeg")
    assert caught.value.code == "FILE_TYPE_MISMATCH"
    assert str(caught.value) == "FILE_TYPE_MISMATCH"
    assert caught.value.__cause__ is None
    assert "UnidentifiedImage" not in str(caught.value)
    assert _pids() == before


def test_bounded_decode_maps_a_child_crash_to_malformed() -> None:
    before = _pids()
    with pytest.raises(ImageRejected) as caught:
        run_bounded_decode(_png(), "image/png", timeout=15, worker=crash_worker)
    assert caught.value.code == "FILE_MALFORMED"
    assert str(caught.value) == "FILE_MALFORMED"
    assert caught.value.__cause__ is None
    assert _pids() == before


def test_bounded_decode_terminates_a_worker_that_exceeds_the_timeout() -> None:
    before = _pids()
    with pytest.raises(ImageRejected) as caught:
        run_bounded_decode(b"\x89PNG\r\n\x1a\n", "image/png", timeout=0.2, worker=sleep_worker)
    assert caught.value.code == "FILE_IMAGE_TOO_LARGE"
    assert str(caught.value) == "FILE_IMAGE_TOO_LARGE"
    assert caught.value.__cause__ is None
    assert _pids() == before


def test_bounded_decode_rejects_oversized_and_invalid_child_results() -> None:
    original = _png()
    before = _pids()
    with pytest.raises(ImageRejected) as oversized:
        run_bounded_decode(original, "image/png", timeout=15, worker=oversized_worker)
    assert oversized.value.code == "FILE_TOO_LARGE"
    assert "x" * 32 not in str(oversized.value)
    with pytest.raises(ImageRejected) as invalid:
        run_bounded_decode(original, "image/png", timeout=15, worker=invalid_worker)
    assert invalid.value.code == "FILE_MALFORMED"
    with pytest.raises(ImageRejected) as leaked:
        run_bounded_decode(original, "image/png", timeout=15, worker=unknown_code_worker)
    assert leaked.value.code == "FILE_MALFORMED"
    assert "PROVIDER_TRACE_1234" not in str(leaked.value)
    with pytest.raises(ImageRejected) as huge_input:
        run_bounded_decode(
            b"x" * (MAX_UPLOAD_BYTES + 1),
            "image/png",
            timeout=1,
            worker=sleep_worker,
        )
    assert huge_input.value.code == "FILE_TOO_LARGE"
    assert _pids() == before
