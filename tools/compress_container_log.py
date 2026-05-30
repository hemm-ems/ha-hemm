#!/usr/bin/env python3
"""Duplicate of hemm/tools/compress_container_log.py (source of truth: core repo).
Kept here so `make test-container-sc | python3 tools/compress_container_log.py`
works from this repo without a cross-repo path. Keep the two copies in sync.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path

MATCH = re.compile(r"FAILED|ERROR|WARNING|Traceback|^E\s|assert ")
SUMMARY_HEADER = re.compile(r"^=+ (short test summary info|FAILURES|ERRORS) =+")


def compress(lines: list[str], context: int) -> list[str]:
    out: list[str] = []
    buf: deque[str] = deque(maxlen=context)
    keep_until_eof = False
    emitted_idx: set[int] = set()

    for i, line in enumerate(lines):
        if not keep_until_eof and SUMMARY_HEADER.search(line):
            keep_until_eof = True
        if keep_until_eof:
            out.append(line)
            emitted_idx.add(i)
            continue
        if MATCH.search(line):
            start = max(0, i - len(buf))
            for j, b in enumerate(buf):
                idx = start + j
                if idx not in emitted_idx:
                    out.append(b)
                    emitted_idx.add(idx)
            if i not in emitted_idx:
                out.append(line)
                emitted_idx.add(i)
            buf.clear()
        else:
            buf.append(line)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, default=None)
    p.add_argument("--context", type=int, default=3)
    args = p.parse_args()

    text = args.file.read_text(errors="replace") if args.file else sys.stdin.read()
    lines = text.splitlines(keepends=False)
    compressed = compress(lines, args.context)
    sys.stdout.write("\n".join(compressed))
    if compressed and not compressed[-1].endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.write(f"\n--- compressed {len(lines)} → {len(compressed)} lines ---\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
