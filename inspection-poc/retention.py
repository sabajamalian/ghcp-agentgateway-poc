#!/usr/bin/env python3
"""Expire only known pilot metadata/evidence files based on modification time."""

import argparse
from pathlib import Path
import re
import time


def expire(directory, pattern, now=None):
    cutoff = (time.time() if now is None else now) - 7 * 86400
    if not directory.exists():
        return
    for entry in directory.iterdir():
        if (re.fullmatch(pattern, entry.name) and not entry.is_symlink()
                and entry.is_file() and entry.stat().st_mtime < cutoff):
            entry.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm", action="store_true")
    args = parser.parse_args()
    if args.vm:
        expire(Path("/var/log/inspection-poc"), r"metadata-\d{4}-\d{2}-\d{2}\.jsonl")
    else:
        expire(Path(__file__).resolve().parent.parent / ".secrets/inspection-poc/evidence",
               r"(?:cli|azure)-[a-f0-9-]{36}\.json")
