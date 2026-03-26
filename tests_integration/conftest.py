"""Fixtures for integration tests that exercise real SWORD modules.

Session-scoped fixtures handle downloading and converting modules once
per test run. Random sampling fixtures pick different books/chapters/verses
each run for broad coverage without exhaustive iteration.
"""

import json
import os
import random
import sys

import pytest

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONF_DIR = os.path.join(_PROJECT_ROOT, 'conf')
_TEST_MODULES_MAP = os.path.join(_CONF_DIR, 'CrosswireModulesMapTest.json')


def _require_pysword():
    """Import pysword or skip the test if not installed."""
    pytest.importorskip("pysword", reason="pysword required for integration tests")


# ── Session-scoped fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def integration_rng(request):
    """Seeded random.Random instance for reproducible random sampling.

    Prints the seed at session start so failures can be reproduced with
    --integration-seed=<value>.
    """
    seed = request.config.getoption("--integration-seed")
    if seed is None:
        seed = random.randint(0, 2**31)
    print(f"\n  Integration test seed: {seed}")
    print(f"  Reproduce with: pytest tests_integration/ --run-integration --integration-seed={seed}")
    return random.Random(seed)


@pytest.fixture(scope="session")
def sword_cache_dir(request):
    """Directory for caching downloaded SWORD module zips.

    Uses --integration-cache-dir if provided, otherwise .sword_cache/
    in the project root. Persists across runs so modules are only
    downloaded once.
    """
    cache_dir = request.config.getoption("--integration-cache-dir")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir
    default = os.path.join(_PROJECT_ROOT, '.sword_cache')
    os.makedirs(default, exist_ok=True)
    return default


@pytest.fixture(scope="session")
def test_module_map():
    """Load the test modules map from CrosswireModulesMapTest.json."""
    with open(_TEST_MODULES_MAP, 'r') as f:
        return json.load(f)


@pytest.fixture(scope="session")
def downloaded_modules(test_module_map, sword_cache_dir):
    """Download all test SWORD modules (cached, skips existing)."""
    from download import download_modules
    paths = download_modules(test_module_map, sword_cache_dir)
    assert len(paths) > 0, "No modules could be downloaded"
    return paths


@pytest.fixture(scope="session")
def conversion_config():
    """Load the real conversion config from conf/ directory."""
    _require_pysword()
    from converter import ConversionConfig
    return ConversionConfig.from_files(_CONF_DIR, _TEST_MODULES_MAP)


@pytest.fixture(scope="session")
def conversion_output_dir(tmp_path_factory):
    """Session-scoped temporary directory for conversion output."""
    return str(tmp_path_factory.mktemp("scripture_output"))


@pytest.fixture(scope="session")
def converted_modules(downloaded_modules, conversion_config, conversion_output_dir):
    """Convert all downloaded modules and return metadata.

    Returns a dict mapping abbreviation to:
        {
            'sword_name': str,
            'abbreviation': str,
            'version_path': str,
            'version_data': dict,
            'output_dir': str,
        }
    """
    _require_pysword()
    from converter import SwordModuleConverter

    converter = SwordModuleConverter(
        conversion_config, conversion_output_dir, conf_dir=_CONF_DIR,
    )
    results = {}
    for zip_path in downloaded_modules:
        sword_name = os.path.basename(zip_path).replace('.zip', '')
        version_path = converter.convert(zip_path)
        assert version_path is not None, f"Conversion failed for {sword_name}"

        with open(version_path, 'r', encoding='utf-8') as f:
            version_data = json.load(f)

        abbreviation = version_data['abbreviation']
        results[abbreviation] = {
            'sword_name': sword_name,
            'abbreviation': abbreviation,
            'version_path': version_path,
            'version_data': version_data,
            'output_dir': conversion_output_dir,
        }
    return results


@pytest.fixture(scope="session")
def hashed_output(converted_modules, conversion_output_dir):
    """Run ContentHasher on the converted output."""
    from hasher import ContentHasher
    hasher = ContentHasher(conversion_output_dir)
    version_hashes, book_hashes, chapter_hashes = hasher.hash_all()
    return {
        'version_hashes': version_hashes,
        'book_hashes': book_hashes,
        'chapter_hashes': chapter_hashes,
    }


# ── Parametrize helpers ─────────────────────────────────────────────────────


def _load_test_modules():
    """Load module map for parametrize (called at collection time)."""
    with open(_TEST_MODULES_MAP, 'r') as f:
        return json.load(f)


def _module_abbreviations():
    """Return abbreviation values for parametrize."""
    return list(_load_test_modules().values())


@pytest.fixture(params=_module_abbreviations())
def per_module(request, converted_modules):
    """Fixture that iterates over each converted module."""
    abbr = request.param
    if abbr not in converted_modules:
        pytest.skip(f"Module {abbr} was not converted")
    return converted_modules[abbr]


@pytest.fixture
def random_book(per_module, integration_rng):
    """Pick a random book from a converted module."""
    books = per_module['version_data']['books']
    assert len(books) > 0, f"No books in {per_module['abbreviation']}"
    return integration_rng.choice(books)


@pytest.fixture
def random_chapter(random_book, integration_rng):
    """Pick a random chapter from a random book."""
    chapters = random_book['chapters']
    assert len(chapters) > 0, f"No chapters in book {random_book['name']}"
    return integration_rng.choice(chapters)


@pytest.fixture
def random_verse(random_chapter, integration_rng):
    """Pick a random verse from a random chapter."""
    verses = random_chapter['verses']
    assert len(verses) > 0, f"No verses in chapter {random_chapter['name']}"
    return integration_rng.choice(verses)
