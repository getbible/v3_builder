"""Tests validating the v3 API JSON output format.

These tests ensure the generated JSON files conform to the expected
getBible API v3 schema at all three levels (version, book, chapter)
and that the token+span model is correctly structured.

This is the contract test suite: if any of these fail, the API output
has changed in a way that would break consumers.
"""

import json
import os
import sys
from unittest import mock

import pytest

# Mock pysword before importing converter — link submodule to parent
# so the import machinery resolves the same object regardless of path.
_pysword_mock = sys.modules.setdefault('pysword', mock.MagicMock())
sys.modules.setdefault('pysword.modules', _pysword_mock.modules)

from converter import ConversionConfig, SwordModuleConverter, _write_json


# ── Helpers ──────────────────────────────────────────────────────────────────

class FakeBook:
    def __init__(self, name, chapters):
        self.name = name
        self.num_chapters = len(chapters)
        self._chapters = chapters

    def get_indicies(self, chapter):
        return self._chapters.get(chapter, [])


def _make_converter(tmp_path, conf_dir, bible_conf, output_path):
    """Create a SwordModuleConverter with test config."""
    config = ConversionConfig.from_files(str(conf_dir), bible_conf)
    return SwordModuleConverter(config, str(output_path), conf_dir=str(conf_dir))


def _fake_sword_module(books_by_testament, verse_texts, raw_verses=None,
                       module_config=None):
    """Create a mock SwordModules that returns test data."""
    default_config = {
        'description': 'Test Bible',
        'lang': 'en',
        'encoding': 'UTF-8',
        'sourcetype': 'OSIS' if raw_verses else 'ThML',
        'version': '1.0',
    }
    if module_config:
        default_config.update(module_config)

    class FakeStructure:
        def __init__(self):
            self._books = books_by_testament

    class FakeBibleMod:
        def get_structure(self):
            return FakeStructure()

        def get(self, books=None, chapters=None, verses=None, clean=True):
            key = (books[0], chapters[0], verses[0])
            if not clean and raw_verses and key in raw_verses:
                return raw_verses[key]
            return verse_texts.get(key, '')

    mock_modules = mock.MagicMock()
    mock_modules.parse_modules.return_value = {'TestBible': default_config}
    mock_modules.get_bible_from_module.return_value = FakeBibleMod()
    return mock_modules


@pytest.fixture
def conf_dir(tmp_path):
    """Create minimal config directory."""
    conf = tmp_path / 'conf'
    conf.mkdir()
    files = {
        'v1Translations.json': {'kjv': 'King James Version'},
        'bookNumbers.json': {'Genesis': 1, 'Exodus': 2, 'Matthew': 40},
        'bookNames.json': {'Genesis': 'Genesis', 'Exodus': 'Exodus'},
        'langCorrection.json': {'en': 'en'},
        'languageNames.json': {'en': 'English'},
        'textDirection.json': {'en': 'LTR'},
    }
    for name, data in files.items():
        (conf / name).write_text(json.dumps(data))
    bible_conf = conf / 'modules.json'
    bible_conf.write_text(json.dumps({'TestBible': 'testbible'}))
    return conf


@pytest.fixture
def bible_conf(conf_dir):
    return str(conf_dir / 'modules.json')


# ── Required Fields at Each Level ────────────────────────────────────────────

REQUIRED_VERSION_FIELDS = {
    'translation', 'abbreviation', 'description', 'lang', 'language',
    'direction', 'encoding', 'books',
    'distribution_lcsh', 'distribution_version', 'distribution_version_date',
    'distribution_abbreviation', 'distribution_about', 'distribution_license',
    'distribution_sourcetype', 'distribution_source', 'distribution_versification',
    'distribution_history',
}

REQUIRED_BOOK_ENTRY_FIELDS = {'nr', 'name', 'chapters'}

REQUIRED_CHAPTER_ENTRY_FIELDS = {'chapter', 'name', 'verses'}

REQUIRED_VERSE_FIELDS = {'chapter', 'verse', 'name', 'text'}

REQUIRED_BOOK_FILE_FIELDS = {
    'translation', 'abbreviation', 'lang', 'language', 'direction',
    'encoding', 'nr', 'name', 'chapters',
}

REQUIRED_CHAPTER_FILE_FIELDS = {
    'translation', 'abbreviation', 'lang', 'language', 'direction',
    'encoding', 'book_nr', 'book_name', 'chapter', 'name', 'verses',
}

REQUIRED_TOKEN_FIELDS = {'token', 'word_start', 'word_end'}

REQUIRED_SPAN_FIELDS = {
    'tag', 'span', 'word_start', 'word_end', 'token_start', 'token_end',
}


class TestVersionLevelFormat:
    """Validate the version-level JSON schema (e.g. kjv.json)."""

    @pytest.fixture
    def version_data(self, tmp_path, conf_dir, bible_conf):
        output = tmp_path / 'output'
        output.mkdir()
        genesis = FakeBook('Genesis', {1: [1, 2], 2: [1]})
        verses = {
            ('Genesis', 1, 1): 'In the beginning God created the heaven and the earth.',
            ('Genesis', 1, 2): 'And the earth was without form.',
            ('Genesis', 2, 1): 'Thus the heavens and the earth were finished.',
        }
        fake_mod = _fake_sword_module({'ot': [genesis]}, verses)
        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            config = ConversionConfig.from_files(str(conf_dir), bible_conf)
            converter = SwordModuleConverter(config, str(output), conf_dir=str(conf_dir))
            result = converter.convert(str(tmp_path / 'TestBible.zip'))
        with open(result) as f:
            return json.load(f)

    def test_has_all_required_fields(self, version_data):
        missing = REQUIRED_VERSION_FIELDS - set(version_data.keys())
        assert not missing, f'Missing version fields: {missing}'

    def test_books_is_list(self, version_data):
        assert isinstance(version_data['books'], list)
        assert len(version_data['books']) > 0

    def test_book_entries_have_required_fields(self, version_data):
        for book in version_data['books']:
            missing = REQUIRED_BOOK_ENTRY_FIELDS - set(book.keys())
            assert not missing, f'Book entry missing fields: {missing}'

    def test_chapter_entries_have_required_fields(self, version_data):
        for book in version_data['books']:
            for chapter in book['chapters']:
                missing = REQUIRED_CHAPTER_ENTRY_FIELDS - set(chapter.keys())
                assert not missing, f'Chapter entry missing fields: {missing}'

    def test_verse_entries_have_required_fields(self, version_data):
        for book in version_data['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    missing = REQUIRED_VERSE_FIELDS - set(verse.keys())
                    assert not missing, f'Verse missing fields: {missing}'

    def test_verse_name_format(self, version_data):
        """Verse name must follow 'BookName Chapter:Verse' pattern."""
        for book in version_data['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    expected = f"{book['name']} {chapter['chapter']}:{verse['verse']}"
                    assert verse['name'] == expected

    def test_book_nr_is_int(self, version_data):
        for book in version_data['books']:
            assert isinstance(book['nr'], int)

    def test_chapter_number_is_int(self, version_data):
        for book in version_data['books']:
            for chapter in book['chapters']:
                assert isinstance(chapter['chapter'], int)

    def test_verse_number_is_int(self, version_data):
        for book in version_data['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    assert isinstance(verse['verse'], int)

    def test_direction_is_valid(self, version_data):
        assert version_data['direction'] in ('LTR', 'RTL')

    def test_distribution_history_is_dict(self, version_data):
        assert isinstance(version_data['distribution_history'], dict)

    def test_text_is_nonempty_string(self, version_data):
        for book in version_data['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    assert isinstance(verse['text'], str)
                    assert len(verse['text']) > 0


class TestBookFileFormat:
    """Validate the book-level JSON schema (e.g. kjv/1.json)."""

    @pytest.fixture
    def book_data(self, tmp_path, conf_dir, bible_conf):
        output = tmp_path / 'output'
        output.mkdir()
        genesis = FakeBook('Genesis', {1: [1]})
        verses = {('Genesis', 1, 1): 'In the beginning.'}
        fake_mod = _fake_sword_module({'ot': [genesis]}, verses)
        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            config = ConversionConfig.from_files(str(conf_dir), bible_conf)
            converter = SwordModuleConverter(config, str(output), conf_dir=str(conf_dir))
            converter.convert(str(tmp_path / 'TestBible.zip'))
        with open(output / 'testbible' / '1.json') as f:
            return json.load(f)

    def test_has_all_required_fields(self, book_data):
        missing = REQUIRED_BOOK_FILE_FIELDS - set(book_data.keys())
        assert not missing, f'Book file missing fields: {missing}'

    def test_chapters_is_list(self, book_data):
        assert isinstance(book_data['chapters'], list)

    def test_has_translation_metadata(self, book_data):
        assert book_data['lang'] == 'en'
        assert book_data['language'] == 'English'
        assert book_data['direction'] == 'LTR'


class TestChapterFileFormat:
    """Validate the chapter-level JSON schema (e.g. kjv/1/1.json)."""

    @pytest.fixture
    def chapter_data(self, tmp_path, conf_dir, bible_conf):
        output = tmp_path / 'output'
        output.mkdir()
        genesis = FakeBook('Genesis', {1: [1, 2]})
        verses = {
            ('Genesis', 1, 1): 'In the beginning.',
            ('Genesis', 1, 2): 'And the earth was void.',
        }
        fake_mod = _fake_sword_module({'ot': [genesis]}, verses)
        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            config = ConversionConfig.from_files(str(conf_dir), bible_conf)
            converter = SwordModuleConverter(config, str(output), conf_dir=str(conf_dir))
            converter.convert(str(tmp_path / 'TestBible.zip'))
        with open(output / 'testbible' / '1' / '1.json') as f:
            return json.load(f)

    def test_has_all_required_fields(self, chapter_data):
        missing = REQUIRED_CHAPTER_FILE_FIELDS - set(chapter_data.keys())
        assert not missing, f'Chapter file missing fields: {missing}'

    def test_verses_is_list(self, chapter_data):
        assert isinstance(chapter_data['verses'], list)
        assert len(chapter_data['verses']) > 0

    def test_has_book_metadata(self, chapter_data):
        assert chapter_data['book_nr'] == 1
        assert chapter_data['book_name'] == 'Genesis'
        assert chapter_data['chapter'] == 1
        assert chapter_data['name'] == 'Genesis 1'


class TestTokenSpanFormat:
    """Validate the v3 token+span model output format."""

    @pytest.fixture
    def verse_with_tokens(self, tmp_path, conf_dir, bible_conf):
        output = tmp_path / 'output'
        output.mkdir()
        genesis = FakeBook('Genesis', {1: [1]})
        verse_texts = {
            ('Genesis', 1, 1): 'In the beginning God created.',
        }
        raw_verses = {
            ('Genesis', 1, 1): (
                '<verse osisID="Gen.1.1">'
                '<q who="God" marker="">'
                '<w lemma="strong:H07225" morph="oshm:HNcfsa">In the beginning</w> '
                '<w lemma="strong:H0430" morph="oshm:HNcmpa">God</w> '
                '</q>'
                '<w lemma="strong:H01254" morph="oshm:HVqp3ms">created</w>'
                '</verse>'
            ),
        }
        fake_mod = _fake_sword_module(
            {'ot': [genesis]}, verse_texts, raw_verses=raw_verses
        )
        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            config = ConversionConfig.from_files(str(conf_dir), bible_conf)
            converter = SwordModuleConverter(config, str(output), conf_dir=str(conf_dir))
            result = converter.convert(str(tmp_path / 'TestBible.zip'))
        with open(result) as f:
            data = json.load(f)
        return data['books'][0]['chapters'][0]['verses'][0]

    def test_tokens_present(self, verse_with_tokens):
        assert 'tokens' in verse_with_tokens
        assert isinstance(verse_with_tokens['tokens'], list)
        assert len(verse_with_tokens['tokens']) > 0

    def test_spans_present(self, verse_with_tokens):
        assert 'spans' in verse_with_tokens
        assert isinstance(verse_with_tokens['spans'], list)

    def test_token_has_required_fields(self, verse_with_tokens):
        for token in verse_with_tokens['tokens']:
            missing = REQUIRED_TOKEN_FIELDS - set(token.keys())
            assert not missing, f'Token missing fields: {missing}'

    def test_token_word_positions_are_ordered(self, verse_with_tokens):
        """Each token's word_start/word_end must be 1-based, ordered, and
        non-decreasing across the tokens list."""
        prev_end = 0
        for token in verse_with_tokens['tokens']:
            assert isinstance(token['word_start'], int)
            assert isinstance(token['word_end'], int)
            assert token['word_start'] >= 1
            assert token['word_start'] <= token['word_end']
            assert token['word_start'] >= prev_end
            prev_end = token['word_end']

    def test_token_text_is_nonempty(self, verse_with_tokens):
        for token in verse_with_tokens['tokens']:
            assert isinstance(token['token'], str)
            assert len(token['token']) > 0

    def test_token_lemma_format(self, verse_with_tokens):
        """Lemma values are scheme-keyed dicts of code arrays."""
        for token in verse_with_tokens['tokens']:
            if 'lemma' in token:
                assert isinstance(token['lemma'], dict)
                assert len(token['lemma']) > 0
                for scheme, codes in token['lemma'].items():
                    assert isinstance(scheme, str) and scheme
                    assert isinstance(codes, list) and codes
                    assert all(isinstance(c, str) and c for c in codes)

    def test_span_has_required_fields(self, verse_with_tokens):
        for span in verse_with_tokens['spans']:
            missing = REQUIRED_SPAN_FIELDS - set(span.keys())
            assert not missing, f'Span missing fields: {missing}'

    def test_span_indices_are_valid(self, verse_with_tokens):
        num_tokens = len(verse_with_tokens['tokens'])
        for span in verse_with_tokens['spans']:
            assert isinstance(span['token_start'], int)
            assert isinstance(span['token_end'], int)
            assert isinstance(span['word_start'], int)
            assert isinstance(span['word_end'], int)
            assert 0 <= span['token_start'] <= span['token_end']
            assert span['token_end'] < num_tokens
            assert span['word_start'] >= 1
            assert span['word_start'] <= span['word_end']
            assert isinstance(span['span'], str)
            assert len(span['span']) > 0

    def test_span_tag_is_string(self, verse_with_tokens):
        for span in verse_with_tokens['spans']:
            assert isinstance(span['tag'], str)
            assert len(span['tag']) > 0

    def test_span_attrs_is_dict_when_present(self, verse_with_tokens):
        for span in verse_with_tokens['spans']:
            if 'attrs' in span:
                assert isinstance(span['attrs'], dict)

    def test_no_tokens_for_non_osis(self, tmp_path, conf_dir, bible_conf):
        """Modules without OSIS word markup must NOT have tokens/spans."""
        output = tmp_path / 'output'
        output.mkdir()
        genesis = FakeBook('Genesis', {1: [1]})
        verses = {('Genesis', 1, 1): 'In the beginning.'}
        fake_mod = _fake_sword_module(
            {'ot': [genesis]}, verses, module_config={'sourcetype': 'ThML'}
        )
        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            config = ConversionConfig.from_files(str(conf_dir), bible_conf)
            converter = SwordModuleConverter(config, str(output), conf_dir=str(conf_dir))
            result = converter.convert(str(tmp_path / 'TestBible.zip'))
        with open(result) as f:
            data = json.load(f)
        verse = data['books'][0]['chapters'][0]['verses'][0]
        assert 'tokens' not in verse
        assert 'spans' not in verse


class TestHashOutputFormat:
    """Validate the hash file output format."""

    @pytest.fixture
    def hashed_dir(self, tmp_path):
        from hasher import ContentHasher
        # Create minimal scripture structure
        kjv = {
            'translation': 'King James Version',
            'abbreviation': 'kjv',
            'lang': 'en',
            'language': 'English',
            'direction': 'LTR',
            'encoding': 'UTF-8',
            'books': [{'nr': 1, 'name': 'Genesis', 'chapters': [
                {'chapter': 1, 'name': 'Genesis 1', 'verses': [
                    {'chapter': 1, 'verse': 1, 'name': 'Genesis 1:1',
                     'text': 'In the beginning.'}
                ]}
            ]}],
            'distribution_license': 'Public Domain',
        }
        kjv_book = {
            'translation': 'King James Version', 'abbreviation': 'kjv',
            'lang': 'en', 'language': 'English', 'direction': 'LTR',
            'encoding': 'UTF-8', 'nr': 1, 'name': 'Genesis',
            'chapters': kjv['books'][0]['chapters'],
        }
        kjv_chapter = {
            'translation': 'King James Version', 'abbreviation': 'kjv',
            'lang': 'en', 'language': 'English', 'direction': 'LTR',
            'encoding': 'UTF-8', 'book_nr': 1, 'book_name': 'Genesis',
            'chapter': 1, 'name': 'Genesis 1',
            'verses': kjv['books'][0]['chapters'][0]['verses'],
        }
        def _write(subpath, data):
            full = tmp_path / subpath
            full.parent.mkdir(parents=True, exist_ok=True)
            with open(full, 'w') as f:
                json.dump(data, f, indent=4)

        _write('kjv.json', kjv)
        _write('kjv/1.json', kjv_book)
        _write('kjv/1/1.json', kjv_chapter)

        hasher = ContentHasher(str(tmp_path))
        hasher.hash_all()
        return tmp_path

    def test_translations_json_format(self, hashed_dir):
        data = json.loads((hashed_dir / 'translations.json').read_text())
        assert isinstance(data, dict)
        for abbr, meta in data.items():
            assert 'url' in meta
            assert 'sha' in meta
            assert 'translation' in meta
            assert 'language' in meta
            assert 'books' not in meta  # Books array must be excluded

    def test_books_json_format(self, hashed_dir):
        data = json.loads((hashed_dir / 'kjv' / 'books.json').read_text())
        assert isinstance(data, dict)
        for nr, meta in data.items():
            assert 'url' in meta
            assert 'sha' in meta
            assert 'name' in meta
            assert 'chapters' not in meta  # Chapters must be excluded

    def test_chapters_json_format(self, hashed_dir):
        data = json.loads((hashed_dir / 'kjv' / '1' / 'chapters.json').read_text())
        assert isinstance(data, dict)
        for ch, meta in data.items():
            assert 'url' in meta
            assert 'sha' in meta
            assert 'name' in meta
            assert 'verses' not in meta  # Verses must be excluded

    def test_sha_files_contain_valid_hashes(self, hashed_dir):
        sha_content = (hashed_dir / 'kjv.sha').read_text().strip()
        assert len(sha_content) == 40  # SHA1 hex length
        assert all(c in '0123456789abcdef' for c in sha_content)

    def test_checksum_json_format(self, hashed_dir):
        data = json.loads((hashed_dir / 'checksum.json').read_text())
        assert isinstance(data, dict)
        for key, sha in data.items():
            assert len(sha) == 40
