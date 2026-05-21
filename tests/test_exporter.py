from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from list_fetcher.models import ListTarget
from list_fetcher.sharepoint import ResolvedList, SharePointExporter, SharePointRestorer


class FakeClient:
    def __init__(self) -> None:
        self.binary_calls: list[str] = []

    def get_json(self, site_url: str, relative_api_url: str):  # noqa: ANN201
        if relative_api_url.endswith("/_api/web/GetList('/sites/finance/Lists/Invoices')"):
            return {"Id": "list-guid", "Title": "Invoices", "Description": "Backup me"}
        raise AssertionError(relative_api_url)

    def get_paged(self, site_url: str, relative_api_url: str):  # noqa: ANN201
        if relative_api_url.startswith("/_api/web/lists?"):
            return [{"Id": "list-guid", "Title": "Invoices", "Hidden": False, "ItemCount": 2, "BaseTemplate": 100, "RootFolder": {"ServerRelativeUrl": "/sites/finance/Lists/Invoices"}}]
        if relative_api_url.endswith("/Fields"):
            return [{"Title": "Title", "InternalName": "Title", "SchemaXml": "<Field />"}]
        if relative_api_url.endswith("/ContentTypes"):
            return [{"Name": "Item", "StringId": "0x01"}]
        if relative_api_url.endswith("/Items?$top=5000"):
            return [{"Id": 1, "Title": "Invoice A", "Attachments": True}, {"Id": 2, "Title": "Invoice B", "Attachments": False}]
        if relative_api_url.endswith("/Items(1)/AttachmentFiles"):
            return [{"FileName": "scan.pdf", "ServerRelativeUrl": "/sites/finance/Lists/Invoices/Attachments/1/scan.pdf"}]
        raise AssertionError(relative_api_url)

    def get_bytes(self, site_url: str, relative_api_url: str) -> bytes:
        self.binary_calls.append(relative_api_url)
        return b"pdf"


class FakeRestoreClient:
    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.json_posts: list[tuple[str, dict]] = []
        self.binary_posts: list[str] = []
        self.created_items = 0

    def try_get_json(self, site_url: str, relative_api_url: str):  # noqa: ANN201
        if self.existing and "GetByTitle('Invoices')" in relative_api_url:
            return {"Id": "already-there", "Title": "Invoices"}
        return None

    def post_json(self, site_url: str, relative_api_url: str, payload: dict, headers=None):  # noqa: ANN001, ANN201
        self.json_posts.append((relative_api_url, payload))
        if relative_api_url == "/_api/web/lists":
            return {"Id": "new-list-guid"}
        if relative_api_url.endswith("/Items"):
            self.created_items += 1
            return {"Id": self.created_items}
        return {}

    def merge_json(self, site_url: str, relative_api_url: str, payload: dict):  # noqa: ANN201
        self.json_posts.append((f"MERGE {relative_api_url}", payload))
        return {}

    def get_json(self, site_url: str, relative_api_url: str):  # noqa: ANN201
        if "GetByTitle('Invoices')?$select=Id,Title,ListItemEntityTypeFullName,RootFolder/ServerRelativeUrl&$expand=RootFolder" in relative_api_url:
            return {
                "Id": "new-list-guid",
                "Title": "Invoices",
                "ListItemEntityTypeFullName": "SP.Data.InvoicesListItem",
                "RootFolder": {"ServerRelativeUrl": "/sites/restore/Lists/Invoices"},
            }
        raise AssertionError(relative_api_url)

    def get_paged(self, site_url: str, relative_api_url: str):  # noqa: ANN201
        if relative_api_url.endswith("/Fields"):
            return [
                {"InternalName": "Title", "Hidden": False, "ReadOnlyField": False, "Sealed": False, "TypeAsString": "Text"},
                {"InternalName": "Amount", "Hidden": False, "ReadOnlyField": False, "Sealed": False, "TypeAsString": "Number"},
            ]
        raise AssertionError(relative_api_url)

    def post_bytes(self, site_url: str, relative_api_url: str, payload: bytes, headers=None):  # noqa: ANN001, ANN201
        self.binary_posts.append(relative_api_url)
        return {}


class ExporterTests(unittest.TestCase):
    def test_resolve_targets_matches_list_path(self) -> None:
        exporter = SharePointExporter(FakeClient())
        targets = exporter.resolve_targets([ListTarget(site_url="https://contoso.sharepoint.com/sites/finance", list_path="/sites/finance/Lists/Invoices", source="file")])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].server_relative_url, "/sites/finance/Lists/Invoices")

    def test_export_writes_manifest_and_attachments(self) -> None:
        messages: list[str] = []
        exporter = SharePointExporter(FakeClient(), status=messages.append)
        target = ResolvedList(
            site_url="https://contoso.sharepoint.com/sites/finance",
            list_id="list-guid",
            title="Invoices",
            server_relative_url="/sites/finance/Lists/Invoices",
            hidden=False,
            base_template=100,
            item_count=2,
            metadata={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            manifest = exporter.export([target], output_dir)
            self.assertEqual(manifest["list_count"], 1)
            top_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(top_manifest["list_count"], 1)
            list_dir = output_dir / "contoso.sharepoint.com__sites__finance" / "Invoices [list-guid]"
            self.assertTrue((list_dir / "list.json").exists())
            self.assertTrue((list_dir / "fields.json").exists())
            self.assertTrue((list_dir / "content_types.json").exists())
            self.assertTrue((list_dir / "items.ndjson").exists())
            self.assertTrue((list_dir / "attachments.json").exists())
            self.assertEqual((list_dir / "attachments" / "1" / "scan.pdf").read_bytes(), b"pdf")
        self.assertTrue(any("Downloading list 'Invoices'" in message for message in messages))
        self.assertTrue(any("Retrieved list 'Invoices'" in message for message in messages))
        self.assertTrue(any("Saved list 'Invoices'" in message for message in messages))

    def test_restore_recreates_list_items_and_attachments(self) -> None:
        messages: list[str] = []
        restorer = SharePointRestorer(FakeRestoreClient(), status=messages.append)
        with tempfile.TemporaryDirectory() as tmp:
            list_dir = Path(tmp) / "Invoices [list-guid]"
            (list_dir / "attachments" / "1").mkdir(parents=True)
            (list_dir / "manifest.json").write_text(json.dumps({"site_url": "https://contoso.sharepoint.com/sites/finance", "paths": {}}), encoding="utf-8")
            (list_dir / "list.json").write_text(
                json.dumps(
                    {
                        "Title": "Invoices",
                        "Description": "Restored invoices",
                        "BaseTemplate": 100,
                        "AllowContentTypes": True,
                        "ContentTypesEnabled": True,
                        "EnableAttachments": True,
                    }
                ),
                encoding="utf-8",
            )
            (list_dir / "fields.json").write_text(
                json.dumps(
                    [
                        {"InternalName": "Title", "FromBaseType": True, "Sealed": False, "SchemaXml": "<Field />"},
                        {"InternalName": "Amount", "FromBaseType": False, "Sealed": False, "SchemaXml": "<Field Name=\"Amount\" />"},
                    ]
                ),
                encoding="utf-8",
            )
            (list_dir / "items.ndjson").write_text(
                "\n".join(
                    [
                        json.dumps({"Id": 1, "Title": "Invoice A", "Amount": 10, "Attachments": True}),
                        json.dumps({"Id": 2, "Title": "Invoice B", "Amount": 20, "Attachments": False}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (list_dir / "attachments.json").write_text(json.dumps([{"item_id": 1, "file_name": "scan.pdf", "local_path": "attachments/1/scan.pdf"}]), encoding="utf-8")
            (list_dir / "attachments" / "1" / "scan.pdf").write_bytes(b"pdf")
            manifest = restorer.restore(list_dir, target_site_url="https://contoso.sharepoint.com/sites/restore")

        self.assertEqual(manifest["list_count"], 1)
        client = restorer._client
        self.assertIn("/_api/web/lists", [call[0] for call in client.json_posts])
        self.assertIn("/_api/web/lists/GetByTitle('Invoices')/Fields/CreateFieldAsXml", [call[0] for call in client.json_posts])
        item_calls = [call for call in client.json_posts if call[0].endswith("/Items")]
        self.assertEqual(len(item_calls), 2)
        self.assertEqual(item_calls[0][1]["Title"], "Invoice A")
        self.assertEqual(client.binary_posts, ["/_api/web/lists/GetByTitle('Invoices')/Items(1)/AttachmentFiles/add(FileName='scan.pdf')"])
        self.assertTrue(any("Checking target site https://contoso.sharepoint.com/sites/restore for existing list 'Invoices'" == message for message in messages))
        self.assertTrue(any("Restoring 2 items for 'Invoices'" == message for message in messages))
        self.assertTrue(any("Finished restoring 'Invoices'" == message for message in messages))

    def test_restore_fails_when_list_exists(self) -> None:
        restorer = SharePointRestorer(FakeRestoreClient(existing=True))
        with tempfile.TemporaryDirectory() as tmp:
            list_dir = Path(tmp) / "Invoices [list-guid]"
            list_dir.mkdir(parents=True)
            (list_dir / "manifest.json").write_text(json.dumps({"site_url": "https://contoso.sharepoint.com/sites/finance", "paths": {}}), encoding="utf-8")
            (list_dir / "list.json").write_text(json.dumps({"Title": "Invoices"}), encoding="utf-8")
            (list_dir / "fields.json").write_text("[]", encoding="utf-8")
            (list_dir / "items.ndjson").write_text("", encoding="utf-8")
            (list_dir / "attachments.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                restorer.restore(list_dir, target_site_url="https://contoso.sharepoint.com/sites/restore")


if __name__ == "__main__":
    unittest.main()
