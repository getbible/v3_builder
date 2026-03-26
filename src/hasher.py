"""
Hashing module for getBible API builder.

Generates SHA1 checksums and metadata files at three levels:
- Versions (translation-level)
- Books (book-level within each translation)
- Chapters (chapter-level within each book)

Each level produces:
- .sha files (single-line SHA1 hash per JSON file)
- checksum (tab-delimited text listing)
- checksum.json (JSON mapping of key → SHA1 hash)
- Detail files (translations.json / books.json / chapters.json)
- Detail text files (translations / books / chapters)
"""

import hashlib
import json
import logging
import os

from file_ops import write_json_minified

log = logging.getLogger(__name__)

# Default base URL for the public API
_DEFAULT_API_BASE_URL = 'https://api.getbible.net/v3'

# Files to skip when scanning for translation JSON files
_SKIP_NAMES = frozenset({'translations', 'checksum', 'books', 'chapters'})


class ContentHasher:
    """Generates SHA1 checksums and metadata for scripture JSON files.

    Operates on a scripture output directory containing translation-level,
    book-level, and chapter-level JSON files produced by the converter.

    Args:
        target_folder: Path to the scripture output directory.
        api_base_url: Base URL for constructing public API links.
    """

    def __init__(self, target_folder, api_base_url=_DEFAULT_API_BASE_URL):
        if not os.path.isdir(target_folder):
            raise FileNotFoundError(f'Folder {target_folder} not found')
        self._folder = target_folder
        self._api_base_url = api_base_url

    def hash_versions(self):
        """Hash all translation-level JSON files.

        For each {abbreviation}.json in the target folder:
        - Reformats the JSON for consistent output
        - Computes SHA1 hash
        - Writes {abbreviation}.sha
        - Builds translations and checksum files (text and JSON)

        Returns:
            Dict mapping abbreviation to SHA1 hash.
        """
        translations_text = '#\tlanguage\ttranslation\tabbreviation\tdirection\tfilename\tsha\n'
        checksum_text = '#\tfilename\tsha\n'
        checksum_json = {}
        translations_json = {}
        nr = 0

        filenames = sorted(f for f in os.listdir(self._folder)
                           if f.endswith('.json') and f[:-5] not in _SKIP_NAMES)

        for filename in filenames:
            abbreviation = filename[:-5]
            filepath = os.path.join(self._folder, filename)

            data = self._reformat_json(filepath)
            file_hash = _sha1_file(filepath)
            nr += 1

            self._write_text(
                os.path.join(self._folder, f'{abbreviation}.sha'),
                file_hash + '\n',
            )

            meta = {k: v for k, v in data.items() if k != 'books' and k != 'discription'}
            meta['url'] = f'{self._api_base_url}/{abbreviation}.json'
            meta['sha'] = file_hash

            language = data.get('language', '')
            translation = data.get('translation', '')
            direction = data.get('direction', '')

            translations_text += f'{nr}\t{language}\t{translation}\t{abbreviation}\t{direction}\t{abbreviation}\t{file_hash}\n'
            checksum_text += f'{nr}\t{abbreviation}\t{file_hash}\n'
            checksum_json[abbreviation] = file_hash
            translations_json[abbreviation] = meta

            log.info('Hashed version %s', abbreviation)

        self._write_text(os.path.join(self._folder, 'translations'), translations_text)
        self._write_text(os.path.join(self._folder, 'checksum'), checksum_text)
        self._write_json(checksum_json, os.path.join(self._folder, 'checksum.json'))
        self._write_json(translations_json, os.path.join(self._folder, 'translations.json'))

        log.info('Done hashing %d versions', nr)
        return checksum_json

    def hash_books(self):
        """Hash all book-level JSON files within each translation.

        Returns:
            Dict mapping abbreviation to {book_nr: sha1_hash}.
        """
        all_hashes = {}

        filenames = sorted(f for f in os.listdir(self._folder)
                           if f.endswith('.json') and f[:-5] not in _SKIP_NAMES)

        for filename in filenames:
            abbreviation = filename[:-5]
            abbr_dir = os.path.join(self._folder, abbreviation)

            if not os.path.isdir(abbr_dir):
                continue

            filepath = os.path.join(self._folder, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                translation_data = json.load(f)

            language = translation_data.get('language', '')
            translation = translation_data.get('translation', '')
            direction = translation_data.get('direction', '')
            book_nrs = [str(b['nr']) for b in translation_data.get('books', [])]

            books_text = '#\tlanguage\ttranslation\tabbreviation\tdirection\tname\tfilename\tsha\n'
            checksum_text = '#\tfilename\tsha\n'
            checksum_json = {}
            books_json = {}

            for nr in book_nrs:
                book_path = os.path.join(abbr_dir, f'{nr}.json')
                if not os.path.isfile(book_path):
                    continue

                book_data = self._reformat_json(book_path)
                file_hash = _sha1_file(book_path)

                self._write_text(os.path.join(abbr_dir, f'{nr}.sha'), file_hash + '\n')

                meta = {k: v for k, v in book_data.items() if k != 'chapters'}
                meta['url'] = f'{self._api_base_url}/{abbreviation}/{nr}.json'
                meta['sha'] = file_hash

                book_name = book_data.get('name', '')

                checksum_text += f'{nr}\t{nr}\t{file_hash}\n'
                books_text += f'{nr}\t{language}\t{translation}\t{abbreviation}\t{direction}\t{book_name}\t{nr}\t{file_hash}\n'
                checksum_json[nr] = file_hash
                books_json[nr] = meta

                log.debug('Hashed %s/%s.json', abbreviation, nr)

            self._write_text(os.path.join(abbr_dir, 'checksum'), checksum_text)
            self._write_text(os.path.join(abbr_dir, 'books'), books_text)
            self._write_json(checksum_json, os.path.join(abbr_dir, 'checksum.json'))
            self._write_json(books_json, os.path.join(abbr_dir, 'books.json'))

            all_hashes[abbreviation] = checksum_json
            log.info('Hashed %d books for %s', len(checksum_json), abbreviation)

        return all_hashes

    def hash_chapters(self):
        """Hash all chapter-level JSON files within each book.

        Returns:
            Dict mapping abbreviation to {book_nr: {chapter: sha1_hash}}.
        """
        all_hashes = {}

        filenames = sorted(f for f in os.listdir(self._folder)
                           if f.endswith('.json') and f[:-5] not in _SKIP_NAMES)

        for filename in filenames:
            abbreviation = filename[:-5]
            abbr_dir = os.path.join(self._folder, abbreviation)

            if not os.path.isdir(abbr_dir):
                continue

            filepath = os.path.join(self._folder, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                translation_data = json.load(f)

            language = translation_data.get('language', '')
            translation_name = translation_data.get('translation', '')
            direction = translation_data.get('direction', '')
            book_nrs = [str(b['nr']) for b in translation_data.get('books', [])]

            abbr_hashes = {}

            for nr in book_nrs:
                book_path = os.path.join(abbr_dir, f'{nr}.json')
                book_dir = os.path.join(abbr_dir, nr)

                if not os.path.isfile(book_path) or not os.path.isdir(book_dir):
                    continue

                with open(book_path, 'r', encoding='utf-8') as f:
                    book_data = json.load(f)

                book_name = book_data.get('name', '')
                chapters = sorted(
                    [c['chapter'] for c in book_data.get('chapters', [])],
                    key=lambda x: int(x) if isinstance(x, (int, str)) and str(x).isdigit() else 0
                )

                chapters_text = '#\tlanguage\ttranslation\tabbreviation\ttextdirection\tbook_nr\tbook_name\tfilename\tsha\n'
                checksum_text = '#\tfilename\tsha\n'
                checksum_json = {}
                chapters_json = {}

                for chapter in chapters:
                    ch_str = str(chapter)
                    ch_path = os.path.join(book_dir, f'{ch_str}.json')

                    if not os.path.isfile(ch_path):
                        continue

                    file_hash = _sha1_file(ch_path)

                    self._write_text(
                        os.path.join(book_dir, f'{ch_str}.sha'),
                        file_hash + '\n',
                    )

                    with open(ch_path, 'r', encoding='utf-8') as f:
                        ch_data = json.load(f)

                    meta = {k: v for k, v in ch_data.items() if k != 'verses'}
                    meta['url'] = f'{self._api_base_url}/{abbreviation}/{nr}/{ch_str}.json'
                    meta['sha'] = file_hash

                    checksum_text += f'{ch_str}\t{ch_str}\t{file_hash}\n'
                    chapters_text += f'{ch_str}\t{language}\t{translation_name}\t{abbreviation}\t{direction}\t{nr}\t{book_name}\t{ch_str}\t{file_hash}\n'
                    checksum_json[ch_str] = file_hash
                    chapters_json[ch_str] = meta

                    log.debug('Hashed %s/%s/%s.json', abbreviation, nr, ch_str)

                self._write_text(os.path.join(book_dir, 'checksum'), checksum_text)
                self._write_text(os.path.join(book_dir, 'chapters'), chapters_text)
                self._write_json(checksum_json, os.path.join(book_dir, 'checksum.json'))
                self._write_json(chapters_json, os.path.join(book_dir, 'chapters.json'))

                abbr_hashes[nr] = checksum_json

            all_hashes[abbreviation] = abbr_hashes
            log.info('Hashed chapters for %s (%d books)', abbreviation, len(abbr_hashes))

        return all_hashes

    def hash_all(self):
        """Run all three hashing levels in sequence.

        Returns:
            Tuple of (version_hashes, book_hashes, chapter_hashes).
        """
        log.info('Starting version hashing...')
        version_hashes = self.hash_versions()
        log.info('Starting book hashing...')
        book_hashes = self.hash_books()
        log.info('Starting chapter hashing...')
        chapter_hashes = self.hash_chapters()
        log.info('All hashing complete.')
        return version_hashes, book_hashes, chapter_hashes

    @staticmethod
    def _write_json(data, path):
        """Write minified JSON. See :func:`file_ops.write_json_minified`.

        Reformatting is what produces the SHA1 hashes consumers verify;
        the format must match what the converter writes byte-for-byte
        so the hash is stable across pipeline stages.
        """
        write_json_minified(data, path)

    @staticmethod
    def _reformat_json(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ContentHasher._write_json(data, path)
        return data

    @staticmethod
    def _write_text(path, content):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)


def _sha1_file(path):
    """Compute SHA1 hash of a file's contents."""
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


# ── Backward-compatible module-level functions ───────────────────────────────

def hash_versions(target_folder):
    """Hash all translation-level JSON files. See ContentHasher.hash_versions."""
    return ContentHasher(target_folder).hash_versions()


def hash_books(target_folder):
    """Hash all book-level JSON files. See ContentHasher.hash_books."""
    return ContentHasher(target_folder).hash_books()


def hash_chapters(target_folder):
    """Hash all chapter-level JSON files. See ContentHasher.hash_chapters."""
    return ContentHasher(target_folder).hash_chapters()


def hash_all(target_folder):
    """Run all three hashing levels. See ContentHasher.hash_all."""
    return ContentHasher(target_folder).hash_all()
