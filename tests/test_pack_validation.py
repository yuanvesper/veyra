"""Validation tests: contract fields, safe paths, digests and limits."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pack_helpers import asset_entry, base_manifest, file_entry, png_bytes, sha256, write_pack

from veyra_pack.contract import Limits, PackError
from veyra_pack.validation import validate_pack


class ValidationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pack = self.root / "pack"

    def codes(self, data, files=None, *, manifest_text=None, limits=None):
        write_pack(self.pack, data, files, manifest_text=manifest_text)
        result = validate_pack(self.pack, limits)
        return result, {issue.code for issue in result.errors}

    def warning_codes(self, result) -> set[str]:
        return {issue.code for issue in result.warnings}


class ValidPackTests(ValidationTestCase):
    def test_empty_pack_is_valid_without_warnings(self) -> None:
        result, errors = self.codes(base_manifest())
        self.assertTrue(result.ok, errors)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.resolved_files, {})

    def test_declared_file_is_resolved_and_hashed(self) -> None:
        payload = b"hello pack"
        data = base_manifest(files=[file_entry("files/readme.txt", payload)])
        result, errors = self.codes(data, {"files/readme.txt": payload})
        self.assertTrue(result.ok, errors)
        self.assertIn("files/readme.txt", result.resolved_files)

    def test_unknown_top_level_field_is_preserved_with_warning(self) -> None:
        data = base_manifest(future_field={"planned": True})
        result, errors = self.codes(data)
        self.assertTrue(result.ok, errors)
        self.assertIn("W_UNKNOWN_FIELD", self.warning_codes(result))


class ManifestErrorTests(ValidationTestCase):
    def test_missing_required_field(self) -> None:
        data = base_manifest()
        del data["pack_type"]
        result, errors = self.codes(data)
        self.assertFalse(result.ok)
        self.assertIn("E_FIELD_MISSING", errors)
        self.assertTrue(
            any(issue.location == "pack.json#/pack_type" for issue in result.errors)
        )

    def test_invalid_json_reports_line_and_column(self) -> None:
        result, errors = self.codes(None, manifest_text='{"schema_version": }')
        self.assertFalse(result.ok)
        self.assertIn("E_JSON_PARSE", errors)
        location = result.errors[0].location
        self.assertTrue(location.startswith("pack.json:1:"), location)

    def test_root_must_be_an_object(self) -> None:
        result, errors = self.codes(None, manifest_text="[]")
        self.assertFalse(result.ok)
        self.assertIn("E_JSON_ROOT", errors)

    def test_unknown_schema_major_is_rejected(self) -> None:
        result, errors = self.codes(base_manifest(schema_version="2.0.0"))
        self.assertIn("E_SCHEMA_UNSUPPORTED", errors)

    def test_unknown_schema_minor_is_rejected(self) -> None:
        result, errors = self.codes(base_manifest(schema_version="1.1.0"))
        self.assertIn("E_SCHEMA_UNSUPPORTED", errors)

    def test_malformed_schema_version_is_rejected(self) -> None:
        result, errors = self.codes(base_manifest(schema_version="v1"))
        self.assertIn("E_SCHEMA_VERSION", errors)

    def test_unknown_pack_type_is_rejected(self) -> None:
        result, errors = self.codes(base_manifest(pack_type="wallpaper"))
        self.assertIn("E_PACK_TYPE", errors)

    def test_bad_metadata_type_is_rejected(self) -> None:
        result, errors = self.codes(base_manifest(metadata=["not", "an", "object"]))
        self.assertIn("E_FIELD_TYPE", errors)

    def test_tool_block_must_be_an_object(self) -> None:
        result, errors = self.codes(base_manifest(tool="veyra-pack"))
        self.assertIn("E_FIELD_TYPE", errors)

    def test_tool_block_requires_name_and_version(self) -> None:
        result, errors = self.codes(base_manifest(tool={"name": "veyra-pack"}))
        self.assertIn("E_FIELD_MISSING", errors)
        self.assertTrue(
            any(issue.location == "pack.json#/tool/version" for issue in result.errors)
        )

    def test_missing_manifest_raises(self) -> None:
        self.pack.mkdir()
        with self.assertRaises(PackError) as caught:
            validate_pack(self.pack)
        self.assertEqual(caught.exception.code, "E_MANIFEST_MISSING")

    def test_missing_root_raises(self) -> None:
        with self.assertRaises(PackError) as caught:
            validate_pack(self.root / "does-not-exist")
        self.assertEqual(caught.exception.code, "E_ROOT_MISSING")


class PathSafetyTests(ValidationTestCase):
    def _expect(self, rel: str, code: str) -> None:
        data = base_manifest(files=[{"path": rel}])
        result, errors = self.codes(data)
        self.assertFalse(result.ok, rel)
        self.assertIn(code, errors, [issue.format() for issue in result.errors])

    def test_parent_traversal(self) -> None:
        self._expect("../outside.txt", "E_PATH_TRAVERSAL")

    def test_nested_parent_traversal(self) -> None:
        self._expect("assets/../../outside.txt", "E_PATH_TRAVERSAL")

    def test_absolute_path(self) -> None:
        self._expect("/etc/passwd", "E_PATH_ABSOLUTE")

    def test_windows_drive_path(self) -> None:
        self._expect("C:/Windows/win.ini", "E_PATH_DRIVE")

    def test_unc_path(self) -> None:
        self._expect("//server/share/file.txt", "E_PATH_ABSOLUTE")

    def test_backslash_path(self) -> None:
        self._expect("assets\\file.txt", "E_PATH_BACKSLASH")

    def test_reserved_manifest_name(self) -> None:
        data = base_manifest(files=[{"path": "pack.json"}])
        result, errors = self.codes(data)
        self.assertIn("E_RESERVED_PATH", errors)

    def test_missing_file(self) -> None:
        data = base_manifest(files=[{"path": "assets/missing.png"}])
        result, errors = self.codes(data)
        self.assertIn("E_FILE_MISSING", errors)

    def test_directory_is_not_a_file(self) -> None:
        self.pack.mkdir()
        (self.pack / "assets" / "folder").mkdir(parents=True)
        data = base_manifest(files=[{"path": "assets/folder"}])
        result, errors = self.codes(data)
        self.assertIn("E_NOT_A_FILE", errors)

    def test_symlink_is_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_bytes(b"secret")
        self.pack.mkdir()
        (self.pack / "assets").mkdir()
        link = self.pack / "assets" / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available in this environment")
        data = base_manifest(files=[{"path": "assets/link.txt"}])
        result, errors = self.codes(data)
        self.assertIn("E_SYMLINK", errors)

    def test_windows_junction_is_rejected(self) -> None:
        if os.name != "nt":
            self.skipTest("NTFS junctions are Windows-only")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.txt").write_bytes(b"outside content")
        self.pack.mkdir()
        (self.pack / "assets").mkdir()
        link = self.pack / "assets" / "linked"
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            self.skipTest(f"cannot create a junction: {completed.stderr}")
        try:
            data = base_manifest(files=[{"path": "assets/linked/file.txt"}])
            result, errors = self.codes(data)
            self.assertIn("E_SYMLINK", errors)
        finally:
            try:
                os.rmdir(link)
            except OSError:
                pass


class DigestAndLimitTests(ValidationTestCase):
    def test_hash_mismatch(self) -> None:
        actual = b"actual"
        entry = file_entry("files/a.txt", actual)
        entry["sha256"] = sha256(b"declared")
        result, errors = self.codes(base_manifest(files=[entry]), {"files/a.txt": actual})
        self.assertIn("E_HASH_MISMATCH", errors)

    def test_size_mismatch(self) -> None:
        actual = b"actual"
        entry = file_entry("files/a.txt", actual)
        entry["size_bytes"] = len(actual) + 1
        result, errors = self.codes(base_manifest(files=[entry]), {"files/a.txt": actual})
        self.assertIn("E_SIZE_MISMATCH", errors)

    def test_per_file_size_limit(self) -> None:
        payload = b"1234567890"
        data = base_manifest(files=[file_entry("files/a.bin", payload)])
        result, errors = self.codes(
            data, {"files/a.bin": payload}, limits=Limits(max_file_bytes=4)
        )
        self.assertIn("E_FILE_TOO_LARGE", errors)

    def test_total_size_limit(self) -> None:
        payload = b"1234567890"
        data = base_manifest(files=[file_entry("files/a.bin", payload)])
        result, errors = self.codes(
            data, {"files/a.bin": payload}, limits=Limits(max_total_bytes=4)
        )
        self.assertIn("E_TOTAL_TOO_LARGE", errors)

    def test_duplicate_file_paths_are_case_insensitive(self) -> None:
        payload = b"x"
        data = base_manifest(
            files=[file_entry("files/a.txt", payload), {"path": "files/A.TXT"}]
        )
        result, errors = self.codes(data, {"files/a.txt": payload})
        self.assertIn("E_DUPLICATE_FILE_PATH", errors)


class AssetTests(ValidationTestCase):
    def test_image_dimensions_are_verified(self) -> None:
        payload = png_bytes(2, 3)
        data = base_manifest(
            files=[file_entry("assets/bg.png", payload)],
            assets=[asset_entry("bg", "assets/bg.png", width=2, height=3)],
        )
        result, errors = self.codes(data, {"assets/bg.png": payload})
        self.assertTrue(result.ok, errors)

    def test_image_dimension_mismatch(self) -> None:
        payload = png_bytes(2, 3)
        data = base_manifest(
            files=[file_entry("assets/bg.png", payload)],
            assets=[asset_entry("bg", "assets/bg.png", width=4, height=5)],
        )
        result, errors = self.codes(data, {"assets/bg.png": payload})
        self.assertIn("E_IMAGE_DIMENSION_MISMATCH", errors)

    def test_image_dimension_limit(self) -> None:
        payload = png_bytes(2, 3)
        data = base_manifest(
            files=[file_entry("assets/bg.png", payload)],
            assets=[asset_entry("bg", "assets/bg.png")],
        )
        result, errors = self.codes(
            data, {"assets/bg.png": payload}, limits=Limits(max_image_dimension=1)
        )
        self.assertIn("E_IMAGE_TOO_LARGE", errors)

    def test_missing_license_is_a_warning(self) -> None:
        payload = png_bytes()
        entry = {"id": "bg", "path": "assets/bg.png", "kind": "image"}
        data = base_manifest(
            files=[file_entry("assets/bg.png", payload)], assets=[entry]
        )
        result, errors = self.codes(data, {"assets/bg.png": payload})
        self.assertTrue(result.ok, errors)
        self.assertIn("W_LICENSE_MISSING", self.warning_codes(result))

    def test_duplicate_asset_id(self) -> None:
        payload = png_bytes()
        data = base_manifest(
            files=[file_entry("assets/a.png", payload)],
            assets=[
                asset_entry("bg", "assets/a.png"),
                asset_entry("bg", "assets/b.png"),
            ],
        )
        result, errors = self.codes(
            data, {"assets/a.png": payload, "assets/b.png": payload}
        )
        self.assertIn("E_DUPLICATE_ASSET_ID", errors)

    def test_duplicate_asset_path(self) -> None:
        payload = png_bytes()
        data = base_manifest(
            files=[file_entry("assets/a.png", payload)],
            assets=[
                asset_entry("one", "assets/a.png"),
                asset_entry("two", "assets/a.png"),
            ],
        )
        result, errors = self.codes(data, {"assets/a.png": payload})
        self.assertIn("E_DUPLICATE_ASSET_PATH", errors)

    def test_unknown_asset_kind(self) -> None:
        payload = png_bytes()
        data = base_manifest(
            files=[file_entry("assets/a.png", payload)],
            assets=[asset_entry("one", "assets/a.png", kind="hologram")],
        )
        result, errors = self.codes(data, {"assets/a.png": payload})
        self.assertIn("E_ASSET_KIND", errors)

    def test_unknown_asset_field_warns(self) -> None:
        payload = png_bytes()
        data = base_manifest(
            files=[file_entry("assets/a.png", payload)],
            assets=[asset_entry("one", "assets/a.png", future_key="value")],
        )
        result, errors = self.codes(data, {"assets/a.png": payload})
        self.assertTrue(result.ok, errors)
        self.assertIn("W_UNKNOWN_FIELD", self.warning_codes(result))


if __name__ == "__main__":
    unittest.main()
