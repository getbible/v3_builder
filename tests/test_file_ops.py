"""Tests for file_ops.py — clean and move operations."""

import json
import os
import shutil
import pytest

from file_ops import (
    _write_all,
    clean_empty_files,
    move_public_hash_files,
    write_json_minified,
)


def test_write_all_retries_short_writes(monkeypatch):
    written = bytearray()

    def short_write(_descriptor, value):
        accepted = value[:3]
        written.extend(accepted)
        return len(accepted)

    monkeypatch.setattr('file_ops.os.write', short_write)

    _write_all(99, b'complete output')

    assert bytes(written) == b'complete output'


def test_minified_json_write_is_atomic_on_serialization_failure(tmp_path):
    target = tmp_path / 'publication.json'
    original = '{"stable":true}\n'
    target.write_text(original, encoding='utf-8')

    with pytest.raises(TypeError):
        write_json_minified(
            {'serializable': 'written first', 'invalid': object()},
            target,
        )

    assert target.read_text(encoding='utf-8') == original
    assert list(tmp_path.glob('.publication.json.*.tmp')) == []


def test_minified_json_write_preserves_target_on_low_level_write_failure(
    tmp_path, monkeypatch
):
    target = tmp_path / 'publication.json'
    original = b'{"stable":true}\n'
    target.write_bytes(original)

    monkeypatch.setattr('file_ops.os.write', lambda _descriptor, _value: 0)

    with pytest.raises(OSError, match='short write'):
        write_json_minified({'replacement': True}, target)

    assert target.read_bytes() == original
    assert list(tmp_path.glob('.publication.json.*.tmp')) == []


def test_minified_json_write_replaces_target_with_complete_utf8(tmp_path):
    target = tmp_path / 'publication.json'
    target.write_text('{"old":true}\n', encoding='utf-8')
    expected = {'text': 'ἐν ἀρχῇ', 'nested': [1, 2, 3]}

    write_json_minified(expected, target)

    content = target.read_text(encoding='utf-8')
    assert json.loads(content) == expected
    assert content == '{"text":"ἐν ἀρχῇ","nested":[1,2,3]}\n'
    assert target.stat().st_mode & 0o777 == 0o644
    assert list(tmp_path.glob('.publication.json.*.tmp')) == []


def test_minified_json_write_removes_temp_when_replace_leaves_source(
    tmp_path, monkeypatch
):
    """Defend against filesystems that copy instead of consuming the temp."""

    target = tmp_path / 'publication.json'

    def copy_instead_of_replace(source, destination):
        shutil.copyfile(source, destination)

    monkeypatch.setattr('file_ops.os.replace', copy_instead_of_replace)

    write_json_minified({'complete': True}, target)

    assert json.loads(target.read_text(encoding='utf-8')) == {'complete': True}
    assert list(tmp_path.glob('.publication.json.*.tmp')) == []


@pytest.fixture
def scripture_with_empties(tmp_path):
    """Create a scripture dir with valid files and empty directories."""
    # Larger generated files
    big_data = {'text': 'x' * 600}
    (tmp_path / 'kjv').mkdir()
    (tmp_path / 'kjv' / '1').mkdir()
    with open(tmp_path / 'kjv.json', 'w') as f:
        json.dump(big_data, f)
    with open(tmp_path / 'kjv' / '1.json', 'w') as f:
        json.dump(big_data, f)
    with open(tmp_path / 'kjv' / '1' / '1.json', 'w') as f:
        json.dump(big_data, f)

    # Small JSON documents are valid output and must be preserved.
    (tmp_path / 'kjv' / '1' / '2.json').write_text('{}')

    # Directory containing a small document is not empty.
    (tmp_path / 'kjv' / '2').mkdir()
    (tmp_path / 'kjv' / '2' / '1.json').write_text('{}')

    # Completely empty directory
    (tmp_path / 'empty_dir').mkdir()

    return tmp_path


@pytest.fixture
def hashed_scripture(tmp_path):
    """Create a scripture dir with hash files ready for public copy."""
    s = tmp_path / 'scripture'
    s.mkdir()
    (s / 'kjv').mkdir()
    (s / 'kjv' / '1').mkdir()

    # Hash files at each level
    (s / 'kjv.sha').write_text('abc123\n')
    (s / 'checksum').write_text('#\tfilename\tsha\n')
    (s / 'checksum.json').write_text('{"kjv": "abc123"}')
    (s / 'translations').write_text('#\theader\n')
    (s / 'translations.json').write_text('{}')

    (s / 'kjv' / '1.sha').write_text('def456\n')
    (s / 'kjv' / 'checksum').write_text('#\tfilename\tsha\n')
    (s / 'kjv' / 'checksum.json').write_text('{}')
    (s / 'kjv' / 'books').write_text('#\theader\n')
    (s / 'kjv' / 'books.json').write_text('{}')

    (s / 'kjv' / '1' / '1.sha').write_text('ghi789\n')
    (s / 'kjv' / '1' / 'checksum').write_text('#\theader\n')
    (s / 'kjv' / '1' / 'checksum.json').write_text('{}')
    (s / 'kjv' / '1' / 'chapters').write_text('#\theader\n')
    (s / 'kjv' / '1' / 'chapters.json').write_text('{}')

    # Non-public files (should NOT be copied)
    (s / 'kjv.json').write_text('{"big": "data"}')
    (s / 'kjv' / '1.json').write_text('{"book": "data"}')
    (s / 'kjv' / '1' / '1.json').write_text('{"chapter": "data"}')

    return s


class TestCleanEmptyFiles:
    def test_preserves_small_json(self, scripture_with_empties):
        f_rm, _ = clean_empty_files(str(scripture_with_empties))
        assert f_rm == 0
        assert (scripture_with_empties / 'kjv' / '1' / '2.json').exists()
        assert (scripture_with_empties / 'kjv' / '2' / '1.json').exists()

    def test_removes_empty_dirs(self, scripture_with_empties):
        clean_empty_files(str(scripture_with_empties))
        assert not (scripture_with_empties / 'empty_dir').exists()

    def test_preserves_valid_files(self, scripture_with_empties):
        clean_empty_files(str(scripture_with_empties))
        assert (scripture_with_empties / 'kjv.json').exists()
        assert (scripture_with_empties / 'kjv' / '1.json').exists()
        assert (scripture_with_empties / 'kjv' / '1' / '1.json').exists()

    def test_nonexistent_folder_raises(self):
        with pytest.raises(FileNotFoundError):
            clean_empty_files('/nonexistent/path')

    def test_returns_counts(self, scripture_with_empties):
        f_rm, d_rm = clean_empty_files(str(scripture_with_empties))
        assert f_rm == 0
        assert d_rm == 1


class TestMovePublicHashFiles:
    def test_copies_sha_files(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert (dest / 'kjv.sha').exists()
        assert (dest / 'kjv' / '1.sha').exists()
        assert (dest / 'kjv' / '1' / '1.sha').exists()

    def test_copies_checksum_files(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert (dest / 'checksum').exists()
        assert (dest / 'checksum.json').exists()
        assert (dest / 'kjv' / 'checksum').exists()
        assert (dest / 'kjv' / 'checksum.json').exists()

    def test_copies_translations_files(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert (dest / 'translations').exists()
        assert (dest / 'translations.json').exists()

    def test_copies_books_files(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert (dest / 'kjv' / 'books').exists()
        assert (dest / 'kjv' / 'books.json').exists()

    def test_copies_chapters_files(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert (dest / 'kjv' / '1' / 'chapters').exists()
        assert (dest / 'kjv' / '1' / 'chapters.json').exists()

    def test_does_not_copy_scripture_json(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert not (dest / 'kjv.json').exists()
        assert not (dest / 'kjv' / '1.json').exists()
        assert not (dest / 'kjv' / '1' / '1.json').exists()

    def test_returns_count(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        count = move_public_hash_files(str(hashed_scripture), str(dest))
        assert count > 0

    def test_creates_dest_if_not_exists(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'new_public'
        move_public_hash_files(str(hashed_scripture), str(dest))
        assert dest.exists()

    def test_cleans_old_files_preserves_git(self, hashed_scripture, tmp_path):
        dest = tmp_path / 'public'
        dest.mkdir()
        # Create .git and LICENSE that should be preserved
        (dest / '.git').mkdir()
        (dest / '.git' / 'config').write_text('[core]')
        (dest / 'LICENSE').write_text('MIT')
        (dest / 'README.md').write_text('# API')
        # Old file that should be removed
        (dest / 'old_file.sha').write_text('old')

        move_public_hash_files(str(hashed_scripture), str(dest))

        assert (dest / '.git' / 'config').exists()
        assert (dest / 'LICENSE').exists()
        assert (dest / 'README.md').exists()
        # Old file cleaned, new files present
        assert (dest / 'kjv.sha').exists()

    def test_nonexistent_source_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            move_public_hash_files('/nonexistent', str(tmp_path))
