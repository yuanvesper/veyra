"""Export tests: archive contents, determinism, overwrite safety, atomicity."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from pack_helpers import asset_entry, base_manifest, file_entry, png_bytes

from veyra_pack.contract import Limits, PackError
from veyra_pack.exporter import ExportError, _verify_archive, export_pack


class ExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pack = self.root / "pack"

    def build_pack(self, *, with_asset: bool = True) -> Path:
        self.pack.mkdir(parents=True)
        (self.pack / "files").mkdir()
        (self.pack / "assets").mkdir()
        payload = b"hello pack\n"
        (self.pack / "files" / "readme.txt").write_bytes(payload)
        png = png_bytes(2, 3)
        (self.pack / "assets" / "bg.png").write_bytes(png)
        data = base_manifest(
            files=[
                file_entry("files/readme.txt", payload),
                file_entry("assets/bg.png", png),
            ]
        )
        if with_asset:
            data["assets"] = [asset_entry("bg", "assets/bg.png", width=2, height=3)]
        (self.pack / "pack.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        return self.pack


class ExportSuccessTests(ExportTestCase):
    def test_archive_contains_manifest_and_declared_files(self) -> None:
        self.build_pack()
        result = export_pack(self.pack, self.root / "out.zip")
        self.assertTrue(result.output.is_file())
        self.assertEqual(
            result.entries, ("pack.json", "assets/bg.png", "files/readme.txt")
        )
        with zipfile.ZipFile(result.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(
                archive.namelist(), ["pack.json", "assets/bg.png", "files/readme.txt"]
            )
            self.assertEqual(
                archive.read("files/readme.txt"), (self.pack / "files" / "readme.txt").read_bytes()
            )
            self.assertEqual(
                archive.read("pack.json"), (self.pack / "pack.json").read_bytes()
            )

    def test_export_is_byte_for_byte_reproducible(self) -> None:
        self.build_pack()
        first = export_pack(self.pack, self.root / "first.zip")
        second = export_pack(self.pack, self.root / "second.zip")
        self.assertEqual(first.output.read_bytes(), second.output.read_bytes())

    def test_undeclared_files_are_not_exported(self) -> None:
        self.build_pack()
        (self.pack / "files" / "scratch.tmp").write_bytes(b"not declared")
        result = export_pack(self.pack, self.root / "out.zip")
        self.assertNotIn("files/scratch.tmp", result.entries)

    def test_output_parent_directories_are_created(self) -> None:
        self.build_pack()
        result = export_pack(self.pack, self.root / "dist" / "nested" / "out.zip")
        self.assertTrue(result.output.is_file())

    def test_default_output_name_uses_id_and_version(self) -> None:
        self.build_pack()
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            result = export_pack(self.pack)
        finally:
            os.chdir(previous)
        self.assertEqual(result.output.name, "demo-kit-1234abcd-0.1.0.zip")
        self.assertTrue((self.root / result.output.name).is_file())


class ExportSafetyTests(ExportTestCase):
    def test_invalid_pack_is_not_exported(self) -> None:
        self.build_pack()
        data = json.loads((self.pack / "pack.json").read_text(encoding="utf-8"))
        data["files"][0]["sha256"] = "0" * 64
        (self.pack / "pack.json").write_text(json.dumps(data), encoding="utf-8")
        output = self.root / "out.zip"
        with self.assertRaises(ExportError) as caught:
            export_pack(self.pack, output)
        self.assertFalse(output.exists())
        self.assertIn("E_HASH_MISMATCH", {issue.code for issue in caught.exception.issues})

    def test_invalid_pack_does_not_touch_an_existing_output(self) -> None:
        self.build_pack()
        output = self.root / "out.zip"
        output.write_bytes(b"previous archive")
        data = json.loads((self.pack / "pack.json").read_text(encoding="utf-8"))
        data["files"] = [{"path": "files/missing.txt"}]
        (self.pack / "pack.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ExportError):
            export_pack(self.pack, output)
        self.assertEqual(output.read_bytes(), b"previous archive")

    def test_existing_output_requires_overwrite(self) -> None:
        self.build_pack()
        output = self.root / "out.zip"
        output.write_bytes(b"old")
        with self.assertRaises(ExportError) as caught:
            export_pack(self.pack, output)
        self.assertEqual(
            {issue.code for issue in caught.exception.issues}, {"E_OUTPUT_EXISTS"}
        )
        self.assertEqual(output.read_bytes(), b"old")
        export_pack(self.pack, output, overwrite=True)
        self.assertTrue(zipfile.is_zipfile(output))

    def test_output_inside_pack_is_refused(self) -> None:
        self.build_pack()
        with self.assertRaises(ExportError) as caught:
            export_pack(self.pack, self.pack / "out.zip")
        self.assertEqual(
            {issue.code for issue in caught.exception.issues}, {"E_EXPORT_INSIDE_PROJECT"}
        )
        self.assertFalse((self.pack / "out.zip").exists())

    def test_failed_replace_leaves_no_output_or_temporary_file(self) -> None:
        self.build_pack()
        output = self.root / "out.zip"
        with mock.patch(
            "veyra_pack.exporter.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                export_pack(self.pack, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".veyra-export-*")), [])

    def test_missing_manifest_raises(self) -> None:
        self.pack.mkdir()
        with self.assertRaises(PackError) as caught:
            export_pack(self.pack, self.root / "out.zip")
        self.assertEqual(caught.exception.code, "E_MANIFEST_MISSING")


class ArchiveVerificationTests(ExportTestCase):
    def test_duplicate_zip_entries_are_detected(self) -> None:
        duplicate = self.root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("pack.json", b"{}")
                archive.writestr("pack.json", b"{}")
        with self.assertRaises(ExportError) as caught:
            _verify_archive(duplicate, {}, b"{}", ["pack.json"])
        self.assertEqual(
            {issue.code for issue in caught.exception.issues}, {"E_ZIP_DUPLICATE"}
        )

    def test_member_mismatch_is_detected(self) -> None:
        broken = self.root / "broken.zip"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("pack.json", b"{}")
            archive.writestr("files/a.txt", b"changed")
        source = self.root / "a.txt"
        source.write_bytes(b"original")
        with self.assertRaises(ExportError) as caught:
            _verify_archive(broken, {"files/a.txt": source}, b"{}", ["pack.json", "files/a.txt"])
        self.assertIn("E_ZIP_CONTENT", {issue.code for issue in caught.exception.issues})


class ExportLimitTests(ExportTestCase):
    def test_export_honours_custom_limits(self) -> None:
        self.build_pack()
        with self.assertRaises(ExportError) as caught:
            export_pack(
                self.pack,
                self.root / "out.zip",
                limits=Limits(max_total_bytes=4),
            )
        self.assertIn("E_TOTAL_TOO_LARGE", {issue.code for issue in caught.exception.issues})


if __name__ == "__main__":
    unittest.main()
