"""Integration tests for hashing real converted output.

Validates that ContentHasher produces correct hash files (.sha,
checksum.json, translations.json, books.json, chapters.json)
from real converted SWORD module data.
"""

import json
import os

import pytest

pytestmark = pytest.mark.integration


class TestHashVersions:
    """Validate version-level hashing on real data."""

    def test_version_hashes_returned(self, hashed_output):
        hashes = hashed_output['version_hashes']
        assert isinstance(hashes, dict)
        assert len(hashes) > 0

    def test_sha_files_exist(self, hashed_output, converted_modules, conversion_output_dir):
        for abbr in hashed_output['version_hashes']:
            sha_path = os.path.join(conversion_output_dir, f'{abbr}.sha')
            assert os.path.isfile(sha_path), f"Missing {sha_path}"

    def test_sha_file_matches_hash(self, hashed_output, conversion_output_dir):
        for abbr, expected_hash in hashed_output['version_hashes'].items():
            sha_path = os.path.join(conversion_output_dir, f'{abbr}.sha')
            with open(sha_path, 'r') as f:
                file_hash = f.read().strip()
            assert file_hash == expected_hash

    def test_checksum_json_exists(self, conversion_output_dir):
        path = os.path.join(conversion_output_dir, 'checksum.json')
        assert os.path.isfile(path)
        with open(path, 'r') as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_translations_json_exists(self, conversion_output_dir):
        path = os.path.join(conversion_output_dir, 'translations.json')
        assert os.path.isfile(path)
        with open(path, 'r') as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_translations_json_has_metadata(self, conversion_output_dir):
        path = os.path.join(conversion_output_dir, 'translations.json')
        with open(path, 'r') as f:
            data = json.load(f)
        for abbr, meta in data.items():
            assert 'url' in meta, f"Translation {abbr} missing url"
            assert 'sha' in meta, f"Translation {abbr} missing sha"
            assert 'language' in meta, f"Translation {abbr} missing language"


class TestHashBooks:
    """Validate book-level hashing on real data."""

    def test_book_hashes_returned(self, hashed_output):
        hashes = hashed_output['book_hashes']
        assert isinstance(hashes, dict)
        assert len(hashes) > 0

    def test_books_json_exists_per_module(self, hashed_output, conversion_output_dir):
        for abbr in hashed_output['book_hashes']:
            path = os.path.join(conversion_output_dir, abbr, 'books.json')
            assert os.path.isfile(path), f"Missing {path}"

    def test_book_sha_files_exist(self, hashed_output, conversion_output_dir):
        for abbr, book_hashes in hashed_output['book_hashes'].items():
            for book_nr in book_hashes:
                sha_path = os.path.join(conversion_output_dir, abbr, f'{book_nr}.sha')
                assert os.path.isfile(sha_path), f"Missing {sha_path}"


class TestHashChapters:
    """Validate chapter-level hashing on real data."""

    def test_chapter_hashes_returned(self, hashed_output):
        hashes = hashed_output['chapter_hashes']
        assert isinstance(hashes, dict)
        assert len(hashes) > 0

    def test_chapters_json_exists(self, hashed_output, conversion_output_dir):
        for abbr, book_hashes in hashed_output['chapter_hashes'].items():
            for book_nr in book_hashes:
                path = os.path.join(
                    conversion_output_dir, abbr, book_nr, 'chapters.json',
                )
                assert os.path.isfile(path), f"Missing {path}"


class TestHashDeterminism:
    """Verify that hashing is deterministic."""

    def test_rehash_produces_same_results(self, hashed_output, conversion_output_dir):
        """Running hash_all() again should produce identical checksums."""
        from hasher import ContentHasher
        hasher = ContentHasher(conversion_output_dir)
        v2, b2, c2 = hasher.hash_all()
        assert v2 == hashed_output['version_hashes']
        assert b2 == hashed_output['book_hashes']
        assert c2 == hashed_output['chapter_hashes']
