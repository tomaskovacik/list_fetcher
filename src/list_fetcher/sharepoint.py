from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import msal
import requests

from .models import AuthConfig, ListTarget
from .utils import host_and_site_slug, safe_path_component, write_json


@dataclass(frozen=True)
class ResolvedList:
    site_url: str
    list_id: str
    title: str
    server_relative_url: str
    hidden: bool
    base_template: int | None
    item_count: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RestoreSource:
    list_dir: Path
    site_url: str


class EntraTokenProvider:
    def __init__(self, auth: AuthConfig):
        auth.validate()
        self._auth = auth
        self._apps: dict[str, msal.ConfidentialClientApplication] = {}

    def get_token(self, site_url: str) -> str:
        parts = urlsplit(site_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        app = self._apps.get(origin)
        if app is None:
            authority = f"https://login.microsoftonline.com/{self._auth.tenant}"
            app = msal.ConfidentialClientApplication(
                client_id=self._auth.client_id,
                authority=authority,
                client_credential={
                    "thumbprint": self._auth.cert_thumbprint,
                    "private_key": Path(self._auth.cert_path or "").read_text(encoding="utf-8"),
                },
            )
            self._apps[origin] = app

        scopes = [f"{origin}/.default"]
        token = app.acquire_token_silent(scopes, account=None)
        if not token:
            token = app.acquire_token_for_client(scopes=scopes)
        if "access_token" not in token:
            raise RuntimeError(f"Failed to acquire token for {origin}: {json.dumps(token, indent=2, ensure_ascii=False)}")
        return token["access_token"]


class SharePointRestClient:
    def __init__(self, token_provider: EntraTokenProvider):
        self._token_provider = token_provider
        self._session = requests.Session()
        self._form_digests: dict[str, tuple[str, datetime]] = {}

    def close(self) -> None:
        self._session.close()

    def _request(
        self,
        site_url: str,
        relative_api_url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        stream: bool = False,
        json_payload: Any = None,
        data: bytes | None = None,
    ) -> requests.Response:
        token = self._token_provider.get_token(site_url)
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json;odata=nometadata",
        }
        if headers:
            request_headers.update(headers)
        response = self._session.request(
            method,
            f"{site_url.rstrip('/')}{relative_api_url}",
            headers=request_headers,
            stream=stream,
            json=json_payload,
            data=data,
            timeout=120,
        )
        response.raise_for_status()
        return response

    def get_json(self, site_url: str, relative_api_url: str) -> Any:
        return self._request(site_url, relative_api_url).json()

    def try_get_json(self, site_url: str, relative_api_url: str) -> Any | None:
        try:
            return self.get_json(site_url, relative_api_url)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def get_bytes(self, site_url: str, relative_api_url: str) -> bytes:
        return self._request(site_url, relative_api_url, stream=True).content

    def get_paged(self, site_url: str, relative_api_url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url = relative_api_url
        while next_url:
            payload = self.get_json(site_url, next_url)
            page_items = payload.get("value") or []
            items.extend(page_items)
            raw_next = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
            if raw_next and raw_next.startswith(site_url):
                next_url = raw_next[len(site_url.rstrip("/")) :]
            else:
                next_url = raw_next
        return items

    def get_form_digest(self, site_url: str) -> str:
        cached = self._form_digests.get(site_url)
        if cached and cached[1] > datetime.now(UTC):
            return cached[0]
        payload = self._request(
            site_url,
            "/_api/contextinfo",
            method="POST",
            headers={"Accept": "application/json;odata=verbose"},
        ).json()
        info = payload.get("d", {}).get("GetContextWebInformation", {})
        digest = info.get("FormDigestValue")
        timeout_seconds = int(info.get("FormDigestTimeoutSeconds", 1200))
        if not digest:
            raise RuntimeError(f"Failed to read form digest for {site_url}")
        self._form_digests[site_url] = (digest, datetime.now(UTC) + timedelta(seconds=max(timeout_seconds - 30, 1)))
        return digest

    def post_json(self, site_url: str, relative_api_url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
        request_headers = {
            "Content-Type": "application/json;odata=verbose",
            "X-RequestDigest": self.get_form_digest(site_url),
        }
        if headers:
            request_headers.update(headers)
        response = self._request(site_url, relative_api_url, method="POST", headers=request_headers, json_payload=payload)
        return response.json() if response.content else {}

    def post_bytes(self, site_url: str, relative_api_url: str, payload: bytes, *, headers: dict[str, str] | None = None) -> Any:
        request_headers = {
            "Content-Type": "application/octet-stream",
            "X-RequestDigest": self.get_form_digest(site_url),
        }
        if headers:
            request_headers.update(headers)
        response = self._request(site_url, relative_api_url, method="POST", headers=request_headers, data=payload)
        return response.json() if response.content else {}

    def merge_json(self, site_url: str, relative_api_url: str, payload: dict[str, Any]) -> Any:
        return self.post_json(site_url, relative_api_url, payload, headers={"X-HTTP-Method": "MERGE", "If-Match": "*"})


class SharePointExporter:
    def __init__(
        self,
        client: SharePointRestClient,
        include_hidden: bool = False,
        status: Callable[[str], None] | None = None,
    ):
        self._client = client
        self._include_hidden = include_hidden
        self._status_callback = status or (lambda _message: None)

    def _status(self, message: str) -> None:
        self._status_callback(message)

    def discover_lists(self, site_url: str) -> list[ResolvedList]:
        self._status(f"Checking site {site_url} for lists")
        payload = self._client.get_paged(
            site_url,
            "/_api/web/lists?$select=Id,Title,Hidden,ItemCount,BaseTemplate,RootFolder/ServerRelativeUrl&$expand=RootFolder",
        )
        result: list[ResolvedList] = []
        for item in payload:
            if item.get("Hidden") and not self._include_hidden:
                continue
            server_relative_url = item.get("RootFolder", {}).get("ServerRelativeUrl")
            if not server_relative_url:
                continue
            result.append(
                ResolvedList(
                    site_url=site_url,
                    list_id=item["Id"],
                    title=item.get("Title") or item["Id"],
                    server_relative_url=server_relative_url,
                    hidden=bool(item.get("Hidden")),
                    base_template=item.get("BaseTemplate"),
                    item_count=item.get("ItemCount"),
                    metadata=item,
                )
            )
        self._status(f"Found {len(result)} visible lists in {site_url}")
        for item in sorted(result, key=lambda current: current.title.lower()):
            self._status(f"  - {item.title} ({item.server_relative_url})")
        return result

    def resolve_targets(self, targets: list[ListTarget]) -> list[ResolvedList]:
        grouped: dict[str, list[ListTarget]] = defaultdict(list)
        for target in targets:
            grouped[target.site_url].append(target)

        resolved: list[ResolvedList] = []
        for site_url, site_targets in grouped.items():
            discovered = self.discover_lists(site_url)
            if any(target.is_site_discovery for target in site_targets):
                resolved.extend(discovered)
            by_path = {item.server_relative_url.rstrip("/").lower(): item for item in discovered}
            for target in site_targets:
                if target.is_site_discovery or not target.list_path:
                    continue
                match = by_path.get(target.list_path.rstrip("/").lower())
                if match is None:
                    raise RuntimeError(f"List path not found in site {site_url}: {target.list_path}")
                resolved.append(match)

        deduped: dict[tuple[str, str], ResolvedList] = {}
        for item in resolved:
            deduped[(item.site_url, item.server_relative_url)] = item
        return list(deduped.values())

    def export(self, targets: list[ResolvedList], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        ordered_targets = sorted(targets, key=lambda i: (i.site_url, i.title.lower()))
        exported: list[dict[str, Any]] = []
        for index, target in enumerate(ordered_targets, start=1):
            self._status(f"[{index}/{len(ordered_targets)}] Downloading list '{target.title}' from {target.site_url}")
            exported.append(self.export_list(target, output_dir))
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "list_count": len(exported),
            "lists": exported,
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    def export_list(self, target: ResolvedList, output_dir: Path) -> dict[str, Any]:
        site_slug = host_and_site_slug(target.site_url)
        list_dir = output_dir / site_slug / safe_path_component(f"{target.title} [{target.list_id}]")
        attachment_dir = list_dir / "attachments"
        attachment_dir.mkdir(parents=True, exist_ok=True)

        list_api = f"/_api/web/GetList('{quote(target.server_relative_url, safe='/')}')"
        list_data = self._client.get_json(target.site_url, list_api)
        fields = self._client.get_paged(target.site_url, f"{list_api}/Fields")
        content_types = self._client.get_paged(target.site_url, f"{list_api}/ContentTypes")
        items = self._client.get_paged(target.site_url, f"{list_api}/Items?$top=5000")
        self._status(
            f"  Retrieved list '{target.title}': {len(fields)} fields, {len(content_types)} content types, {len(items)} items"
        )

        write_json(list_dir / "list.json", list_data)
        write_json(list_dir / "fields.json", fields)
        write_json(list_dir / "content_types.json", content_types)

        attachments_manifest: list[dict[str, Any]] = []
        with (list_dir / "items.ndjson").open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(dict(item), ensure_ascii=False, sort_keys=True) + "\n")
                if not item.get("Attachments"):
                    continue
                item_id = item.get("Id")
                if item_id is None:
                    continue
                item_attachments = self._client.get_paged(target.site_url, f"{list_api}/Items({item_id})/AttachmentFiles")
                item_attachment_dir = attachment_dir / str(item_id)
                item_attachment_dir.mkdir(parents=True, exist_ok=True)
                for attachment in item_attachments:
                    file_name = attachment.get("FileName") or attachment.get("FileLeafRef") or "attachment.bin"
                    binary = self._client.get_bytes(
                        target.site_url,
                        f"{list_api}/Items({item_id})/AttachmentFiles('{quote(file_name, safe='')}')/$value",
                    )
                    target_path = item_attachment_dir / file_name
                    target_path.write_bytes(binary)
                    attachments_manifest.append(
                        {
                            "item_id": item_id,
                            "file_name": file_name,
                            "server_relative_url": attachment.get("ServerRelativeUrl"),
                            "local_path": str(target_path.relative_to(list_dir)),
                        }
                    )

        write_json(list_dir / "attachments.json", attachments_manifest)
        self._status(f"  Saved list '{target.title}' to {list_dir}")
        self._status(f"  Downloaded {len(attachments_manifest)} attachments for '{target.title}'")
        list_manifest = {
            "site_url": target.site_url,
            "list_id": target.list_id,
            "title": target.title,
            "server_relative_url": target.server_relative_url,
            "base_template": target.base_template,
            "item_count": len(items),
            "attachment_count": len(attachments_manifest),
            "paths": {
                "list": "list.json",
                "fields": "fields.json",
                "content_types": "content_types.json",
                "items": "items.ndjson",
                "attachments": "attachments.json",
                "attachment_root": "attachments",
            },
        }
        write_json(list_dir / "manifest.json", list_manifest)
        return {
            "site_url": target.site_url,
            "list_id": target.list_id,
            "title": target.title,
            "server_relative_url": target.server_relative_url,
            "path": str(list_dir.relative_to(output_dir)),
        }


NON_WRITABLE_FIELD_TYPES = {"Attachments", "Computed", "ContentTypeId", "Counter", "Guid", "ModStat", "WorkflowStatus"}
EXCLUDED_ITEM_FIELDS = {"Attachments", "AuthorId", "ComplianceAssetId", "ContentTypeId", "Created", "EditorId", "FileSystemObjectType", "GUID", "ID", "Id", "Modified", "OData__UIVersionString"}


def escape_odata_value(value: str) -> str:
    return value.replace("'", "''")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            items.append(json.loads(stripped))
    return items


class SharePointRestorer:
    def __init__(self, client: SharePointRestClient, status: Callable[[str], None] | None = None):
        self._client = client
        self._status_callback = status or (lambda _message: None)

    def _status(self, message: str) -> None:
        self._status_callback(message)

    def restore(self, restore_path: Path, target_site_url: str | None = None) -> dict[str, Any]:
        sources = self._load_sources(restore_path, target_site_url)
        restored: list[dict[str, Any]] = []
        for index, source in enumerate(sources, start=1):
            self._status(f"[{index}/{len(sources)}] Restoring from {source.list_dir}")
            restored.append(self.restore_list(source))
        return {"restored_at": datetime.now(UTC).isoformat(), "list_count": len(restored), "lists": restored}

    def _load_sources(self, restore_path: Path, target_site_url: str | None) -> list[RestoreSource]:
        manifest_path = restore_path if restore_path.name == "manifest.json" else restore_path / "manifest.json"
        if manifest_path.exists():
            manifest = load_json(manifest_path)
            if "lists" in manifest and isinstance(manifest["lists"], list):
                return [
                    RestoreSource(list_dir=manifest_path.parent / item["path"], site_url=(target_site_url or item["site_url"]).rstrip("/"))
                    for item in manifest["lists"]
                ]
            if "paths" in manifest:
                return [RestoreSource(list_dir=manifest_path.parent, site_url=(target_site_url or manifest["site_url"]).rstrip("/"))]
        if (restore_path / "list.json").exists():
            single_manifest = load_json(restore_path / "manifest.json")
            return [RestoreSource(list_dir=restore_path, site_url=(target_site_url or single_manifest["site_url"]).rstrip("/"))]
        raise ValueError(f"Restore path does not contain an export manifest: {restore_path}")

    def restore_list(self, source: RestoreSource) -> dict[str, Any]:
        list_data = load_json(source.list_dir / "list.json")
        fields = load_json(source.list_dir / "fields.json")
        items = load_ndjson(source.list_dir / "items.ndjson")
        attachments = load_json(source.list_dir / "attachments.json")

        list_title = list_data["Title"]
        escaped_title = escape_odata_value(list_title)
        self._status(f"Checking target site {source.site_url} for existing list '{list_title}'")
        existing = self._client.try_get_json(source.site_url, f"/_api/web/lists/GetByTitle('{escaped_title}')?$select=Id,Title")
        if existing is not None:
            raise RuntimeError(f"Target list already exists in {source.site_url}: {list_title}")

        self._status(f"Creating list '{list_title}' in {source.site_url}")
        self._client.post_json(
            source.site_url,
            "/_api/web/lists",
            {
                "__metadata": {"type": "SP.List"},
                "Title": list_title,
                "Description": list_data.get("Description") or "",
                "BaseTemplate": list_data.get("BaseTemplate") or 100,
                "AllowContentTypes": bool(list_data.get("AllowContentTypes")),
                "ContentTypesEnabled": bool(list_data.get("ContentTypesEnabled")),
            },
        )

        list_api = f"/_api/web/lists/GetByTitle('{escaped_title}')"
        merge_payload = {
            "__metadata": {"type": "SP.List"},
            **{
                key: list_data[key]
                for key in (
                    "Description",
                    "EnableAttachments",
                    "EnableFolderCreation",
                    "EnableMinorVersions",
                    "EnableModeration",
                    "EnableVersioning",
                    "ForceCheckout",
                    "DraftVersionVisibility",
                    "MajorVersionLimit",
                    "MajorWithMinorVersionsLimit",
                )
                if key in list_data and list_data[key] is not None
            },
        }
        self._client.merge_json(source.site_url, list_api, merge_payload)

        custom_fields = [field for field in fields if self._should_restore_field(field)]
        self._status(f"Restoring {len(custom_fields)} custom fields for '{list_title}'")
        for field in fields:
            if not self._should_restore_field(field):
                continue
            self._client.post_json(
                source.site_url,
                f"{list_api}/Fields/CreateFieldAsXml",
                {"parameters": {"__metadata": {"type": "SP.XmlSchemaFieldCreationInformation"}, "SchemaXml": field["SchemaXml"], "Options": 0}},
            )

        created_list = self._client.get_json(
            source.site_url,
            f"{list_api}?$select=Id,Title,ListItemEntityTypeFullName,RootFolder/ServerRelativeUrl&$expand=RootFolder",
        )
        created_fields = self._client.get_paged(source.site_url, f"{list_api}/Fields")
        writable_fields = self._build_writable_field_names(created_fields)
        item_type_name = created_list["ListItemEntityTypeFullName"]

        id_map: dict[int, int] = {}
        self._status(f"Restoring {len(items)} items for '{list_title}'")
        for item in items:
            created = self._client.post_json(source.site_url, f"{list_api}/Items", self._build_item_payload(item, writable_fields, item_type_name))
            source_id = int(item["Id"])
            created_id = created.get("Id") or created.get("ID")
            if created_id is None:
                raise RuntimeError(f"SharePoint did not return an item id for restored item {source_id}")
            id_map[source_id] = int(created_id)

        self._status(f"Restoring {len(attachments)} attachments for '{list_title}'")
        for attachment in attachments:
            old_id = int(attachment["item_id"])
            if old_id not in id_map:
                raise RuntimeError(f"Attachment refers to missing source item id {old_id}")
            local_path = source.list_dir / attachment["local_path"]
            self._client.post_bytes(
                source.site_url,
                f"{list_api}/Items({id_map[old_id]})/AttachmentFiles/add(FileName='{quote(attachment['file_name'], safe='')}')",
                local_path.read_bytes(),
            )

        self._status(f"Finished restoring '{list_title}'")

        return {
            "site_url": source.site_url,
            "title": list_title,
            "item_count": len(items),
            "attachment_count": len(attachments),
            "source_path": str(source.list_dir),
        }

    def _should_restore_field(self, field: dict[str, Any]) -> bool:
        return bool(field.get("SchemaXml")) and not field.get("FromBaseType") and not field.get("Sealed")

    def _build_writable_field_names(self, fields: list[dict[str, Any]]) -> set[str]:
        writable: set[str] = set()
        for field in fields:
            internal_name = field.get("InternalName")
            if not internal_name:
                continue
            if field.get("Hidden") or field.get("ReadOnlyField") or field.get("Sealed"):
                continue
            if field.get("TypeAsString") in NON_WRITABLE_FIELD_TYPES:
                continue
            writable.add(internal_name)
            if field.get("TypeAsString") in {"Lookup", "LookupMulti", "User", "UserMulti"}:
                writable.add(f"{internal_name}Id")
        return writable

    def _build_item_payload(self, item: dict[str, Any], writable_fields: set[str], item_type_name: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"__metadata": {"type": item_type_name}}
        for key, value in item.items():
            if key.startswith("@odata.") or key in EXCLUDED_ITEM_FIELDS or key not in writable_fields or value is None:
                continue
            if isinstance(value, dict) and "results" not in value:
                continue
            payload[key] = value
        return payload
