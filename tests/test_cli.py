from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from list_fetcher.cli import (
    build_auth_config,
    build_parser,
    collect_exclude_list_ids,
    collect_targets,
    main,
    print_list_summary,
    run_export,
    run_restore,
    validate_mode_args,
)
from list_fetcher.models import ListTarget
from list_fetcher.sharepoint import ResolvedList

from test_exporter import FakeClient, FakeDiscoveryClient, FakeRestoreClient

AUTH_ARGS = ["--tenant", "t", "--client-id", "c", "--cert-path", "x", "--cert-thumbprint", "y"]


def _parse(args: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(args)


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_require_output(self) -> None:
        args = _parse(["--dry-run", "--site-url", "https://contoso.sharepoint.com/sites/finance", *AUTH_ARGS])
        self.assertTrue(args.dry_run)
        self.assertIsNone(args.output)

    def test_print_list_summary_includes_id_title_and_path(self) -> None:
        rows = [
            ResolvedList(
                site_url="https://contoso.sharepoint.com/sites/finance",
                list_id="ad3deed7-2be4-4879-aa75-ae4934b67402",
                title="CARS DB",
                server_relative_url="/sites/finance/Lists/CarsDB",
                hidden=False,
                base_template=100,
                item_count=12,
                metadata={},
            ),
        ]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_list_summary(rows)
        output = buffer.getvalue()
        self.assertIn("ad3deed7-2be4-4879-aa75-ae4934b67402", output)
        self.assertIn("CARS DB", output)
        self.assertIn("/sites/finance/Lists/CarsDB", output)


class CollectTargetsTests(unittest.TestCase):
    def test_collects_site_urls_and_strips_trailing_slash(self) -> None:
        args = _parse(["--site-url", "https://contoso.sharepoint.com/sites/finance/"])
        targets = collect_targets(args)
        self.assertEqual(targets, [ListTarget(site_url="https://contoso.sharepoint.com/sites/finance", source="site")])

    def test_raises_when_no_targets_given(self) -> None:
        with self.assertRaises(ValueError):
            collect_targets(_parse([]))

    def test_collects_from_list_urls_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            urls_file = Path(tmp) / "lists.txt"
            urls_file.write_text(
                "https://contoso.sharepoint.com/sites/finance/Lists/Invoices/AllItems.aspx\n", encoding="utf-8"
            )
            targets = collect_targets(_parse(["--list-urls-file", str(urls_file)]))
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].source, "file")


class CollectExcludeListIdsTests(unittest.TestCase):
    def test_collects_repeatable_flag_values(self) -> None:
        args = _parse(["--exclude-list-id", "id-1", "--exclude-list-id", "id-2"])
        self.assertEqual(collect_exclude_list_ids(args), {"id-1", "id-2"})

    def test_merges_flag_and_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ids_file = Path(tmp) / "exclude.txt"
            ids_file.write_text("# comment\nid-2\n\nid-3\n", encoding="utf-8")
            args = _parse(["--exclude-list-id", "id-1", "--exclude-list-ids-file", str(ids_file)])
            self.assertEqual(collect_exclude_list_ids(args), {"id-1", "id-2", "id-3"})


class BuildAuthConfigTests(unittest.TestCase):
    def test_requires_tenant_and_client_id(self) -> None:
        args = _parse(["--client-id", "c", "--cert-path", "x", "--cert-thumbprint", "y"])
        with self.assertRaises(ValueError):
            build_auth_config(args)

    def test_builds_config_when_all_fields_present(self) -> None:
        config = build_auth_config(_parse(AUTH_ARGS))
        self.assertEqual(config.tenant, "t")
        self.assertEqual(config.client_id, "c")


class ValidateModeArgsTests(unittest.TestCase):
    def test_restore_mode_rejects_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--restore-path", "/tmp/backup", "--dry-run", *AUTH_ARGS])
        with self.assertRaises(SystemExit):
            validate_mode_args(args, parser)

    def test_restore_mode_rejects_output(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--restore-path", "/tmp/backup", "--output", "/tmp/out", *AUTH_ARGS])
        with self.assertRaises(SystemExit):
            validate_mode_args(args, parser)

    def test_restore_mode_returns_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--restore-path", "/tmp/backup", *AUTH_ARGS])
        self.assertIsNone(validate_mode_args(args, parser))

    def test_export_mode_requires_output_unless_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--site-url", "https://contoso.sharepoint.com/sites/finance", *AUTH_ARGS])
        with self.assertRaises(SystemExit):
            validate_mode_args(args, parser)

    def test_export_mode_returns_targets_and_exclusions(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--site-url",
                "https://contoso.sharepoint.com/sites/finance",
                "--output",
                "/tmp/out",
                "--exclude-list-id",
                "id-1",
                *AUTH_ARGS,
            ]
        )
        targets, exclude_ids = validate_mode_args(args, parser)
        self.assertEqual(len(targets), 1)
        self.assertEqual(exclude_ids, {"id-1"})

    def test_export_mode_reports_missing_targets_via_parser_error(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--output", "/tmp/out", *AUTH_ARGS])
        with self.assertRaises(SystemExit):
            validate_mode_args(args, parser)


class RunModeTests(unittest.TestCase):
    def test_run_export_dry_run_prints_summary(self) -> None:
        args = _parse(["--dry-run", "--site-url", "https://contoso.sharepoint.com/sites/finance", *AUTH_ARGS])
        targets, exclude_ids = validate_mode_args(args, build_parser())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            message = run_export(FakeDiscoveryClient(), args, targets, exclude_ids)
        self.assertEqual(message, "2 list(s) matched (dry run, nothing downloaded)")
        self.assertIn("Invoices", buffer.getvalue())

    def test_run_export_writes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "backup"
            args = _parse(
                [
                    "--site-url",
                    "https://contoso.sharepoint.com/sites/finance",
                    "--output",
                    str(output_dir),
                    *AUTH_ARGS,
                ]
            )
            targets, exclude_ids = validate_mode_args(args, build_parser())
            message = run_export(FakeClient(), args, targets, exclude_ids)
        self.assertEqual(message, f"Exported 1 list(s) to {output_dir}")

    def test_run_restore_reports_list_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            list_dir = Path(tmp) / "Invoices [list-guid]"
            (list_dir / "attachments").mkdir(parents=True)
            (list_dir / "manifest.json").write_text(
                json.dumps({"site_url": "https://contoso.sharepoint.com/sites/finance", "paths": {}}), encoding="utf-8"
            )
            (list_dir / "list.json").write_text(
                json.dumps(
                    {
                        "Title": "Invoices",
                        "BaseTemplate": 100,
                        "AllowContentTypes": True,
                        "ContentTypesEnabled": True,
                        "EnableAttachments": True,
                    }
                ),
                encoding="utf-8",
            )
            (list_dir / "fields.json").write_text("[]", encoding="utf-8")
            (list_dir / "items.ndjson").write_text("", encoding="utf-8")
            (list_dir / "attachments.json").write_text("[]", encoding="utf-8")
            args = _parse(["--restore-path", str(list_dir), *AUTH_ARGS])
            message = run_restore(FakeRestoreClient(), args)
        self.assertEqual(message, f"Restored 1 list(s) from {list_dir}")


class _ClosingWrapper:
    """Adds the close() the real SharePointRestClient has, which cli.main() always calls."""

    def __init__(self, inner) -> None:  # noqa: ANN001
        self._inner = inner

    def close(self) -> None:
        pass

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._inner, name)


class MainTests(unittest.TestCase):
    def test_main_exits_nonzero_when_auth_config_invalid(self) -> None:
        # --client-id "" overrides any SP_EXPORT_CLIENT_ID env var, keeping this
        # deterministic regardless of the environment the tests run in.
        with self.assertRaises(SystemExit):
            main(
                [
                    "--site-url",
                    "https://contoso.sharepoint.com/sites/finance",
                    "--output",
                    "/tmp/out",
                    "--client-id",
                    "",
                ]
            )

    def test_main_dry_run_returns_zero_and_prints_summary(self) -> None:
        with patch("list_fetcher.cli.SharePointRestClient", lambda *_a, **_k: _ClosingWrapper(FakeDiscoveryClient())):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    ["--dry-run", "--site-url", "https://contoso.sharepoint.com/sites/finance", *AUTH_ARGS]
                )
        self.assertEqual(exit_code, 0)
        self.assertIn("2 list(s) matched", buffer.getvalue())

    def test_main_returns_one_and_prints_error_on_failure(self) -> None:
        class _BrokenClient:
            def close(self) -> None:
                pass

            def get_paged(self, site_url: str, relative_api_url: str):  # noqa: ANN201
                raise OSError("network unreachable")

        with patch("list_fetcher.cli.SharePointRestClient", lambda *_a, **_k: _BrokenClient()):
            stderr_buffer = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr_buffer):
                exit_code = main(
                    ["--dry-run", "--site-url", "https://contoso.sharepoint.com/sites/finance", *AUTH_ARGS]
                )
        self.assertEqual(exit_code, 1)
        self.assertIn("error: network unreachable", stderr_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
