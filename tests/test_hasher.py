"""Tests for hasher.py — SHA1 hashing at version, book, and chapter levels."""

import hashlib
import json
import os
import pytest

from hasher import hash_versions, hash_books, hash_chapters, hash_all


@pytest.fixture
def scripture_dir(tmp_path):
    """
    Create a realistic scripture directory structure matching converter.py output.

    Structure:
        tmp_path/
            kjv.json          (translation-level)
            kjv/
                1.json        (book: Genesis)
                1/
                    1.json    (chapter: Genesis 1)
                    2.json    (chapter: Genesis 2)
                2.json        (book: Exodus)
                2/
                    1.json    (chapter: Exodus 1)
            aov.json          (second translation)
            aov/
                1.json
                1/
                    1.json
    """
    # --- KJV translation ---
    kjv_gen_ch1 = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'book_nr': 1,
        'book_name': 'Genesis',
        'chapter': 1,
        'name': 'Genesis 1',
        'verses': [
            {'chapter': 1, 'verse': 1, 'name': 'Genesis 1:1',
             'text': 'In the beginning God created the heaven and the earth.'}
        ]
    }
    kjv_gen_ch2 = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'book_nr': 1,
        'book_name': 'Genesis',
        'chapter': 2,
        'name': 'Genesis 2',
        'verses': [
            {'chapter': 2, 'verse': 1, 'name': 'Genesis 2:1',
             'text': 'Thus the heavens and the earth were finished.'}
        ]
    }
    kjv_exo_ch1 = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'book_nr': 2,
        'book_name': 'Exodus',
        'chapter': 1,
        'name': 'Exodus 1',
        'verses': [
            {'chapter': 1, 'verse': 1, 'name': 'Exodus 1:1',
             'text': 'Now these are the names of the children of Israel.'}
        ]
    }
    kjv_gen_book = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'nr': 1,
        'name': 'Genesis',
        'chapters': [
            {'chapter': 1, 'name': 'Genesis 1', 'verses': kjv_gen_ch1['verses']},
            {'chapter': 2, 'name': 'Genesis 2', 'verses': kjv_gen_ch2['verses']},
        ]
    }
    kjv_exo_book = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'nr': 2,
        'name': 'Exodus',
        'chapters': [
            {'chapter': 1, 'name': 'Exodus 1', 'verses': kjv_exo_ch1['verses']},
        ]
    }
    kjv_translation = {
        'translation': 'King James Version',
        'abbreviation': 'kjv',
        'description': 'King James Version (1769)',
        'lang': 'en',
        'language': 'English',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'books': [
            {'nr': 1, 'name': 'Genesis', 'chapters': kjv_gen_book['chapters']},
            {'nr': 2, 'name': 'Exodus', 'chapters': kjv_exo_book['chapters']},
        ],
        'distribution_license': 'Public Domain',
    }

    # --- AOV translation (Afrikaans) ---
    aov_gen_ch1 = {
        'translation': 'Afrikaans 1953',
        'abbreviation': 'aov',
        'lang': 'af',
        'language': 'Afrikaans',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'book_nr': 1,
        'book_name': 'Genesis',
        'chapter': 1,
        'name': 'Genesis 1',
        'verses': [
            {'chapter': 1, 'verse': 1, 'name': 'Genesis 1:1',
             'text': 'In die begin het God die hemel en die aarde geskape.'}
        ]
    }
    aov_gen_book = {
        'translation': 'Afrikaans 1953',
        'abbreviation': 'aov',
        'lang': 'af',
        'language': 'Afrikaans',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'nr': 1,
        'name': 'Genesis',
        'chapters': [
            {'chapter': 1, 'name': 'Genesis 1', 'verses': aov_gen_ch1['verses']},
        ]
    }
    aov_translation = {
        'translation': 'Afrikaans 1953',
        'abbreviation': 'aov',
        'description': 'Afrikaans Ou Vertaling',
        'lang': 'af',
        'language': 'Afrikaans',
        'direction': 'LTR',
        'encoding': 'UTF-8',
        'books': [
            {'nr': 1, 'name': 'Genesis', 'chapters': aov_gen_book['chapters']},
        ],
        'distribution_license': 'Public Domain',
    }

    # Write all files
    def write(subpath, data):
        full = tmp_path / subpath
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, 'w') as f:
            json.dump(data, f, indent=4)

    write('kjv.json', kjv_translation)
    write('kjv/1.json', kjv_gen_book)
    write('kjv/1/1.json', kjv_gen_ch1)
    write('kjv/1/2.json', kjv_gen_ch2)
    write('kjv/2.json', kjv_exo_book)
    write('kjv/2/1.json', kjv_exo_ch1)
    write('aov.json', aov_translation)
    write('aov/1.json', aov_gen_book)
    write('aov/1/1.json', aov_gen_ch1)

    return tmp_path


def sha1_of_file(path):
    """Helper to compute SHA1 for verification."""
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


# =========================================================================
# hash_versions
# =========================================================================

class TestHashVersions:
    def test_returns_checksum_dict(self, scripture_dir):
        result = hash_versions(str(scripture_dir))
        assert isinstance(result, dict)
        assert 'kjv' in result
        assert 'aov' in result
        assert len(result) == 2

    def test_sha_files_created(self, scripture_dir):
        hash_versions(str(scripture_dir))
        assert (scripture_dir / 'kjv.sha').exists()
        assert (scripture_dir / 'aov.sha').exists()

    def test_sha_file_matches_actual_hash(self, scripture_dir):
        result = hash_versions(str(scripture_dir))
        actual_hash = sha1_of_file(scripture_dir / 'kjv.json')
        sha_content = (scripture_dir / 'kjv.sha').read_text().strip()
        assert sha_content == actual_hash
        assert result['kjv'] == actual_hash

    def test_checksum_json_created(self, scripture_dir):
        hash_versions(str(scripture_dir))
        path = scripture_dir / 'checksum.json'
        assert path.exists()
        data = json.loads(path.read_text())
        assert 'kjv' in data
        assert 'aov' in data

    def test_translations_json_created(self, scripture_dir):
        hash_versions(str(scripture_dir))
        path = scripture_dir / 'translations.json'
        assert path.exists()
        data = json.loads(path.read_text())
        assert 'kjv' in data
        kjv = data['kjv']
        assert kjv['language'] == 'English'
        assert kjv['translation'] == 'King James Version'
        assert kjv['direction'] == 'LTR'
        assert 'url' in kjv
        assert 'sha' in kjv
        # books array should be excluded
        assert 'books' not in kjv

    def test_translations_text_created(self, scripture_dir):
        hash_versions(str(scripture_dir))
        content = (scripture_dir / 'translations').read_text()
        assert content.startswith('#\t')
        assert 'kjv' in content
        assert 'English' in content

    def test_checksum_text_created(self, scripture_dir):
        hash_versions(str(scripture_dir))
        content = (scripture_dir / 'checksum').read_text()
        assert content.startswith('#\t')
        assert 'kjv' in content

    def test_skips_metadata_files(self, scripture_dir):
        """Should not try to hash translations.json, checksum.json, etc."""
        # Create a translations.json that already exists (from previous run)
        (scripture_dir / 'translations.json').write_text('{}')
        (scripture_dir / 'checksum.json').write_text('{}')
        # Should not crash or include these in output
        result = hash_versions(str(scripture_dir))
        assert 'translations' not in result
        assert 'checksum' not in result

    def test_nonexistent_folder_raises(self):
        with pytest.raises(FileNotFoundError):
            hash_versions('/nonexistent/path')

    def test_reformatted_files_are_minified(self, scripture_dir):
        """Regression for the kjv.json / kjva.json > 100 MB push failure.

        Every v3 API file — including the per-version translation files
        the hasher reformats — must be shipped minified. The previous
        ``indent=2, ensure_ascii=True`` reformat ballooned kjv.json
        from ~50 MB compact back to ~167 MB and caused the remote
        pre-receive hook to reject the push:

            File kjv.json is 167.52 MB; this exceeds GitHub's file
            size limit of 100.00 MB

        This test verifies hash_versions leaves the file minified,
        with a single trailing POSIX newline, and that the data
        round-trips intact.
        """
        hash_versions(str(scripture_dir))
        content = (scripture_dir / 'kjv.json').read_text()

        data = json.loads(content)
        assert data['abbreviation'] == 'kjv'

        # Minified: no indent, no separator padding.
        assert '    ' not in content
        assert ': ' not in content
        assert ', ' not in content
        # Single trailing newline only.
        assert content.endswith('\n')
        assert '\n' not in content[:-1]

    def test_url_format(self, scripture_dir):
        hash_versions(str(scripture_dir))
        data = json.loads((scripture_dir / 'translations.json').read_text())
        assert data['kjv']['url'] == 'https://api.getbible.net/v3/kjv.json'
        assert data['aov']['url'] == 'https://api.getbible.net/v3/aov.json'


# =========================================================================
# hash_books
# =========================================================================

class TestHashBooks:
    def test_returns_nested_dict(self, scripture_dir):
        result = hash_books(str(scripture_dir))
        assert 'kjv' in result
        assert '1' in result['kjv']  # Genesis
        assert '2' in result['kjv']  # Exodus
        assert 'aov' in result
        assert '1' in result['aov']

    def test_sha_files_created(self, scripture_dir):
        hash_books(str(scripture_dir))
        assert (scripture_dir / 'kjv' / '1.sha').exists()
        assert (scripture_dir / 'kjv' / '2.sha').exists()
        assert (scripture_dir / 'aov' / '1.sha').exists()

    def test_sha_matches_actual_hash(self, scripture_dir):
        result = hash_books(str(scripture_dir))
        actual = sha1_of_file(scripture_dir / 'kjv' / '1.json')
        sha_content = (scripture_dir / 'kjv' / '1.sha').read_text().strip()
        assert sha_content == actual
        assert result['kjv']['1'] == actual

    def test_checksum_json(self, scripture_dir):
        hash_books(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / 'checksum.json').read_text())
        assert '1' in data
        assert '2' in data

    def test_books_json(self, scripture_dir):
        hash_books(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / 'books.json').read_text())
        assert '1' in data
        gen = data['1']
        assert gen['name'] == 'Genesis'
        assert 'url' in gen
        assert 'sha' in gen
        # chapters array should be excluded
        assert 'chapters' not in gen

    def test_books_text(self, scripture_dir):
        hash_books(str(scripture_dir))
        content = (scripture_dir / 'kjv' / 'books').read_text()
        assert 'Genesis' in content
        assert 'Exodus' in content

    def test_checksum_text(self, scripture_dir):
        hash_books(str(scripture_dir))
        content = (scripture_dir / 'kjv' / 'checksum').read_text()
        assert content.startswith('#\t')

    def test_url_format(self, scripture_dir):
        hash_books(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / 'books.json').read_text())
        assert data['1']['url'] == 'https://api.getbible.net/v3/kjv/1.json'
        assert data['2']['url'] == 'https://api.getbible.net/v3/kjv/2.json'

    def test_skips_translations_without_directory(self, scripture_dir):
        """If abbreviation directory doesn't exist, skip silently."""
        # Create a translation JSON without a matching directory
        orphan = {'translation': 'Orphan', 'abbreviation': 'orp',
                  'language': 'Test', 'direction': 'LTR', 'books': []}
        with open(scripture_dir / 'orp.json', 'w') as f:
            json.dump(orphan, f)
        result = hash_books(str(scripture_dir))
        assert 'orp' not in result


# =========================================================================
# hash_chapters
# =========================================================================

class TestHashChapters:
    def test_returns_nested_dict(self, scripture_dir):
        result = hash_chapters(str(scripture_dir))
        assert 'kjv' in result
        assert '1' in result['kjv']  # Genesis
        assert '1' in result['kjv']['1']  # Chapter 1
        assert '2' in result['kjv']['1']  # Chapter 2

    def test_sha_files_created(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        assert (scripture_dir / 'kjv' / '1' / '1.sha').exists()
        assert (scripture_dir / 'kjv' / '1' / '2.sha').exists()
        assert (scripture_dir / 'kjv' / '2' / '1.sha').exists()

    def test_sha_matches_actual_hash(self, scripture_dir):
        result = hash_chapters(str(scripture_dir))
        actual = sha1_of_file(scripture_dir / 'kjv' / '1' / '1.json')
        sha_content = (scripture_dir / 'kjv' / '1' / '1.sha').read_text().strip()
        assert sha_content == actual
        assert result['kjv']['1']['1'] == actual

    def test_checksum_json(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / '1' / 'checksum.json').read_text())
        assert '1' in data
        assert '2' in data

    def test_chapters_json(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / '1' / 'chapters.json').read_text())
        assert '1' in data
        ch1 = data['1']
        assert ch1['name'] == 'Genesis 1'
        assert 'url' in ch1
        assert 'sha' in ch1
        # verses should be excluded
        assert 'verses' not in ch1

    def test_chapters_text(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        content = (scripture_dir / 'kjv' / '1' / 'chapters').read_text()
        assert 'Genesis' in content

    def test_url_format(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        data = json.loads((scripture_dir / 'kjv' / '1' / 'chapters.json').read_text())
        assert data['1']['url'] == 'https://api.getbible.net/v3/kjv/1/1.json'
        assert data['2']['url'] == 'https://api.getbible.net/v3/kjv/1/2.json'

    def test_multiple_translations(self, scripture_dir):
        result = hash_chapters(str(scripture_dir))
        assert 'aov' in result
        assert '1' in result['aov']
        assert '1' in result['aov']['1']


# =========================================================================
# hash_all
# =========================================================================

class TestHashAll:
    def test_runs_all_three_levels(self, scripture_dir):
        v, b, c = hash_all(str(scripture_dir))
        # Versions
        assert 'kjv' in v
        assert 'aov' in v
        # Books
        assert '1' in b['kjv']
        # Chapters
        assert '1' in c['kjv']['1']

    def test_all_output_files_created(self, scripture_dir):
        hash_all(str(scripture_dir))

        # Version level
        assert (scripture_dir / 'translations.json').exists()
        assert (scripture_dir / 'translations').exists()
        assert (scripture_dir / 'checksum.json').exists()
        assert (scripture_dir / 'checksum').exists()
        assert (scripture_dir / 'kjv.sha').exists()

        # Book level
        assert (scripture_dir / 'kjv' / 'books.json').exists()
        assert (scripture_dir / 'kjv' / 'books').exists()
        assert (scripture_dir / 'kjv' / 'checksum.json').exists()
        assert (scripture_dir / 'kjv' / '1.sha').exists()

        # Chapter level
        assert (scripture_dir / 'kjv' / '1' / 'chapters.json').exists()
        assert (scripture_dir / 'kjv' / '1' / 'chapters').exists()
        assert (scripture_dir / 'kjv' / '1' / 'checksum.json').exists()
        assert (scripture_dir / 'kjv' / '1' / '1.sha').exists()


# =========================================================================
# Hash consistency / integrity
# =========================================================================

class TestHashIntegrity:
    def test_version_hash_matches_reformatted_file(self, scripture_dir):
        """The hash in checksum.json must match the actual file after reformatting."""
        hash_versions(str(scripture_dir))
        data = json.loads((scripture_dir / 'checksum.json').read_text())
        for abbr, expected_hash in data.items():
            actual = sha1_of_file(scripture_dir / f'{abbr}.json')
            assert actual == expected_hash, f'Hash mismatch for {abbr}'

    def test_book_hash_matches_reformatted_file(self, scripture_dir):
        hash_books(str(scripture_dir))
        for abbr_dir in [scripture_dir / 'kjv', scripture_dir / 'aov']:
            if not (abbr_dir / 'checksum.json').exists():
                continue
            data = json.loads((abbr_dir / 'checksum.json').read_text())
            for nr, expected_hash in data.items():
                actual = sha1_of_file(abbr_dir / f'{nr}.json')
                assert actual == expected_hash

    def test_chapter_hash_matches_file(self, scripture_dir):
        hash_chapters(str(scripture_dir))
        ch_dir = scripture_dir / 'kjv' / '1'
        data = json.loads((ch_dir / 'checksum.json').read_text())
        for ch, expected_hash in data.items():
            actual = sha1_of_file(ch_dir / f'{ch}.json')
            assert actual == expected_hash

    def test_idempotent(self, scripture_dir):
        """Running hash_all twice produces identical results."""
        hash_all(str(scripture_dir))
        first_checksum = (scripture_dir / 'checksum.json').read_text()
        first_kjv_sha = (scripture_dir / 'kjv.sha').read_text()

        hash_all(str(scripture_dir))
        second_checksum = (scripture_dir / 'checksum.json').read_text()
        second_kjv_sha = (scripture_dir / 'kjv.sha').read_text()

        assert first_checksum == second_checksum
        assert first_kjv_sha == second_kjv_sha
