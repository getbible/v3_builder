"""Tests for generated publication filesystem and hard-size gates."""

import pytest

from publication_safety import PublicationSafetyError, validate_generated_output


def test_rejects_missing_or_symlinked_publication_root(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(PublicationSafetyError, match="root does not exist"):
        validate_generated_output(str(missing))

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PublicationSafetyError, match="not a real directory"):
        validate_generated_output(str(linked))


def test_rejects_file_at_hard_ceiling_with_exact_path_and_size(tmp_path):
    output = tmp_path / "kjv.json"
    with output.open("wb") as stream:
        stream.truncate(100)

    with pytest.raises(PublicationSafetyError) as exc_info:
        validate_generated_output(str(tmp_path), max_file_bytes=100)

    message = str(exc_info.value)
    assert str(output) in message
    assert "100 bytes" in message
    assert "hard ceiling" in message


def test_reports_every_oversized_file_in_deterministic_order(tmp_path):
    second = tmp_path / "z.json"
    first = tmp_path / "a.json"
    first.write_bytes(b"a" * 11)
    second.write_bytes(b"z" * 12)

    with pytest.raises(PublicationSafetyError) as exc_info:
        validate_generated_output(str(tmp_path), max_file_bytes=10)

    message = str(exc_info.value)
    assert message.index(str(first)) < message.index(str(second))
    assert "11 bytes" in message
    assert "12 bytes" in message


def test_allows_content_size_changes_below_hard_ceiling(tmp_path):
    output = tmp_path / "kjv.json"
    output.write_bytes(b"x" * 500)

    files = validate_generated_output(str(tmp_path), max_file_bytes=501)

    assert [(item.relative_path, item.size) for item in files] == [
        ("kjv.json", 500)
    ]


def test_rejects_incomplete_atomic_json_writes(tmp_path):
    temporary = tmp_path / '.kjv.json.interrupted.tmp'
    temporary.write_text('{"books":', encoding='utf-8')

    with pytest.raises(PublicationSafetyError) as exc_info:
        validate_generated_output(str(tmp_path))

    message = str(exc_info.value)
    assert str(temporary) in message
    assert 'incomplete atomic JSON writes' in message


def test_ignores_git_metadata_and_preserved_files(tmp_path):
    git_blob = tmp_path / ".git" / "objects" / "large"
    git_blob.parent.mkdir(parents=True)
    git_blob.write_bytes(b"x" * 100)
    readme = tmp_path / "README.md"
    readme.write_bytes(b"x" * 100)
    target = tmp_path / "small.json"
    target.write_text("{}")
    files = validate_generated_output(
        str(tmp_path), max_file_bytes=10, preserved_names={"README.md", ".git"}
    )

    assert [item.relative_path for item in files] == ["small.json"]


@pytest.mark.parametrize("link_to_directory", [False, True])
def test_rejects_generated_symlinks(tmp_path, link_to_directory):
    if link_to_directory:
        target = tmp_path / "target"
        target.mkdir()
        symlink = tmp_path / "linked-directory"
    else:
        target = tmp_path / "target.json"
        target.write_text("{}")
        symlink = tmp_path / "linked.json"
    symlink.symlink_to(target, target_is_directory=link_to_directory)

    with pytest.raises(PublicationSafetyError) as exc_info:
        validate_generated_output(str(tmp_path))

    assert str(symlink) in str(exc_info.value)
    assert "symlink or special" in str(exc_info.value)
