"""Tests for project-local login storage."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from six2one.auth import delete_login, find_project_root, load_login, request_headers, save_login
from six2one.errors import UsageError


class AuthTests(unittest.TestCase):
    def test_find_project_root_from_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

            found_root = find_project_root(nested)

            self.assertEqual(found_root, root.resolve())

    def test_save_and_load_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            path = save_login(" hexerade ", " fake-api-key ", root)
            credentials = load_login(root)

            self.assertEqual(path, root / ".six2one-login.json")
            self.assertEqual(credentials.username, "hexerade")
            self.assertEqual(credentials.api_key, "fake-api-key")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_delete_login_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = save_login("hexerade", "fake-api-key", root)

            deleted_path = delete_login(root)

            self.assertEqual(deleted_path, path)
            self.assertFalse(path.exists())

    def test_delete_login_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(UsageError):
                delete_login(Path(temp_dir))

    def test_load_login_rejects_malformed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".six2one-login.json").write_text(json.dumps({"username": "hexerade"}), encoding="utf-8")

            with self.assertRaises(UsageError):
                load_login(root)

    def test_request_headers_include_basic_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials = save_login("hexerade", "fake-api-key", Path(temp_dir))
            loaded_credentials = load_login(credentials.parent)
            expected_token = base64.b64encode(b"hexerade:fake-api-key").decode("ascii")

            headers = request_headers(loaded_credentials)

            self.assertEqual(headers["Authorization"], f"Basic {expected_token}")
            self.assertEqual(headers["User-Agent"], "six2one/0.1.0 (by hexerade on e621)")


if __name__ == "__main__":
    unittest.main()
