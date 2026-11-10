"""Environment selection and isolation without Azure requests."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from environment import load_environment
from compare_model import build_argument_parser, RESPONSES_DEPLOYMENTS


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / ".env").write_text("LEGACY_ONLY=must-not-load\n")
        (self.root / ".env.dev").write_text(
            "AZURE_SEARCH_ENDPOINT=https://dev.example\nDEV_ONLY=yes\n")
        (self.root / ".env.prod").write_text(
            "AZURE_SEARCH_ENDPOINT=https://prod.example\nPROD_ONLY=yes\n")
        self.environ = patch.dict(os.environ, {}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def test_defaults_to_dev_without_legacy_or_prod_values(self):
        self.assertEqual(load_environment([], root=self.root), "dev")
        self.assertEqual(os.environ["AZURE_SEARCH_ENDPOINT"], "https://dev.example")
        self.assertNotIn("PROD_ONLY", os.environ)
        self.assertNotIn("LEGACY_ONLY", os.environ)

    def test_prod_selected_before_parser_builds_defaults(self):
        environment = load_environment(["--env", "prod"], root=self.root)
        args = build_argument_parser(environment).parse_args(["--env", "prod"])
        self.assertEqual(args.search_endpoint, "https://prod.example")
        self.assertEqual(args.storage_account, "aibillingstorageprod")
        self.assertEqual(args.search_index, "ai-billing-search-prod-index")
        self.assertEqual(args.models, ["ai-billing-search-prod-foundry-6.1-sol",
                                       "ai-billing-search-prod-foundry-5.6-sol"])
        self.assertIn(args.models[0], RESPONSES_DEPLOYMENTS)
        self.assertEqual(args.output.parent.parts[-2:], ("results", "prod"))
        self.assertNotIn("DEV_ONLY", os.environ)

    def test_cli_selector_overrides_session_default(self):
        os.environ["PYTHONCLIENT_ENV"] = "prod"
        self.assertEqual(load_environment(["--env=dev"], root=self.root), "dev")

    def test_session_can_select_prod(self):
        os.environ["PYTHONCLIENT_ENV"] = "prod"
        self.assertEqual(load_environment([], root=self.root), "prod")

    def test_exported_settings_override_selected_file(self):
        os.environ["AZURE_SEARCH_ENDPOINT"] = "https://exported.example"
        load_environment(["--env", "prod"], root=self.root)
        args = build_argument_parser("prod").parse_args([])
        self.assertEqual(args.search_endpoint, "https://exported.example")
        args = build_argument_parser("prod").parse_args(["--search-endpoint", "https://cli.example"])
        self.assertEqual(args.search_endpoint, "https://cli.example")

    def test_missing_prod_does_not_fall_back(self):
        (self.root / ".env.prod").unlink()
        with self.assertRaises(SystemExit):
            load_environment(["--env", "prod"], root=self.root)
        self.assertNotIn("DEV_ONLY", os.environ)
        self.assertNotIn("LEGACY_ONLY", os.environ)

    def test_invalid_session_environment_is_rejected(self):
        os.environ["PYTHONCLIENT_ENV"] = "production"
        with self.assertRaises(SystemExit):
            load_environment([], root=self.root)

    def test_help_works_without_environment_files(self):
        (self.root / ".env.prod").unlink()
        self.assertEqual(load_environment(["--env", "prod", "--help"], root=self.root), "prod")

    def test_output_paths_are_separate(self):
        dev = build_argument_parser("dev").parse_args([])
        prod = build_argument_parser("prod").parse_args([])
        self.assertNotEqual(dev.output, prod.output)


if __name__ == "__main__":
    unittest.main()
