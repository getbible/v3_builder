"""Integration tests for real SWORD module conversion.

Validates that SwordModuleConverter produces correct JSON at all three
levels (version, book, chapter) for each module in the test map, with
random sampling of books/chapters/verses for broad coverage.
"""

import json
import os
import re

import pytest

pytestmark = pytest.mark.integration

# ── Expected fields (matching the contract tests in test_api_format.py) ──────

REQUIRED_VERSION_FIELDS = {
    'translation', 'abbreviation', 'description', 'lang', 'language',
    'direction', 'encoding', 'books',
    'distribution_lcsh', 'distribution_version', 'distribution_version_date',
    'distribution_abbreviation', 'distribution_about', 'distribution_license',
    'distribution_sourcetype', 'distribution_source', 'distribution_versification',
    'distribution_history',
}

REQUIRED_BOOK_FILE_FIELDS = {
    'translation', 'abbreviation', 'lang', 'language', 'direction',
    'encoding', 'nr', 'name', 'chapters',
}

REQUIRED_CHAPTER_FILE_FIELDS = {
    'translation', 'abbreviation', 'lang', 'language', 'direction',
    'encoding', 'book_nr', 'book_name', 'chapter', 'name', 'verses',
}

REQUIRED_VERSE_FIELDS = {'chapter', 'verse', 'name', 'text'}


# ── Version-level tests (per module) ────────────────────────────────────────


class TestVersionLevel:
    """Validate version-level JSON for each converted module."""

    def test_version_file_exists(self, per_module):
        assert os.path.isfile(per_module['version_path'])

    def test_version_has_required_fields(self, per_module):
        data = per_module['version_data']
        missing = REQUIRED_VERSION_FIELDS - set(data.keys())
        assert not missing, f"{per_module['abbreviation']} missing: {missing}"

    def test_version_has_books(self, per_module):
        books = per_module['version_data']['books']
        assert isinstance(books, list)
        assert len(books) > 0, f"{per_module['abbreviation']} has no books"

    def test_abbreviation_matches(self, per_module):
        assert per_module['version_data']['abbreviation'] == per_module['abbreviation']

    def test_direction_is_valid(self, per_module):
        direction = per_module['version_data']['direction']
        assert direction in ('LTR', 'RTL'), f"Invalid direction: {direction}"

    def test_distribution_history_is_dict(self, per_module):
        assert isinstance(per_module['version_data']['distribution_history'], dict)

    def test_all_book_nrs_are_int(self, per_module):
        for book in per_module['version_data']['books']:
            assert isinstance(book['nr'], int), f"Book nr not int: {book.get('nr')}"


# ── Book-level tests (random book per module) ───────────────────────────────


class TestBookLevel:
    """Validate book-level JSON files with random sampling."""

    def test_book_file_exists(self, per_module, random_book):
        book_path = os.path.join(
            per_module['output_dir'],
            per_module['abbreviation'],
            f"{random_book['nr']}.json",
        )
        assert os.path.isfile(book_path), f"Missing book file: {book_path}"

    def test_book_file_has_required_fields(self, per_module, random_book):
        book_path = os.path.join(
            per_module['output_dir'],
            per_module['abbreviation'],
            f"{random_book['nr']}.json",
        )
        with open(book_path, 'r', encoding='utf-8') as f:
            book_data = json.load(f)
        missing = REQUIRED_BOOK_FILE_FIELDS - set(book_data.keys())
        assert not missing, f"Book file missing: {missing}"

    def test_book_has_chapters(self, random_book):
        assert isinstance(random_book['chapters'], list)
        assert len(random_book['chapters']) > 0

    def test_book_has_name(self, random_book):
        assert isinstance(random_book['name'], str)
        assert len(random_book['name']) > 0


# ── Chapter-level tests (random chapter per module) ─────────────────────────


class TestChapterLevel:
    """Validate chapter-level JSON files with random sampling."""

    def test_chapter_file_exists(self, per_module, random_book, random_chapter):
        ch_path = os.path.join(
            per_module['output_dir'],
            per_module['abbreviation'],
            str(random_book['nr']),
            f"{random_chapter['chapter']}.json",
        )
        assert os.path.isfile(ch_path), f"Missing chapter file: {ch_path}"

    def test_chapter_file_has_required_fields(self, per_module, random_book, random_chapter):
        ch_path = os.path.join(
            per_module['output_dir'],
            per_module['abbreviation'],
            str(random_book['nr']),
            f"{random_chapter['chapter']}.json",
        )
        with open(ch_path, 'r', encoding='utf-8') as f:
            ch_data = json.load(f)
        missing = REQUIRED_CHAPTER_FILE_FIELDS - set(ch_data.keys())
        assert not missing, f"Chapter file missing: {missing}"

    def test_chapter_has_verses(self, random_chapter):
        assert isinstance(random_chapter['verses'], list)
        assert len(random_chapter['verses']) > 0

    def test_chapter_number_is_int(self, random_chapter):
        assert isinstance(random_chapter['chapter'], int)
        assert random_chapter['chapter'] > 0


# ── Verse-level tests (random verse per module) ─────────────────────────────


class TestVerseLevel:
    """Validate verse data with random sampling."""

    def test_verse_has_required_fields(self, random_verse):
        missing = REQUIRED_VERSE_FIELDS - set(random_verse.keys())
        assert not missing, f"Verse missing fields: {missing}"

    def test_verse_text_is_nonempty(self, random_verse):
        assert isinstance(random_verse['text'], str)
        assert len(random_verse['text'].strip()) > 0

    def test_verse_number_is_int(self, random_verse):
        assert isinstance(random_verse['verse'], int)
        assert random_verse['verse'] > 0

    def test_verse_chapter_is_int(self, random_verse):
        assert isinstance(random_verse['chapter'], int)
        assert random_verse['chapter'] > 0

    def test_verse_name_format(self, random_verse):
        """Verse name must follow 'BookName Chapter:Verse' pattern."""
        name = random_verse['name']
        assert ':' in name, f"Verse name missing colon: {name}"
        assert re.match(r'.+ \d+:\d+$', name), f"Invalid verse name format: {name}"
