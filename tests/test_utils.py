from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from list_fetcher.models import ListTarget
from list_fetcher.utils import (
    derive_list_path_from_url,
    discover_site_url,
    load_dotenv,
    load_list_targets_file,
    parse_list_target,
    safe_path_component,
    slugify,
    write_json,
)


class UrlParsingTests(unittest.TestCase):
    def test_discovers_team_site_url(self) -> None:
        self.assertEqual(
            discover_site_url("https://contoso.sharepoint.com/sites/finance/Lists/Invoices/AllItems.aspx"),
            "https://contoso.sharepoint.com/sites/finance",
        )

    def test_derives_classic_list_path(self) -> None:
        self.assertEqual(
            derive_list_path_from_url("https://contoso.sharepoint.com/sites/finance/Lists/Invoices/AllItems.aspx"),
            "/sites/finance/Lists/Invoices",
        )

    def test_derives_document_library_path(self) -> None:
        self.assertEqual(
            derive_list_path_from_url("https://contoso.sharepoint.com/sites/finance/Shared%20Documents/Forms/AllItems.aspx"),
            "/sites/finance/Shared Documents",
        )

    def test_parse_list_target(self) -> None:
        self.assertEqual(
            parse_list_target("https://contoso.sharepoint.com/sites/finance/Lists/Invoices/AllItems.aspx"),
            ListTarget(
                site_url="https://contoso.sharepoint.com/sites/finance",
                list_path="/sites/finance/Lists/Invoices",
                source="file",
            ),
        )

    def test_load_file_skips_blank_and_comment_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lists.txt"
            path.write_text("# comment\n\nhttps://contoso.sharepoint.com/sites/finance/Lists/Invoices/AllItems.aspx\n", encoding="utf-8")
            targets = load_list_targets_file(path)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].list_path, "/sites/finance/Lists/Invoices")

    def test_slugify_keeps_useful_ascii(self) -> None:
        self.assertEqual(slugify("Finance / Invoices 2026"), "Finance-Invoices-2026")

    def test_slugify_preserves_utf_letters(self) -> None:
        self.assertEqual(slugify("Ľudské zdroje / Žiadosti"), "Ľudské-zdroje-Žiadosti")

    def test_safe_path_component_preserves_utf_title(self) -> None:
        self.assertEqual(safe_path_component("Ľudské zdroje / Žiadosti [abc-123]"), "Ľudské zdroje - Žiadosti [abc-123]")

    def test_write_json_preserves_utf_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            write_json(path, {"title": "Ľudské zdroje", "city": "Žilina"})
            content = path.read_text(encoding="utf-8")
        self.assertIn("Ľudské zdroje", content)
        self.assertIn("Žilina", content)
        self.assertNotIn("\\u", content)

    def test_load_dotenv_sets_missing_values_without_overriding_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "SP_EXPORT_TENANT=11111111-2222-3333-4444-555555555555\n"
                "SP_EXPORT_CLIENT_ID='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'\n"
                "export SP_EXPORT_CERT_PATH=/config/sharepoint-app-key.pem\n",
                encoding="utf-8",
            )
            previous_tenant = os.environ.pop("SP_EXPORT_TENANT", None)
            previous_client_id = os.environ.pop("SP_EXPORT_CLIENT_ID", None)
            previous_cert_path = os.environ.get("SP_EXPORT_CERT_PATH")
            os.environ["SP_EXPORT_CERT_PATH"] = "existing"
            try:
                load_dotenv(path)
                self.assertEqual(os.environ["SP_EXPORT_TENANT"], "11111111-2222-3333-4444-555555555555")
                self.assertEqual(os.environ["SP_EXPORT_CLIENT_ID"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
                self.assertEqual(os.environ["SP_EXPORT_CERT_PATH"], "existing")
            finally:
                if previous_tenant is None:
                    os.environ.pop("SP_EXPORT_TENANT", None)
                else:
                    os.environ["SP_EXPORT_TENANT"] = previous_tenant
                if previous_client_id is None:
                    os.environ.pop("SP_EXPORT_CLIENT_ID", None)
                else:
                    os.environ["SP_EXPORT_CLIENT_ID"] = previous_client_id
                if previous_cert_path is None:
                    os.environ.pop("SP_EXPORT_CERT_PATH", None)
                else:
                    os.environ["SP_EXPORT_CERT_PATH"] = previous_cert_path


if __name__ == "__main__":
    unittest.main()
