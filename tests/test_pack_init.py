"""Tests for ``veyra_pack init`` and the default manifest."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pack_helpers import run_cli

from veyra_pack.contract import PACK_FILENAME, PACK_TOOL_VERSION, SCHEMA_VERSION
from veyra_pack.init_project import init_pack
from veyra_pack.validation import validate_pack


class InitPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_init_creates_manifest_and_directories(self) -> None:
        pack_path = init_pack(
            self.root / "kit", name="Demo Kit", pack_type="character_kit"
        )
        self.assertEqual(pack_path, self.root / "kit" / PACK_FILENAME)
        self.assertTrue((self.root / "kit" / "assets").is_dir())
        self.assertTrue((self.root / "kit" / "files").is_dir())
        data = json.loads(pack_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["pack_type"], "character_kit")
        self.assertEqual(data["name"], "Demo Kit")
        self.assertEqual(data["assets"], [])
        self.assertEqual(data["files"], [])
        self.assertEqual(
            data["tool"], {"name": "veyra-pack", "version": PACK_TOOL_VERSION}
        )
        self.assertIn(PACK_TOOL_VERSION, data["metadata"]["generated_by"])

    def test_init_writes_a_valid_pack(self) -> None:
        init_pack(self.root / "kit", name="Demo Kit", pack_type="desktop_theme")
        result = validate_pack(self.root / "kit")
        self.assertTrue(result.ok, [issue.format() for issue in result.issues])

    def test_init_accepts_all_documented_fields(self) -> None:
        init_pack(
            self.root / "kit",
            name="Star Rail Wallpaper",
            pack_type="desktop_theme",
            description="1920x1080 wallpaper",
            target_game="Honkai: Star Rail",
            author="Vesper",
            pack_id="star-rail-wallpaper-0001",
        )
        data = json.loads((self.root / "kit" / PACK_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(data["pack_id"], "star-rail-wallpaper-0001")
        self.assertEqual(data["target_game"], "Honkai: Star Rail")
        self.assertEqual(data["author"], "Vesper")
        self.assertEqual(data["description"], "1920x1080 wallpaper")

    def test_init_refuses_non_empty_destination(self) -> None:
        target = self.root / "kit"
        target.mkdir()
        (target / "notes.txt").write_text("keep me", encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            init_pack(target, name="Demo Kit", pack_type="character_kit")
        self.assertEqual(getattr(caught.exception, "code", ""), "E_DEST_NOT_EMPTY")
        self.assertEqual((target / "notes.txt").read_text(encoding="utf-8"), "keep me")
        self.assertFalse((target / PACK_FILENAME).exists())

    def test_init_accepts_an_existing_empty_directory(self) -> None:
        target = self.root / "kit"
        target.mkdir()
        init_pack(target, name="Demo Kit", pack_type="character_kit")
        self.assertTrue((target / PACK_FILENAME).is_file())

    def test_init_refuses_a_file_destination(self) -> None:
        target = self.root / "kit"
        target.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(Exception) as caught:
            init_pack(target, name="Demo Kit", pack_type="character_kit")
        self.assertEqual(getattr(caught.exception, "code", ""), "E_DEST_NOT_DIR")

    def test_init_rejects_unknown_pack_type(self) -> None:
        with self.assertRaises(Exception) as caught:
            init_pack(self.root / "kit", name="Demo Kit", pack_type="not-a-type")
        self.assertEqual(getattr(caught.exception, "code", ""), "E_PACK_TYPE")
        self.assertFalse((self.root / "kit").exists())

    def test_init_rejects_empty_name(self) -> None:
        with self.assertRaises(Exception) as caught:
            init_pack(self.root / "kit", name="   ", pack_type="character_kit")
        self.assertEqual(getattr(caught.exception, "code", ""), "E_NAME")

    def test_init_generates_unique_ids(self) -> None:
        first = json.loads(
            init_pack(self.root / "a", name="Same Name", pack_type="other").read_text(
                encoding="utf-8"
            )
        )
        second = json.loads(
            init_pack(self.root / "b", name="Same Name", pack_type="other").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotEqual(first["pack_id"], second["pack_id"])

    def test_init_cli_creates_and_validates(self) -> None:
        code, out, err = run_cli(
            [
                "init",
                str(self.root / "kit"),
                "--name",
                "CLI Kit",
                "--type",
                "asset_pack",
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("created", out)
        code, out, err = run_cli(["validate", str(self.root / "kit")])
        self.assertEqual(code, 0, err)
        self.assertIn("pack valid", out)


if __name__ == "__main__":
    unittest.main()
