#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import msal

from list_fetcher.utils import load_dotenv


def decode_jwt_claims(access_token: str) -> dict:
    parts = access_token.split(".")
    if len(parts) != 3:
        raise ValueError("Access token is not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def build_client_credential(args: argparse.Namespace) -> dict[str, str]:
    if args.cert_path and args.cert_thumbprint:
        return {
            "thumbprint": args.cert_thumbprint,
            "private_key": Path(args.cert_path).read_text(encoding="utf-8"),
        }
    raise ValueError("Provide both SP_EXPORT_CERT_PATH and SP_EXPORT_CERT_THUMBPRINT.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire and inspect a SharePoint app-only access token.")
    parser.add_argument("--site-url", required=True, help="SharePoint site URL used to derive the token audience.")
    parser.add_argument("--tenant", default=os.getenv("SP_EXPORT_TENANT"), help="Tenant UUID or domain.")
    parser.add_argument("--client-id", default=os.getenv("SP_EXPORT_CLIENT_ID"), help="Entra application client id.")
    parser.add_argument("--cert-path", default=os.getenv("SP_EXPORT_CERT_PATH"), help="PEM private key path.")
    parser.add_argument("--cert-thumbprint", default=os.getenv("SP_EXPORT_CERT_THUMBPRINT"), help="Certificate thumbprint.")
    parser.add_argument("--show-access-token", action="store_true", help="Print the raw access token too.")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.tenant or not args.client_id:
        parser.error("Both --tenant and --client-id are required.")

    try:
        client_credential = build_client_credential(args)
    except ValueError as exc:
        parser.error(str(exc))

    split = urlsplit(args.site_url)
    site_origin = f"{split.scheme}://{split.netloc}"

    app = msal.ConfidentialClientApplication(
        client_id=args.client_id,
        authority=f"https://login.microsoftonline.com/{args.tenant}",
        client_credential=client_credential,
    )

    token = app.acquire_token_for_client(scopes=[f"{site_origin}/.default"])
    print("TOKEN RESPONSE KEYS:", sorted(token.keys()))
    if "access_token" not in token:
        print(json.dumps(token, indent=2, ensure_ascii=False))
        return 1

    claims = decode_jwt_claims(token["access_token"])
    print(
        json.dumps(
            {
                "aud": claims.get("aud"),
                "tid": claims.get("tid"),
                "appid": claims.get("appid"),
                "azp": claims.get("azp"),
                "roles": claims.get("roles"),
                "iss": claims.get("iss"),
                "xms_tcdt": claims.get("xms_tcdt"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    if args.show_access_token:
        print("\nACCESS TOKEN:\n")
        print(token["access_token"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
