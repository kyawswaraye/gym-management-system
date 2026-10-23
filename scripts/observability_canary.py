#!/usr/bin/env python3
"""Print a synthetic alert for the non-production sink.

This command does not contact Alertmanager and does not deliver a notification.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from observability import synthetic_canary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(synthetic_canary(Path(args.repo).resolve()))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
