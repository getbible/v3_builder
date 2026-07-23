"""Tests for converter.py — SWORD module to JSON conversion."""

import json
import os
import sys
from unittest import mock

import pytest

# Mock pysword before importing converter since it may not be installed —
# link submodule to parent so the import machinery resolves the same object.
_pysword_mock = sys.modules.setdefault('pysword', mock.MagicMock())
sys.modules.setdefault('pysword.modules', _pysword_mock.modules)

from converter import (
    ConversionConfig,
    SwordModuleConverter,
    convert_module,
    load_config,
    parse_args,
    normalize_verse_text,
    _detect_word_data,
    _write_json,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def test_normalize_verse_text_removes_only_leading_line_endings():
    assert normalize_verse_text("\r\n\nVerse line one\nVerse line two\n") == (
        "Verse line one\nVerse line two\n"
    )


@pytest.fixture
def conf_dir(tmp_path):
    """Create a minimal configuration directory with all required files."""
    conf = tmp_path / 'conf'
    conf.mkdir()

    files = {
        'v1Translations.json': {'kjv': 'King James Version'},
        'bookNumbers.json': {'Genesis': 1, 'Exodus': 2, 'Matthew': 40},
        'bookNames.json': {'Genesis': 'Genesis', 'Exodus': 'Exodus', 'Matthew': 'Matthew'},
        'langCorrection.json': {'en': 'en'},
        'languageNames.json': {'en': 'English'},
        'textDirection.json': {'en': 'LTR'},
    }
    for name, data in files.items():
        (conf / name).write_text(json.dumps(data))

    # Bible modules map
    bible_conf = conf / 'modules.json'
    bible_conf.write_text(json.dumps({'KJV': 'kjv'}))

    return conf


@pytest.fixture
def bible_conf(conf_dir):
    """Return path to the Bible modules map."""
    return str(conf_dir / 'modules.json')


class FakeBook:
    """Fake pysword book object for testing."""

    def __init__(self, name, chapters):
        self.name = name
        self.num_chapters = len(chapters)
        self._chapters = chapters

    def get_indicies(self, chapter):
        return self._chapters.get(chapter, [])


class FakeStructure:
    """Fake pysword structure with _books dict."""

    def __init__(self, books_by_testament):
        self._books = books_by_testament


class FakeBibleModule:
    """Fake pysword Bible module for testing."""

    def __init__(self, books_by_testament, verses):
        self._structure = FakeStructure(books_by_testament)
        self._verses = verses

    def get_structure(self):
        return self._structure

    def get(self, books=None, chapters=None, verses=None, clean=True):
        key = (books[0], chapters[0], verses[0])
        text = self._verses.get(key, '')
        if not clean and key in self._raw_verses:
            return self._raw_verses[key]
        return text

    _raw_verses = {}


def _make_fake_module(books_by_testament, verses, raw_verses=None, config=None):
    """Create mock SwordModules that returns fake data."""
    bible_mod = FakeBibleModule(books_by_testament, verses)
    if raw_verses:
        bible_mod._raw_verses = raw_verses

    default_config = {
        'description': 'Test Bible',
        'lang': 'en',
        'encoding': 'UTF-8',
        'sourcetype': 'OSIS',
        'version': '1.0',
    }
    if config:
        default_config.update(config)

    mock_modules = mock.MagicMock()
    mock_modules.parse_modules.return_value = {'TestBible': default_config}
    mock_modules.get_bible_from_module.return_value = bible_mod
    return mock_modules


# ─── Tests: load_config ──────────────────────────────────────────────────────

class TestLoadConfig:
    def test_loads_all_config_files(self, conf_dir, bible_conf):
        config = load_config(str(conf_dir), bible_conf)
        assert 'translation_names' in config
        assert 'v1_translations' in config
        assert 'book_numbers' in config
        assert 'book_names' in config
        assert 'lang_correction' in config
        assert 'language_names' in config
        assert 'text_direction' in config

    def test_config_values_correct(self, conf_dir, bible_conf):
        config = load_config(str(conf_dir), bible_conf)
        assert config['translation_names'] == {'KJV': 'kjv'}
        assert config['book_numbers']['Genesis'] == 1
        assert config['language_names']['en'] == 'English'
        assert config['text_direction']['en'] == 'LTR'

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path), str(tmp_path / 'missing.json'))


# ─── Tests: _detect_word_data ────────────────────────────────────────────────

class TestDetectWordData:
    def test_osis_with_w_tags(self):
        book = FakeBook('Genesis', {1: [1]})
        bible_mod = FakeBibleModule({'ot': [book]}, {('Genesis', 1, 1): ''})
        bible_mod.get = mock.MagicMock(
            return_value='<w lemma="H7225">In the beginning</w>'
        )
        assert _detect_word_data({'sourcetype': 'OSIS'}, bible_mod, [book]) is True

    def test_osis_without_w_tags(self):
        book = FakeBook('Genesis', {1: [1]})
        bible_mod = FakeBibleModule({'ot': [book]}, {('Genesis', 1, 1): ''})
        bible_mod.get = mock.MagicMock(return_value='In the beginning')
        assert _detect_word_data({'sourcetype': 'OSIS'}, bible_mod, [book]) is False

    def test_non_osis_returns_false(self):
        book = FakeBook('Genesis', {1: [1]})
        bible_mod = FakeBibleModule({'ot': [book]}, {})
        assert _detect_word_data({'sourcetype': 'ThML'}, bible_mod, [book]) is False

    def test_empty_books_returns_false(self):
        assert _detect_word_data({'sourcetype': 'OSIS'}, None, []) is False


# ─── Tests: _write_json ─────────────────────────────────────────────────────

class TestWriteJson:
    def test_writes_valid_json(self, tmp_path):
        path = str(tmp_path / 'test.json')
        data = {'key': 'value', 'number': 42}
        _write_json(data, path)

        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_output_is_minified(self, tmp_path):
        """Every v3 API file is shipped minified — no indentation, no
        padding around separators. Bytes-on-the-wire is the priority."""
        path = str(tmp_path / 'test.json')
        data = {'tokens': [{'t': i, 'src': [i]} for i in range(20)]}
        _write_json(data, path)

        with open(path) as f:
            content = f.read()

        # Round-trips as the same data …
        assert json.loads(content) == data
        # … with no indentation or padding …
        assert '    ' not in content
        assert ': ' not in content
        assert ', ' not in content
        # … and one trailing POSIX newline (and only one).
        assert content.endswith('\n')
        assert '\n' not in content[:-1]

    def test_preserves_unicode_without_escaping(self, tmp_path):
        """ensure_ascii=False keeps multi-byte characters (Hebrew, Greek,
        Cyrillic, …) in their UTF-8 form, which is shorter than \\uXXXX
        escapes and keeps the on-disk bytes equal to the wire bytes."""
        path = str(tmp_path / 'unicode.json')
        # Hebrew: בְּרֵאשִׁ֖ית — first word of Genesis 1:1
        data = {'token': 'בְּרֵאשִׁ֖ית'}
        _write_json(data, path)

        raw = open(path, 'rb').read()
        assert b'\\u' not in raw
        assert 'בְּרֵאשִׁ֖ית'.encode('utf-8') in raw


# ─── Tests: parse_args ───────────────────────────────────────────────────────

class TestParseArgs:
    def test_required_args(self):
        args = parse_args([
            '--source_file', '/path/to/file.zip',
            '--output_path', '/path/to/output',
            '--conf_dir', '/path/to/conf',
            '--bible_conf', '/path/to/conf.json',
        ])
        assert args.source_file == '/path/to/file.zip'
        assert args.output_path == '/path/to/output'
        assert args.conf_dir == '/path/to/conf'
        assert args.bible_conf == '/path/to/conf.json'
        assert args.verbose is False

    def test_verbose_flag(self):
        args = parse_args([
            '--source_file', 'f.zip',
            '--output_path', '/out',
            '--conf_dir', '/conf',
            '--bible_conf', '/conf.json',
            '-v',
        ])
        assert args.verbose is True

    def test_missing_required_exits(self):
        with pytest.raises(SystemExit):
            parse_args([])


# ─── Tests: convert_module output format ─────────────────────────────────────

class TestConvertModuleOutputFormat:
    """Verify the JSON output conforms to the expected v3 API format."""

    @pytest.fixture
    def simple_conversion(self, tmp_path, conf_dir, bible_conf):
        """Run a conversion with a simple single-book, single-chapter Bible."""
        output = tmp_path / 'output'
        output.mkdir()

        genesis = FakeBook('Genesis', {1: [1, 2, 3]})
        verses = {
            ('Genesis', 1, 1): 'In the beginning God created the heaven and the earth.',
            ('Genesis', 1, 2): 'And the earth was without form, and void.',
            ('Genesis', 1, 3): 'And God said, Let there be light: and there was light.',
        }

        fake_mod = _make_fake_module(
            {'ot': [genesis]}, verses,
            config={'sourcetype': 'ThML'},
        )

        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            result = convert_module(
                str(tmp_path / 'TestBible.zip'),
                str(output), str(conf_dir), bible_conf,
            )

        return output, result

    def test_version_file_created(self, simple_conversion):
        output, result = simple_conversion
        assert result is not None
        assert os.path.exists(result)

    def test_version_json_structure(self, simple_conversion):
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        # Required top-level fields
        assert 'translation' in data
        assert 'abbreviation' in data
        assert 'lang' in data
        assert 'language' in data
        assert 'direction' in data
        assert 'encoding' in data
        assert 'books' in data
        assert isinstance(data['books'], list)

        # Distribution fields
        assert 'distribution_lcsh' in data
        assert 'distribution_version' in data
        assert 'distribution_version_date' in data
        assert 'distribution_abbreviation' in data
        assert 'distribution_about' in data
        assert 'distribution_license' in data
        assert 'distribution_sourcetype' in data
        assert 'distribution_source' in data
        assert 'distribution_versification' in data
        assert 'distribution_history' in data

    def test_version_metadata_values(self, simple_conversion):
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        assert data['lang'] == 'en'
        assert data['language'] == 'English'
        assert data['direction'] == 'LTR'

    def test_book_structure(self, simple_conversion):
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        assert len(data['books']) == 1
        book = data['books'][0]
        assert 'nr' in book
        assert 'name' in book
        assert 'chapters' in book
        assert book['nr'] == 1
        assert book['name'] == 'Genesis'

    def test_chapter_structure(self, simple_conversion):
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        chapter = data['books'][0]['chapters'][0]
        assert 'chapter' in chapter
        assert 'name' in chapter
        assert 'verses' in chapter
        assert chapter['chapter'] == 1
        assert chapter['name'] == 'Genesis 1'

    def test_verse_structure(self, simple_conversion):
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        verses = data['books'][0]['chapters'][0]['verses']
        assert len(verses) == 3

        verse = verses[0]
        assert 'chapter' in verse
        assert 'verse' in verse
        assert 'name' in verse
        assert 'text' in verse
        assert verse['chapter'] == 1
        assert verse['verse'] == 1
        assert verse['name'] == 'Genesis 1:1'
        assert 'beginning' in verse['text']

    def test_verse_name_format(self, simple_conversion):
        """Verse names must follow 'BookName Chapter:Verse' format."""
        output, result = simple_conversion
        with open(result) as f:
            data = json.load(f)

        for book in data['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    expected = f"{book['name']} {chapter['chapter']}:{verse['verse']}"
                    assert verse['name'] == expected

    def test_book_file_created(self, simple_conversion):
        output, _ = simple_conversion
        # Book file at abbreviation/book_nr.json
        abbr = 'testbible'
        book_file = output / abbr / '1.json'
        assert book_file.exists()

        with open(book_file) as f:
            data = json.load(f)
        assert 'nr' in data
        assert 'name' in data
        assert 'chapters' in data
        assert 'translation' in data
        assert 'abbreviation' in data

    def test_chapter_file_created(self, simple_conversion):
        output, _ = simple_conversion
        abbr = 'testbible'
        chapter_file = output / abbr / '1' / '1.json'
        assert chapter_file.exists()

        with open(chapter_file) as f:
            data = json.load(f)
        assert 'book_nr' in data
        assert 'book_name' in data
        assert 'chapter' in data
        assert 'name' in data
        assert 'verses' in data
        assert 'translation' in data
        assert 'abbreviation' in data

    def test_chapter_file_metadata(self, simple_conversion):
        """Chapter files must include full translation metadata."""
        output, _ = simple_conversion
        abbr = 'testbible'
        chapter_file = output / abbr / '1' / '1.json'

        with open(chapter_file) as f:
            data = json.load(f)
        assert data['lang'] == 'en'
        assert data['language'] == 'English'
        assert data['direction'] == 'LTR'
        assert data['book_nr'] == 1
        assert data['book_name'] == 'Genesis'
        assert data['chapter'] == 1

    def test_empty_verses_excluded(self, tmp_path, conf_dir, bible_conf):
        """Verses with empty or whitespace-only text should be excluded."""
        output = tmp_path / 'output'
        output.mkdir()

        genesis = FakeBook('Genesis', {1: [1, 2]})
        verses = {
            ('Genesis', 1, 1): 'Valid text',
            ('Genesis', 1, 2): '   ',  # Whitespace only
        }

        fake_mod = _make_fake_module(
            {'ot': [genesis]}, verses,
            config={'sourcetype': 'ThML'},
        )

        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            result = convert_module(
                str(tmp_path / 'TestBible.zip'),
                str(output), str(conf_dir), bible_conf,
            )

        with open(result) as f:
            data = json.load(f)
        verses_out = data['books'][0]['chapters'][0]['verses']
        assert len(verses_out) == 1
        assert verses_out[0]['verse'] == 1

    def test_bracket_only_text_included_as_empty(self, tmp_path, conf_dir, bible_conf):
        """Verses with '[]' have brackets stripped but non-empty original text passes through."""
        output = tmp_path / 'output'
        output.mkdir()

        genesis = FakeBook('Genesis', {1: [1, 2]})
        verses = {
            ('Genesis', 1, 1): 'Valid text',
            ('Genesis', 1, 2): '[]',
        }

        fake_mod = _make_fake_module(
            {'ot': [genesis]}, verses,
            config={'sourcetype': 'ThML'},
        )

        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            result = convert_module(
                str(tmp_path / 'TestBible.zip'),
                str(output), str(conf_dir), bible_conf,
            )

        with open(result) as f:
            data = json.load(f)
        verses_out = data['books'][0]['chapters'][0]['verses']
        # '[]' passes the len(text)>0 check and empty string is not whitespace
        assert len(verses_out) == 2


class TestConvertModuleWithWordData:
    """Verify token+span output for OSIS modules with word-level markup."""

    @pytest.fixture
    def osis_conversion(self, tmp_path, conf_dir, bible_conf):
        """Run a conversion with OSIS word-level data."""
        output = tmp_path / 'output'
        output.mkdir()

        genesis = FakeBook('Genesis', {1: [1]})
        verse_texts = {
            ('Genesis', 1, 1): 'In the beginning God created the heaven and the earth.',
        }
        raw_verses = {
            ('Genesis', 1, 1): (
                '<verse osisID="Gen.1.1">'
                '<w lemma="strong:H07225" morph="oshm:HNcfsa">In the beginning</w> '
                '<w lemma="strong:H0430" morph="oshm:HNcmpa">God</w> '
                '<w lemma="strong:H0853 strong:H01254" morph="oshm:HTr:HVqp3ms">'
                'created</w>'
                '</verse>'
            ),
        }

        fake_mod = _make_fake_module(
            {'ot': [genesis]}, verse_texts,
            raw_verses=raw_verses,
            config={'sourcetype': 'OSIS'},
        )

        # Make get() return raw XML when clean=False
        def patched_get(books=None, chapters=None, verses=None, clean=True):
            key = (books[0], chapters[0], verses[0])
            if not clean and key in raw_verses:
                return raw_verses[key]
            return verse_texts.get(key, '')

        fake_mod.get_bible_from_module.return_value.get = patched_get

        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            result = convert_module(
                str(tmp_path / 'TestBible.zip'),
                str(output), str(conf_dir), bible_conf,
            )

        return output, result

    def test_tokens_present(self, osis_conversion):
        output, result = osis_conversion
        with open(result) as f:
            data = json.load(f)
        verse = data['books'][0]['chapters'][0]['verses'][0]
        assert 'tokens' in verse
        assert isinstance(verse['tokens'], list)
        assert len(verse['tokens']) == 3

    def test_token_structure(self, osis_conversion):
        output, result = osis_conversion
        with open(result) as f:
            data = json.load(f)
        token = data['books'][0]['chapters'][0]['verses'][0]['tokens'][0]
        assert 'token' in token  # text
        assert 'word_start' in token
        assert 'word_end' in token
        assert token['token'] == 'In the beginning'
        assert token['word_start'] == 1
        assert token['word_end'] == 3

    def test_token_attributes(self, osis_conversion):
        output, result = osis_conversion
        with open(result) as f:
            data = json.load(f)
        token = data['books'][0]['chapters'][0]['verses'][0]['tokens'][0]
        assert 'lemma' in token
        assert 'morph' in token

    def test_spans_present(self, osis_conversion):
        output, result = osis_conversion
        with open(result) as f:
            data = json.load(f)
        verse = data['books'][0]['chapters'][0]['verses'][0]
        assert 'spans' in verse
        assert isinstance(verse['spans'], list)

    def test_no_tokens_without_word_data(self, tmp_path, conf_dir, bible_conf):
        """Non-OSIS modules should not have tokens/spans in output."""
        output = tmp_path / 'output'
        output.mkdir()

        genesis = FakeBook('Genesis', {1: [1]})
        verses = {('Genesis', 1, 1): 'In the beginning'}

        fake_mod = _make_fake_module(
            {'ot': [genesis]}, verses,
            config={'sourcetype': 'ThML'},
        )

        with mock.patch('pysword.modules.SwordModules', return_value=fake_mod):
            result = convert_module(
                str(tmp_path / 'TestBible.zip'),
                str(output), str(conf_dir), bible_conf,
            )

        with open(result) as f:
            data = json.load(f)
        verse = data['books'][0]['chapters'][0]['verses'][0]
        assert 'tokens' not in verse
        assert 'spans' not in verse
