"""
SWORD module to JSON converter for getBible API.

Provides ConversionConfig for loading Bible configuration data and
SwordModuleConverter for converting Crosswire SWORD modules to
structured JSON files at version, book, and chapter levels.

Originally derived from sword_to_json by Jake Wasdin (2017) and
Llewellyn van der Merwe (2018), licensed under BSD 2-Clause.
Substantially rewritten for v3 with token+span model, class-based
architecture, and SOLID design principles.
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field

from file_ops import write_json_minified
from osis_parser import parse_osis_verse

log = logging.getLogger(__name__)


@dataclass
class ConversionConfig:
    """Configuration data needed for SWORD-to-JSON conversion.

    Holds all lookup tables loaded from the conf/ directory: translation
    name mappings, book numbers, language metadata, etc.

    Use ConversionConfig.from_files() to load from the standard layout.
    """

    translation_names: dict = field(default_factory=dict)
    v1_translations: dict = field(default_factory=dict)
    book_numbers: dict = field(default_factory=dict)
    book_names: dict = field(default_factory=dict)
    lang_correction: dict = field(default_factory=dict)
    language_names: dict = field(default_factory=dict)
    text_direction: dict = field(default_factory=dict)

    @classmethod
    def from_files(cls, conf_dir, bible_conf):
        """Load configuration from the standard conf/ directory layout.

        Args:
            conf_dir: Path to the configuration directory.
            bible_conf: Path to the Bible modules map JSON.

        Returns:
            ConversionConfig instance with all data loaded.
        """
        def _load(path):
            with open(path, 'r') as f:
                return json.load(f)

        return cls(
            translation_names=_load(bible_conf),
            v1_translations=_load(os.path.join(conf_dir, 'v1Translations.json')),
            book_numbers=_load(os.path.join(conf_dir, 'bookNumbers.json')),
            book_names=_load(os.path.join(conf_dir, 'bookNames.json')),
            lang_correction=_load(os.path.join(conf_dir, 'langCorrection.json')),
            language_names=_load(os.path.join(conf_dir, 'languageNames.json')),
            text_direction=_load(os.path.join(conf_dir, 'textDirection.json')),
        )


def normalize_verse_text(text):
    """Remove source line endings before a verse while preserving its body."""

    return text.lstrip('\r\n')


class SwordModuleConverter:
    """Converts SWORD Bible modules to getBible JSON format.

    Reads .zip SWORD modules via pysword and outputs structured JSON at
    three levels: version, book, and chapter. For OSIS modules with
    word-level markup (<w> tags), includes token+span annotations.

    Args:
        config: ConversionConfig with all lookup tables.
        output_path: Directory for JSON output.
        conf_dir: Configuration directory path (for local book name files).
        book_name_resolver: Optional callable(book_nr, default_name,
            abbreviation, conf_dir, config) -> str for resolving display
            names. If None, uses the default resolver with HTTP fallback.
    """

    def __init__(self, config, output_path, conf_dir=None, book_name_resolver=None):
        self._config = config
        self._output_path = output_path
        self._conf_dir = conf_dir
        self._resolve_book_name = book_name_resolver or self._default_book_name_resolver

    def convert(self, source_file):
        """Convert a single SWORD module to JSON files.

        Produces JSON at three levels:
        - Version level: {abbreviation}.json (complete Bible)
        - Book level: {abbreviation}/{book_nr}.json
        - Chapter level: {abbreviation}/{book_nr}/{chapter}.json

        Args:
            source_file: Path to the SWORD module .zip file.

        Returns:
            Path to the version-level JSON file, or None on failure.
        """
        from pysword.modules import SwordModules

        bible_version = os.path.basename(source_file).replace('.zip', '')
        log.info('Converting module: %s', bible_version)

        module = SwordModules(source_file)
        module_config = module.parse_modules()[bible_version]
        bible_mod = module.get_bible_from_module(bible_version)

        testaments = bible_mod.get_structure()._books
        books = []
        for testament in testaments:
            books += testaments[testament]

        has_word_data = self._detect_word_data(module_config, bible_mod, books)
        if has_word_data:
            log.info('Module %s has OSIS word-level markup', bible_version)

        abbreviation = self._config.translation_names.get(bible_version, bible_version.lower())

        lang = module_config.get('lang', '')
        bible = {
            'translation': self._config.v1_translations.get(
                abbreviation, module_config.get('description', bible_version)
            ),
            'abbreviation': abbreviation,
            'description': module_config.get('description', ''),
            'lang': self._config.lang_correction.get(lang, lang),
            'language': self._config.language_names.get(lang, ''),
            'direction': self._config.text_direction.get(lang, 'LTR'),
            'encoding': module_config.get('encoding', ''),
        }

        shared_meta = {
            'translation': bible['translation'],
            'abbreviation': abbreviation,
            'lang': bible['lang'],
            'language': bible['language'],
            'direction': bible['direction'],
            'encoding': bible['encoding'],
        }

        os.makedirs(self._output_path, exist_ok=True)

        bible['books'] = []
        total_books = len(books)

        for book_idx, book in enumerate(books, 1):
            book_nr = self._config.book_numbers.get(book.name)
            book_name = self._resolve_book_name(
                book_nr,
                self._config.book_names.get(book.name, book.name),
                abbreviation, self._conf_dir, self._config,
            )
            book_path = os.path.join(self._output_path, abbreviation, str(book_nr))
            os.makedirs(book_path, exist_ok=True)

            book_has_verses = False
            chapters = []

            for chapter in range(1, book.num_chapters + 1):
                verses = []
                chapter_has_verses = False

                for verse in range(1, len(book.get_indicies(chapter)) + 1):
                    text = bible_mod.get(
                        books=[book.name], chapters=[chapter], verses=[verse]
                    )
                    text = normalize_verse_text(text)
                    cleaned = text.replace('[]', '')
                    if len(text) > 0 and not cleaned.isspace():
                        book_has_verses = True
                        chapter_has_verses = True
                        verse_data = {
                            'chapter': chapter,
                            'verse': verse,
                            'name': f'{book_name} {chapter}:{verse}',
                            'text': text,
                        }
                        if has_word_data:
                            raw_text = bible_mod.get(
                                books=[book.name], chapters=[chapter],
                                verses=[verse], clean=False,
                            )
                            word_data = parse_osis_verse(raw_text, text)
                            if word_data:
                                verse_data['tokens'] = word_data['tokens']
                                verse_data['spans'] = word_data['spans']
                        verses.append(verse_data)

                if chapter_has_verses:
                    chapter_entry = {
                        'chapter': chapter,
                        'name': f'{book_name} {chapter}',
                        'verses': verses,
                    }
                    chapters.append(chapter_entry)

                    chapter_data = dict(shared_meta)
                    chapter_data['book_nr'] = book_nr
                    chapter_data['book_name'] = book_name
                    chapter_data['chapter'] = chapter
                    chapter_data['name'] = f'{book_name} {chapter}'
                    chapter_data['verses'] = verses
                    _write_json(chapter_data, os.path.join(book_path, f'{chapter}.json'))
                    log.debug(
                        '[%s] Chapter %d of %s written',
                        abbreviation, chapter, book_name,
                    )

            if book_has_verses:
                bible['books'].append({
                    'nr': book_nr,
                    'name': book_name,
                    'chapters': chapters,
                })

                book_data = dict(shared_meta)
                book_data['nr'] = book_nr
                book_data['name'] = book_name
                book_data['chapters'] = chapters
                _write_json(book_data, book_path + '.json')
                log.info(
                    '[%d/%d] Book "%s" added to %s',
                    book_idx, total_books, book_name, abbreviation,
                )

        bible['distribution_lcsh'] = module_config.get('lcsh', '')
        bible['distribution_version'] = module_config.get('version', '')
        bible['distribution_version_date'] = module_config.get(
            'SwordVersionDate', module_config.get('swordversiondate', '')
        )
        bible['distribution_abbreviation'] = module_config.get('abbreviation', abbreviation)
        bible['distribution_about'] = module_config.get('about', '')
        bible['distribution_license'] = module_config.get('distributionlicense', '')
        bible['distribution_sourcetype'] = module_config.get('sourcetype', '')
        bible['distribution_source'] = module_config.get('textsource', '')
        bible['distribution_versification'] = module_config.get('versification', '')
        bible['distribution_history'] = {
            k: v for k, v in module_config.items() if 'history' in k
        }

        version_filename = f'{abbreviation}.json'
        version_path = os.path.join(self._output_path, version_filename)
        _write_json(bible, version_path)
        log.info('Module %s conversion complete (%d books)', abbreviation, len(bible['books']))

        return version_path

    @staticmethod
    def _detect_word_data(module_config, bible_mod, books):
        """Check if the SWORD module contains OSIS word-level markup."""
        source_type = module_config.get('sourcetype', '').upper()
        if source_type != 'OSIS':
            return False

        for book in books:
            try:
                sample = bible_mod.get(
                    books=[book.name], chapters=[1], verses=[1], clean=False
                )
                if sample and '<w ' in sample:
                    return True
                return False
            except Exception:
                log.debug('Could not sample book %s for word data detection', book.name)
                continue

        return False

    @staticmethod
    def _default_book_name_resolver(book_nr, book_name_default, abbreviation, conf_dir, config):
        """Resolve book display name from local files, v1 API, or SWORD default."""
        if conf_dir:
            local_path = os.path.join(conf_dir, f'books_{abbreviation}.json')
            if os.path.exists(local_path):
                with open(local_path, 'r') as f:
                    local_names = json.load(f)
                return local_names.get(str(book_nr), book_name_default)

        if isinstance(config, ConversionConfig):
            v1 = config.v1_translations
        else:
            v1 = config.get('v1_translations', {})

        if abbreviation in v1:
            try:
                import requests
                v1_books = requests.get(
                    f'https://api.getbible.net/v1/{abbreviation}/books.json'
                ).json()
                return v1_books.get(str(book_nr), {}).get('name', book_name_default)
            except Exception:
                log.debug('Could not fetch v1 book names for %s', abbreviation)

        return book_name_default


# All v3 API output is minified. The data is a build artifact served
# directly to clients; readability on disk is not a goal, but every
# byte of whitespace would multiply across ~120K chapter files served
# from the public API. _write_json is preserved as a private alias so
# existing call sites and tests don't have to change.
def _write_json(data, output_file):
    """Write ``data`` to ``output_file`` as minified JSON.

    See :func:`file_ops.write_json_minified`.
    """
    write_json_minified(data, output_file)


# ── Backward-compatible module-level functions ───────────────────────────────

def load_config(conf_dir, bible_conf):
    """Load configuration files. See ConversionConfig.from_files."""
    cfg = ConversionConfig.from_files(conf_dir, bible_conf)
    return {
        'translation_names': cfg.translation_names,
        'v1_translations': cfg.v1_translations,
        'book_numbers': cfg.book_numbers,
        'book_names': cfg.book_names,
        'lang_correction': cfg.lang_correction,
        'language_names': cfg.language_names,
        'text_direction': cfg.text_direction,
    }


def convert_module(source_file, output_path, conf_dir, bible_conf):
    """Convert a SWORD module. See SwordModuleConverter.convert."""
    config = ConversionConfig.from_files(conf_dir, bible_conf)
    converter = SwordModuleConverter(config, output_path, conf_dir=conf_dir)
    return converter.convert(source_file)


def _detect_word_data(module_config, bible_mod, books):
    """Check for OSIS word markup. See SwordModuleConverter._detect_word_data."""
    return SwordModuleConverter._detect_word_data(module_config, bible_mod, books)


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Convert a SWORD Bible module to getBible JSON format',
    )
    parser.add_argument('--source_file', required=True,
                        help='Path to the SWORD module .zip file')
    parser.add_argument('--output_path', required=True,
                        help='Directory for JSON output')
    parser.add_argument('--conf_dir', required=True,
                        help='Path to configuration files')
    parser.add_argument('--bible_conf', required=True,
                        help='Path to Bible modules map JSON')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable debug logging')
    return parser.parse_args(argv)


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    config = ConversionConfig.from_files(args.conf_dir, args.bible_conf)
    converter = SwordModuleConverter(config, args.output_path, conf_dir=args.conf_dir)
    result = converter.convert(args.source_file)

    if result:
        log.info('Output written to %s', result)
        return 0
    else:
        log.error('Conversion failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
