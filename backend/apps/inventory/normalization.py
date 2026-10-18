"""Canonical catalog text.

Category names collapse internal whitespace and compare in uppercase. Barcodes
drop whitespace and uppercase ASCII letters. Neither transformation accepts an
empty result.
"""

from __future__ import annotations

import re

_BARCODE = re.compile(r"^[0-9A-Z]{1,64}$")
_SPACE = re.compile(r"\s+")


def canonical_display_name(value: str) -> str:
    """Collapse whitespace and reject a blank catalog name."""

    collapsed = _SPACE.sub(" ", value.strip())
    if collapsed == "":
        raise ValueError("Enter a name.")
    return collapsed


def canonical_category_name(value: str) -> tuple[str, str]:
    """Return the stored name and the gym-unique uppercase form."""

    collapsed = _SPACE.sub(" ", value.strip())
    if collapsed == "":
        raise ValueError("Enter a category name.")
    return collapsed, collapsed.upper()


def canonical_barcode(value: str | None) -> str | None:
    """Return a normalized barcode, or ``None`` when the input is absent."""

    if value is None:
        return None
    compact = _SPACE.sub("", value.strip()).upper()
    if _BARCODE.fullmatch(compact) is None:
        raise ValueError("Enter a barcode of letters and digits.")
    return compact
