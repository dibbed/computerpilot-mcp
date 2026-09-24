from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from core.errors import ToolError

MODIFY_AND_CREATE = """diff --git a/one.txt b/one.txt
--- a/one.txt
+++ b/one.txt
@@ -1,2 +1,2 @@
 alpha
-old
+new
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+created
+file
"""


def test_parse_unified_patch_supports_modify_and_create() -> None:
    from tools.filesystem.patches import PatchLimits, parse_unified_patch

    patch = parse_unified_patch(MODIFY_AND_CREATE, PatchLimits(max_bytes=100_000, max_files=10, max_hunks=20))

    assert [item.kind for item in patch.files] == ["modify", "create"]
    assert patch.files[0].old_path == "one.txt"
    assert patch.files[1].new_path == "new.txt"
    assert sum(len(item.hunks) for item in patch.files) == 2


@pytest.mark.parametrize(
    "header",
    [
        "diff --git a/../escape.txt b/../escape.txt\n--- a/../escape.txt\n+++ b/../escape.txt\n",
        "diff --git C:/escape.txt C:/escape.txt\n--- C:/escape.txt\n+++ C:/escape.txt\n",
        "diff --git //server/share/file //server/share/file\n--- //server/share/file\n+++ //server/share/file\n",
        "diff --cc combined.txt\n",
        "GIT binary patch\n",
        "diff --git a/file.txt b/file.txt\nold mode 100644\nnew mode 100755\n",
    ],
)
def test_parse_unified_patch_rejects_unsafe_or_unsupported_input(header: str) -> None:
    from tools.filesystem.patches import PatchLimits, parse_unified_patch

    with pytest.raises(ToolError) as raised:
        parse_unified_patch(header, PatchLimits())

    assert raised.value.code in {"invalid_patch_path", "unsupported_patch", "invalid_patch"}


def test_parse_unified_patch_rejects_malformed_hunk_counts() -> None:
    from tools.filesystem.patches import PatchLimits, parse_unified_patch

    malformed = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,2 @@
-before
+after
"""
    with pytest.raises(ToolError) as raised:
        parse_unified_patch(malformed, PatchLimits())
    assert raised.value.code == "invalid_patch"


def test_apply_patch_dry_run_reports_changes_without_mutating(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    target = tmp_path / "one.txt"
    target.write_text("alpha\nold\n", encoding="utf-8")

    result = apply_patch_transaction(tmp_path, MODIFY_AND_CREATE, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert target.read_text(encoding="utf-8") == "alpha\nold\n"
    assert not (tmp_path / "new.txt").exists()
    assert [item["operation"] for item in result["files"]] == ["modify", "create"]


def test_apply_patch_applies_multiple_files_and_checks_expected_hashes(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    target = tmp_path / "one.txt"
    target.write_text("alpha\nold\n", encoding="utf-8")
    expected = hashlib.sha256(target.read_bytes()).hexdigest()

    result = apply_patch_transaction(tmp_path, MODIFY_AND_CREATE, expected_sha256={"one.txt": expected})

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "alpha\nnew\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "created\nfile\n"
    assert all(item["sha256_after"] for item in result["files"])


def test_apply_patch_rejects_stale_expected_hash_without_mutating(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    target = tmp_path / "one.txt"
    target.write_text("alpha\nold\n", encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        apply_patch_transaction(tmp_path, MODIFY_AND_CREATE, expected_sha256={"one.txt": "0" * 64})

    assert raised.value.code == "hash_conflict"
    assert target.read_text(encoding="utf-8") == "alpha\nold\n"
    assert not (tmp_path / "new.txt").exists()


def test_apply_patch_rolls_back_every_file_when_validation_fails(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    target = tmp_path / "one.txt"
    target.write_text("alpha\nold\n", encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        apply_patch_transaction(
            tmp_path,
            MODIFY_AND_CREATE,
            validation_command=[sys.executable, "-c", "raise SystemExit(7)"],
            validation_cwd=tmp_path,
        )

    assert raised.value.code == "patch_validation_failed"
    assert target.read_text(encoding="utf-8") == "alpha\nold\n"
    assert not (tmp_path / "new.txt").exists()


def test_apply_patch_rejects_python_syntax_before_any_write(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    first = tmp_path / "first.txt"
    module = tmp_path / "module.py"
    first.write_text("old\n", encoding="utf-8")
    module.write_text("value = 1\n", encoding="utf-8")
    patch = """diff --git a/first.txt b/first.txt
--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-old
+new
diff --git a/module.py b/module.py
--- a/module.py
+++ b/module.py
@@ -1 +1 @@
-value = 1
+def broken(
"""

    with pytest.raises(ToolError) as raised:
        apply_patch_transaction(tmp_path, patch)

    assert raised.value.code == "python_syntax_error"
    assert first.read_text(encoding="utf-8") == "old\n"
    assert module.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_patch_restores_preimages_after_intermediate_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.filesystem import patches

    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before-one\n", encoding="utf-8")
    second.write_text("before-two\n", encoding="utf-8")
    patch = """diff --git a/first.txt b/first.txt
--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-before-one
+after-one
diff --git a/second.txt b/second.txt
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-before-two
+after-two
diff --git a/created.txt b/created.txt
new file mode 100644
--- /dev/null
+++ b/created.txt
@@ -0,0 +1 @@
+created
"""
    real_write = patches._write_bytes
    writes = 0

    def fail_second_write(path: Path, content: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected write failure")
        real_write(path, content)

    monkeypatch.setattr(patches, "_write_bytes", fail_second_write)
    with pytest.raises(OSError, match="injected write failure"):
        patches.apply_patch_transaction(tmp_path, patch)

    assert first.read_text(encoding="utf-8") == "before-one\n"
    assert second.read_text(encoding="utf-8") == "before-two\n"
    assert not (tmp_path / "created.txt").exists()


def test_apply_patch_supports_delete_and_rename_with_crlf(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    deleted = tmp_path / "deleted.txt"
    renamed = tmp_path / "old.txt"
    deleted.write_text("gone\r\n", encoding="utf-8", newline="")
    renamed.write_text("keep\r\n", encoding="utf-8", newline="")
    patch = """diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
--- a/deleted.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
diff --git a/old.txt b/new.txt
similarity index 100%
rename from old.txt
rename to new.txt
"""

    result = apply_patch_transaction(tmp_path, patch)

    assert result["ok"] is True
    assert not deleted.exists()
    assert not renamed.exists()
    assert (tmp_path / "new.txt").read_bytes() == b"keep\r\n"


def test_apply_patch_preserves_no_newline_marker_for_created_file(tmp_path: Path) -> None:
    from tools.filesystem.patches import apply_patch_transaction

    patch = r"""diff --git a/no-newline.txt b/no-newline.txt
new file mode 100644
--- /dev/null
+++ b/no-newline.txt
@@ -0,0 +1 @@
+value
\ No newline at end of file
"""

    apply_patch_transaction(tmp_path, patch)

    assert (tmp_path / "no-newline.txt").read_bytes() == b"value"
