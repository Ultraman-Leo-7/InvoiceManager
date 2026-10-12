import hashlib
from pathlib import Path

import pytest

import duplicate_files
from duplicate_files import (
    DUPLICATE_PREFIX,
    build_duplicate_groups,
    cleanup_duplicate_files,
    pending_duplicate_groups,
    rename_duplicate_files,
)


def make_copies(folder, names, content=b"same invoice"):
    for name in names:
        (folder / name).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_grouping_is_read_only_deterministic_and_prefers_previous_name(tmp_path):
    digest = make_copies(tmp_path, ["b.pdf", "A.pdf", "c.pdf"])
    inputs = {digest: ["b.pdf", "c.pdf", "A.pdf", "c.pdf"], "unique": ["only.pdf", "only.pdf"]}

    assert build_duplicate_groups(inputs) == [
        {"digest": digest, "keep": "A.pdf", "remove": ["b.pdf", "c.pdf"]}
    ]
    assert build_duplicate_groups(inputs, {digest: "b.pdf"}) == [
        {"digest": digest, "keep": "b.pdf", "remove": ["A.pdf", "c.pdf"]}
    ]
    assert build_duplicate_groups(inputs, {digest: "missing.pdf"}) == build_duplicate_groups(inputs)
    assert inputs[digest] == ["b.pdf", "c.pdf", "A.pdf", "c.pdf"]
    assert len(list(tmp_path.iterdir())) == 3
    assert build_duplicate_groups({digest: ["a.pdf", "A.pdf"]})[0]["keep"] == "A.pdf"


def test_cleanup_leaves_one_copy_and_preserves_unlisted_files(tmp_path):
    names = ["a.pdf", "b.pdf", "c.PDF"]
    digest = make_copies(tmp_path, names)
    (tmp_path / "different.pdf").write_bytes(b"another invoice")
    (tmp_path / "unlisted.pdf").write_bytes(b"same invoice")
    plan = build_duplicate_groups({digest: names}, {digest: "b.pdf"})

    result = cleanup_duplicate_files(tmp_path, plan)

    assert result == {"deleted": ["a.pdf", "c.PDF"], "errors": []}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["b.pdf", "different.pdf", "unlisted.pdf"]
    assert (tmp_path / "b.pdf").read_bytes() == b"same invoice"


def test_arrival_order_retains_oldest_copy_unless_existing_database_name_is_present():
    files = {"digest": ["a-new.pdf", "z-old.pdf", "m-middle.pdf"]}
    arrival_times = {"a-new.pdf": 30, "z-old.pdf": 10, "m-middle.pdf": 20}
    assert build_duplicate_groups(files, arrival_times=arrival_times) == [
        {"digest": "digest", "keep": "z-old.pdf", "remove": ["m-middle.pdf", "a-new.pdf"]}
    ]
    assert build_duplicate_groups(files, {"digest": "m-middle.pdf"}, arrival_times=arrival_times) == [
        {"digest": "digest", "keep": "m-middle.pdf", "remove": ["z-old.pdf", "a-new.pdf"]}
    ]


def test_changed_target_is_not_deleted(tmp_path):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf"])
    plan = build_duplicate_groups({digest: ["a.pdf", "b.pdf"]})
    (tmp_path / "b.pdf").write_bytes(b"new invoice")

    result = cleanup_duplicate_files(tmp_path, plan)

    assert not result["deleted"]
    assert "哈希不一致" in result["errors"][0]
    assert (tmp_path / "b.pdf").read_bytes() == b"new invoice"


@pytest.mark.parametrize("keeper_change", ["missing", "changed"])
def test_missing_or_changed_keeper_preserves_all_targets(tmp_path, keeper_change):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf", "c.pdf"])
    plan = build_duplicate_groups({digest: ["a.pdf", "b.pdf", "c.pdf"]})
    if keeper_change == "missing":
        (tmp_path / "a.pdf").unlink()
    else:
        (tmp_path / "a.pdf").write_bytes(b"changed keeper")

    result = cleanup_duplicate_files(tmp_path, plan)

    assert not result["deleted"]
    assert "保留文件" in result["errors"][0]
    assert (tmp_path / "b.pdf").exists()
    assert (tmp_path / "c.pdf").exists()


def test_missing_target_reports_error_and_continues_with_other_copy(tmp_path):
    digest = make_copies(tmp_path, ["a.pdf", "c.pdf"])
    plan = build_duplicate_groups({digest: ["a.pdf", "b.pdf", "c.pdf"]})

    result = cleanup_duplicate_files(tmp_path, plan)

    assert result["deleted"] == ["c.pdf"]
    assert len(result["errors"]) == 1
    assert (tmp_path / "a.pdf").exists()


@pytest.mark.parametrize("unsafe_name", ["../outside.pdf", "sub/copy.pdf", "sub\\copy.pdf", "/copy.pdf", "C:copy.pdf", "copy.txt", "copy.pdf "])
def test_unsafe_names_are_rejected(tmp_path, unsafe_name):
    digest = make_copies(tmp_path, ["a.pdf"])
    result = cleanup_duplicate_files(tmp_path, [{"digest": digest, "keep": "a.pdf", "remove": [unsafe_name]}])
    assert not result["deleted"]
    assert "文件名不安全" in result["errors"][0]
    assert (tmp_path / "a.pdf").exists()


@pytest.mark.parametrize("link_role", ["keeper", "target"])
def test_symlinks_are_not_followed_or_deleted(tmp_path, link_role):
    digest = make_copies(tmp_path, ["actual.pdf"])
    link = tmp_path / "link.pdf"
    try:
        link.symlink_to(tmp_path / "actual.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    keep, remove = ("link.pdf", "actual.pdf") if link_role == "keeper" else ("actual.pdf", "link.pdf")

    result = cleanup_duplicate_files(tmp_path, [{"digest": digest, "keep": keep, "remove": [remove]}])

    assert not result["deleted"]
    assert "符号链接" in result["errors"][0]
    assert link.is_symlink()
    assert (tmp_path / "actual.pdf").exists()


def test_keep_name_in_removal_plan_is_never_deleted(tmp_path):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf"])
    result = cleanup_duplicate_files(tmp_path, [{"digest": digest, "keep": "a.pdf", "remove": ["a.pdf", "b.pdf"]}])
    assert result["deleted"] == ["b.pdf"]
    assert "保留文件" in result["errors"][0]
    assert (tmp_path / "a.pdf").exists()


def test_deletion_error_is_reported_without_losing_keeper(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf"])
    original_unlink = Path.unlink

    def deny_delete(path, *args, **kwargs):
        if path.name == "b.pdf":
            raise PermissionError("test: access denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_delete)
    result = cleanup_duplicate_files(tmp_path, build_duplicate_groups({digest: ["a.pdf", "b.pdf"]}))
    assert not result["deleted"]
    assert "未删除“b.pdf”" in result["errors"][0]
    assert (tmp_path / "a.pdf").exists()
    assert (tmp_path / "b.pdf").exists()


def test_target_changed_during_keeper_recheck_is_not_deleted(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf"])
    original_hash = duplicate_files._verified_hash
    calls = 0

    def hash_and_change_target(path, expected):
        nonlocal calls
        signature = original_hash(path, expected)
        if path.name == "a.pdf":
            calls += 1
            if calls == 2:
                (tmp_path / "b.pdf").write_bytes(b"replacement invoice")
        return signature

    monkeypatch.setattr(duplicate_files, "_verified_hash", hash_and_change_target)
    result = cleanup_duplicate_files(tmp_path, build_duplicate_groups({digest: ["a.pdf", "b.pdf"]}))
    assert not result["deleted"]
    assert "删除前已变化" in result["errors"][0]
    assert (tmp_path / "b.pdf").read_bytes() == b"replacement invoice"


def test_keeper_disappearing_after_first_delete_preserves_last_copy(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["a.pdf", "b.pdf", "c.pdf"])
    original_unlink = Path.unlink

    def unlink_and_remove_keeper(path, *args, **kwargs):
        result = original_unlink(path, *args, **kwargs)
        if path.name == "b.pdf":
            original_unlink(tmp_path / "a.pdf")
        return result

    monkeypatch.setattr(Path, "unlink", unlink_and_remove_keeper)
    result = cleanup_duplicate_files(tmp_path, build_duplicate_groups({digest: ["a.pdf", "b.pdf", "c.pdf"]}))
    assert result["deleted"] == ["b.pdf"]
    assert "停止清理" in result["errors"][0]
    assert (tmp_path / "c.pdf").exists()


def test_refused_cleanup_marks_copies_and_retains_every_byte(tmp_path):
    names = ["original.pdf", "copy-a.pdf", "copy-b.pdf"]
    digest = make_copies(tmp_path, names)
    groups = build_duplicate_groups({digest: names}, {digest: "original.pdf"})

    result = rename_duplicate_files(tmp_path, groups)

    assert DUPLICATE_PREFIX == "哈希副本-"
    assert result == {
        "renamed": [("copy-a.pdf", "哈希副本-copy-a.pdf"), ("copy-b.pdf", "哈希副本-copy-b.pdf")],
        "errors": [],
    }
    assert sorted(path.name for path in tmp_path.iterdir()) == ["original.pdf", "哈希副本-copy-a.pdf", "哈希副本-copy-b.pdf"]
    assert all(path.read_bytes() == b"same invoice" for path in tmp_path.iterdir())


def test_marked_copies_do_not_prompt_or_receive_a_second_prefix(tmp_path):
    names = ["original.pdf", "哈希副本-copy.pdf"]
    digest = make_copies(tmp_path, names)
    groups = build_duplicate_groups({digest: names}, {digest: "original.pdf"})
    assert pending_duplicate_groups(groups) == []
    assert rename_duplicate_files(tmp_path, groups) == {"renamed": [], "errors": []}
    groups[0]["remove"].append("new.pdf")
    assert pending_duplicate_groups(groups) == [{"digest": digest, "keep": "original.pdf", "remove": ["new.pdf"]}]
    assert groups[0]["remove"] == ["哈希副本-copy.pdf", "new.pdf"]
    assert (tmp_path / "哈希副本-copy.pdf").exists()


def test_rename_collision_preserves_existing_different_content(tmp_path):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])
    (tmp_path / "哈希副本-copy.pdf").write_bytes(b"different invoice")
    (tmp_path / "哈希副本-copy (2).pdf").write_bytes(b"another different invoice")

    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])

    assert result == {"renamed": [("copy.pdf", "哈希副本-copy (3).pdf")], "errors": []}
    assert (tmp_path / "哈希副本-copy.pdf").read_bytes() == b"different invoice"
    assert (tmp_path / "哈希副本-copy (2).pdf").read_bytes() == b"another different invoice"
    assert (tmp_path / "哈希副本-copy (3).pdf").read_bytes() == b"same invoice"


def test_rename_collision_with_broken_symlink_is_not_overwritten(tmp_path):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])
    link = tmp_path / "哈希副本-copy.pdf"
    try:
        link.symlink_to(tmp_path / "missing.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])
    assert result["renamed"] == [("copy.pdf", "哈希副本-copy (2).pdf")]
    assert link.is_symlink()
    assert (tmp_path / "哈希副本-copy (2).pdf").read_bytes() == b"same invoice"


@pytest.mark.parametrize("role", ["original.pdf", "copy.pdf"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_changed_or_missing_file_is_not_renamed(tmp_path, role, change):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])
    if change == "missing":
        (tmp_path / role).unlink()
    else:
        (tmp_path / role).write_bytes(b"changed invoice")

    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])

    assert not result["renamed"]
    assert result["errors"]
    assert not list(tmp_path.glob("哈希副本-*"))
    if role != "copy.pdf" or change != "missing":
        assert (tmp_path / "copy.pdf").exists()


@pytest.mark.parametrize("unsafe_name", ["../copy.pdf", "sub/copy.pdf", "sub\\copy.pdf", "/copy.pdf", "C:copy.pdf", "copy.txt"])
def test_rename_rejects_unsafe_paths(tmp_path, unsafe_name):
    digest = make_copies(tmp_path, ["original.pdf"])
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": [unsafe_name]}])
    assert not result["renamed"]
    assert "文件名不安全" in result["errors"][0]


@pytest.mark.parametrize("role", ["keeper", "copy"])
def test_rename_rejects_symlinks(tmp_path, role):
    digest = make_copies(tmp_path, ["actual.pdf"])
    link = tmp_path / "link.pdf"
    try:
        link.symlink_to(tmp_path / "actual.pdf")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    keep, source = ("link.pdf", "actual.pdf") if role == "keeper" else ("actual.pdf", "link.pdf")
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": keep, "remove": [source]}])
    assert not result["renamed"]
    assert "符号链接" in result["errors"][0]
    assert link.is_symlink()
    assert (tmp_path / "actual.pdf").read_bytes() == b"same invoice"


def test_rename_permission_error_preserves_original_names(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])

    def deny_rename(*args, **kwargs):
        raise PermissionError("test: access denied")

    monkeypatch.setattr(duplicate_files, "_rename_no_replace", deny_rename)
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])
    assert not result["renamed"]
    assert "未标记副本" in result["errors"][0]
    assert (tmp_path / "original.pdf").read_bytes() == b"same invoice"
    assert (tmp_path / "copy.pdf").read_bytes() == b"same invoice"


def test_rename_never_changes_a_keeper_even_in_an_invalid_plan(tmp_path):
    digest = make_copies(tmp_path, ["original.pdf"])
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["original.pdf"]}])
    assert not result["renamed"]
    assert "保留文件" in result["errors"][0]
    assert (tmp_path / "original.pdf").exists()


def test_rename_too_long_reports_error_without_losing_data(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])

    def filename_too_long(*args, **kwargs):
        raise OSError(36, "File name too long")

    monkeypatch.setattr(duplicate_files, "_rename_no_replace", filename_too_long)
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])
    assert not result["renamed"]
    assert result["errors"]
    assert (tmp_path / "copy.pdf").read_bytes() == b"same invoice"


@pytest.mark.skipif(duplicate_files.os.name == "nt", reason="POSIX hard-link rollback")
def test_rename_unlink_failure_rolls_back_new_name_and_retains_original(tmp_path, monkeypatch):
    digest = make_copies(tmp_path, ["original.pdf", "copy.pdf"])
    original_unlink = Path.unlink

    def deny_source_unlink(path, *args, **kwargs):
        if path.name == "copy.pdf":
            raise PermissionError("test: access denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_source_unlink)
    result = rename_duplicate_files(tmp_path, [{"digest": digest, "keep": "original.pdf", "remove": ["copy.pdf"]}])

    assert not result["renamed"]
    assert result["errors"]
    assert not (tmp_path / "哈希副本-copy.pdf").exists()
    assert (tmp_path / "copy.pdf").read_bytes() == b"same invoice"
    assert (tmp_path / "original.pdf").read_bytes() == b"same invoice"
