from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthConfig:
    tenant: str
    client_id: str
    cert_path: str | None = None
    cert_thumbprint: str | None = None

    def validate(self) -> None:
        if self.cert_path and self.cert_thumbprint:
            return
        raise ValueError("Authentication requires both --cert-path and --cert-thumbprint.")


@dataclass(frozen=True)
class ListTarget:
    site_url: str
    list_path: str | None = None
    source: str = "site"

    @property
    def is_site_discovery(self) -> bool:
        return self.list_path is None
