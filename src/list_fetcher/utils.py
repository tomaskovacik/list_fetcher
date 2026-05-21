from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import ListTarget

SITE_MARKERS = ("sites", "teams")


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(path: Path | None = None) -> None:
    dotenv_path = path or (Path.cwd() / ".env")
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _parse_env_value(value)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE)
    return cleaned.strip("-") or "item"


def safe_path_component(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "-", value.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned, flags=re.UNICODE).strip(" .")
    return cleaned or "item"


def host_and_site_slug(site_url: str) -> str:
    parts = urlsplit(site_url)
    path = parts.path.strip("/").replace("/", "__") or "root"
    return slugify(f"{parts.netloc}__{path}")


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def discover_site_url(full_url: str) -> str:
    parts = urlsplit(full_url)
    segments = [segment for segment in parts.path.split("/") if segment]
    for marker in SITE_MARKERS:
        if marker in segments:
            idx = segments.index(marker)
            if idx + 1 < len(segments):
                site_path = "/" + "/".join(segments[: idx + 2])
                return f"{parts.scheme}://{parts.netloc}{site_path}"
    return f"{parts.scheme}://{parts.netloc}"


def derive_list_path_from_url(full_url: str) -> str:
    parts = urlsplit(full_url)
    decoded_path = unquote(parts.path)
    lowered = decoded_path.lower()

    forms_marker = "/forms/"
    if forms_marker in lowered:
        index = lowered.index(forms_marker)
        candidate = decoded_path[:index]
        return candidate or "/"

    lists_marker = "/lists/"
    if lists_marker in lowered:
        start = lowered.index(lists_marker)
        tail = decoded_path.rstrip("/")
        bits = tail[start:].split("/")
        if len(bits) >= 3:
            return decoded_path[:start] + "/".join(bits[:3])
        return decoded_path
    return decoded_path.rstrip("/") or "/"


def parse_list_target(raw: str) -> ListTarget:
    value = raw.strip()
    if not value or value.startswith("#"):
        raise ValueError("blank")
    return ListTarget(site_url=discover_site_url(value), list_path=derive_list_path_from_url(value), source="file")


def load_list_targets_file(path: Path) -> list[ListTarget]:
    targets: list[ListTarget] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        targets.append(parse_list_target(stripped))
    return targets
