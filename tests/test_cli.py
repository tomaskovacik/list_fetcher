from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from list_fetcher.cli import build_parser, print_list_summary
from list_fetcher.sharepoint import ResolvedList


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_require_output(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "--dry-run",
                "--site-url",
                "https://contoso.sharepoint.com/sites/finance",
                "--tenant",
                "t",
                "--client-id",
                "c",
            ]
        )
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


if __name__ == "__main__":
    unittest.main()
