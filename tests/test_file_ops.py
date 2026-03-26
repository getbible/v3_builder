"""Tests for file_ops.py — clean and move operations."""

import json
import os
import pytest

from file_ops import clean_empty_files, move_public_hash_files


@pytest.fixture
def scripture_with_empties(tmp_path):
    """Create a scripture dir with some valid and some empty/small files."""
    # Valid files (>500 bytes)
    big_data = {'text': 'x' * 600}
    (tmp_path / 'kjv').mkdir()
    (tmp_path / 'kjv' / '1').mkdir()
    with open(tmp_path / 'kjv.json', 'w') as f:
        json.dump(big_data, f)
    with open(tmp_path / 'kjv' / '1.json', 'w') as f:
        json.dump(big_data, f)
    with open(tmp_path / 'kjv' / '1' / '1.json', 'w') as f:
        json.dump(big_data, f)

    # Small file (<500 bytes) — should be cleaned
    (tmp_path / 'kjv' / '1' / '2.json').write_text('{}')

    # Empty directory with only a small file
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
    def test_removes_small_json(self, scripture_with_empties):
        f_rm, _ = clean_empty_files(str(scripture_with_empties))
        assert f_rm >= 2  # 2.json and kjv/2/1.json
        assert not (scripture_with_empties / 'kjv' / '1' / '2.json').exists()

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
        assert f_rm >= 2
        assert d_rm >= 1  # at least empty_dir


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
