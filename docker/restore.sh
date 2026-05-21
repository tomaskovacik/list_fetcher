#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    restore_path = os.getenv("LIST_FETCHER_RESTORE_PATH", "").strip()
    if not restore_path:
        print("error: LIST_FETCHER_RESTORE_PATH must be set", file=sys.stderr)
        return 1

    cmd = ["list-fetcher", "--restore-path", restore_path]
    target_site_url = os.getenv("LIST_FETCHER_TARGET_SITE_URL", "").strip()
    if target_site_url:
        cmd.extend(["--target-site-url", target_site_url])

    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
