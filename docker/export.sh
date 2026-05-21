#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def parse_site_urls(value: str) -> list[str]:
    normalized = value.replace(",", "\n")
    return [item.strip() for item in normalized.splitlines() if item.strip()]


def main() -> int:
    output_dir = os.getenv("LIST_FETCHER_OUTPUT_DIR", "").strip()
    list_urls_file = os.getenv("LIST_FETCHER_LIST_URLS_FILE", "").strip()
    site_urls = parse_site_urls(os.getenv("LIST_FETCHER_SITE_URLS", ""))

    if not output_dir:
        print("error: LIST_FETCHER_OUTPUT_DIR must be set", file=sys.stderr)
        return 1
    if not site_urls and not list_urls_file:
        print("error: provide LIST_FETCHER_SITE_URLS or LIST_FETCHER_LIST_URLS_FILE", file=sys.stderr)
        return 1

    cmd = ["list-fetcher", "--output", output_dir]
    for site_url in site_urls:
        cmd.extend(["--site-url", site_url])
    if list_urls_file:
        cmd.extend(["--list-urls-file", list_urls_file])
    if env_flag("LIST_FETCHER_INCLUDE_HIDDEN"):
        cmd.append("--include-hidden")

    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
