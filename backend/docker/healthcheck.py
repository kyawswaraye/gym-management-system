"""Loopback liveness probe for the private API container.

The forwarded-proto header matches the BFF so Django's TLS redirect does not
fail this probe. Reaching this process is not a certificate or provider proof.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request


def main() -> int:
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/health/",
        headers={"X-Forwarded-Proto": "https"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (OSError, TimeoutError, urllib.error.URLError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
