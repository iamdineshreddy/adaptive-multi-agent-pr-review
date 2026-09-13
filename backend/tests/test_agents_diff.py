"""Unit tests for the unified-diff parser and slicing (docs/AGENTS.md §3)."""

from __future__ import annotations

from app.agents.contract import ChangeKind
from app.agents.diff_utils import (
    changed_file_slices,
    file_slices_from_patches,
    parse_patch,
)

_SINGLE_HUNK = """@@ -1,3 +1,3 @@
 def login(user):
-    return user.authenticate()
+    return require_active(user) and user.authenticate()
@@ -20,5 +21,7 @@
     if not user:
         return None
+    audit.login(user)
+    log.info("login", user=user.id)
 return user"""

_MULTI_HUNK = """@@ -1,3 +1,3 @@
 a
-b
+c
@@ -10,2 +11,2 @@
 d
-e
+f"""

_NO_PATCH = ""


class TestParsePatch:
    def test_single_hunk_with_added_lines(self) -> None:
        hunks = parse_patch(_SINGLE_HUNK)
        assert len(hunks) == 2
        first = hunks[0]
        assert first.header.startswith("@@ -1,3 +1,3 @@")
        # line 2 of the new file is the added line
        assert 2 in first.added_lines

    def test_multiple_hunks(self) -> None:
        hunks = parse_patch(_MULTI_HUNK)
        assert [h.header for h in hunks] == [
            "@@ -1,3 +1,3 @@",
            "@@ -10,2 +11,2 @@",
        ]

    def test_hunk_without_counts_defaults_to_one(self) -> None:
        one_line = "@@ -4 +8 @@\n-old\n+new\n"
        hunks = parse_patch(one_line)
        assert hunks[0].old_count == 1
        assert hunks[0].new_count == 1
        assert hunks[0].old_start == 4
        assert hunks[0].new_start == 8

    def test_empty_patch_yields_no_hunks(self) -> None:
        assert parse_patch("") == []


class TestChangedFileSlices:
    def test_builds_bounded_slice_with_inferred_kind(self) -> None:
        slices = changed_file_slices("app/auth.py", _SINGLE_HUNK, "modified")
        assert len(slices) == 2
        first = slices[0]
        assert first.file_path == "app/auth.py"
        assert first.change_kind == ChangeKind.MODIFIED
        assert first.new_start == 1
        assert first.new_end == 3
        assert "@@ -1,3 +1,3 @@" in first.patch

    def test_added_status_mapping(self) -> None:
        slices = changed_file_slices("new.py", None, "added")
        assert slices[0].change_kind == ChangeKind.ADDED

    def test_removed_status_mapping(self) -> None:
        slices = changed_file_slices("gone.py", None, "removed")
        assert slices[0].change_kind == ChangeKind.REMOVED
        assert "no diff window" in slices[0].patch

    def test_file_slices_from_patches(self) -> None:
        slices = file_slices_from_patches(
            [("a.py", _MULTI_HUNK, "modified"), ("b.py", None, "renamed")]
        )
        assert [s.file_path for s in slices] == ["a.py", "a.py", "b.py"]
