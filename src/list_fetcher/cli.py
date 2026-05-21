from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

from .models import AuthConfig, ListTarget
from .sharepoint import EntraTokenProvider, SharePointExporter, SharePointRestClient, SharePointRestorer
from .utils import load_dotenv, load_list_targets_file


def print_status(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export or restore SharePoint Online lists with schema and items.")
    parser.add_argument("--site-url", action="append", default=[], help="SharePoint site URL to discover lists from.")
    parser.add_argument("--list-urls-file", type=Path, help="Text file with one full list URL per line.")
    parser.add_argument("--output", type=Path, help="Directory where exports will be written.")
    parser.add_argument("--restore-path", type=Path, help="Path to an export root or single list export directory to restore from.")
    parser.add_argument("--target-site-url", help="Override the target site URL for restore.")
    parser.add_argument("--tenant", default=os.getenv("SP_EXPORT_TENANT"), help="Entra tenant id or domain.")
    parser.add_argument("--client-id", default=os.getenv("SP_EXPORT_CLIENT_ID"), help="Entra application client id.")
    parser.add_argument("--cert-path", default=os.getenv("SP_EXPORT_CERT_PATH"), help="PEM private key path.")
    parser.add_argument("--cert-thumbprint", default=os.getenv("SP_EXPORT_CERT_THUMBPRINT"), help="Certificate thumbprint.")
    parser.add_argument("--include-hidden", action="store_true", help="Include hidden/system lists during discovery.")
    return parser


def collect_targets(args: argparse.Namespace) -> list[ListTarget]:
    targets: list[ListTarget] = [ListTarget(site_url=url.rstrip("/"), source="site") for url in args.site_url]
    if args.list_urls_file:
        targets.extend(load_list_targets_file(args.list_urls_file))
    if not targets:
        raise ValueError("Provide at least one --site-url or --list-urls-file.")
    return targets


def build_auth_config(args: argparse.Namespace) -> AuthConfig:
    if not args.tenant or not args.client_id:
        raise ValueError("Both --tenant and --client-id are required.")
    config = AuthConfig(
        tenant=args.tenant,
        client_id=args.client_id,
        cert_path=args.cert_path,
        cert_thumbprint=args.cert_thumbprint,
    )
    config.validate()
    return config


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        auth = build_auth_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.restore_path:
        if args.output or args.site_url or args.list_urls_file:
            parser.error("Restore mode uses --restore-path and optional --target-site-url only.")
    else:
        if not args.output:
            parser.error("--output is required for export mode.")
        try:
            targets = collect_targets(args)
        except ValueError as exc:
            parser.error(str(exc))

    client = SharePointRestClient(EntraTokenProvider(auth))
    try:
        if args.restore_path:
            restorer = SharePointRestorer(client, status=print_status)
            manifest = restorer.restore(args.restore_path, target_site_url=args.target_site_url)
            print(f"Restored {manifest['list_count']} list(s) from {args.restore_path}")
        else:
            exporter = SharePointExporter(client, include_hidden=args.include_hidden, status=print_status)
            resolved = exporter.resolve_targets(targets)
            manifest = exporter.export(resolved, args.output)
            print(f"Exported {manifest['list_count']} list(s) to {args.output}")
    except (OSError, RuntimeError, requests.RequestException, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
