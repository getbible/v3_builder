"""Fixtures for real-module tests through the getBibleSWORD boundary."""

import json
import os
import random
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONF_DIR = os.path.join(_PROJECT_ROOT, "conf")
_TEST_MODULES_MAP = os.path.join(_CONF_DIR, "CrosswireModulesMapTest.json")


@pytest.fixture(scope="session")
def integration_rng(request):
    seed = request.config.getoption("--integration-seed")
    if seed is None:
        seed = random.randint(0, 2**31)
    print(f"\n  Integration test seed: {seed}")
    return random.Random(seed)


@pytest.fixture(scope="session")
def sword_cache_dir(request):
    cache_dir = request.config.getoption("--integration-cache-dir")
    cache_dir = cache_dir or os.path.join(_PROJECT_ROOT, ".sword_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


@pytest.fixture(scope="session")
def test_module_map():
    with open(_TEST_MODULES_MAP, "r", encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture(scope="session")
def getbiblesword_executable():
    configured = os.environ.get("GETBIBLESWORD_BIN", "getbiblesword")
    executable = configured if os.path.sep in configured else shutil.which(configured)
    if not executable or not os.path.isfile(executable):
        pytest.skip("GETBIBLESWORD_BIN is required for native integration tests")
    return executable


@pytest.fixture(scope="session")
def downloaded_modules(test_module_map, sword_cache_dir):
    from download import download_modules
    paths = download_modules(test_module_map, sword_cache_dir)
    assert len(paths) == len(test_module_map), "not all test modules downloaded"
    return paths


@pytest.fixture(scope="session")
def conversion_config():
    from converter import ConversionConfig
    return ConversionConfig.from_files(_CONF_DIR, _TEST_MODULES_MAP)


@pytest.fixture(scope="session")
def conversion_output_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("scripture_output"))


@pytest.fixture(scope="session")
def converted_modules(
    downloaded_modules,
    conversion_config,
    conversion_output_dir,
    getbiblesword_executable,
    test_module_map,
    tmp_path_factory,
):
    from getbiblesword_converter import GetBibleSwordConverter
    from getbiblesword_reader import GetBibleSwordReader, materialize_sword_root

    workspace = tmp_path_factory.mktemp("native_sword")
    sword_root = materialize_sword_root(downloaded_modules, str(workspace / "root"))
    contracts = workspace / "contracts"
    contracts.mkdir()
    reader = GetBibleSwordReader(getbiblesword_executable)
    converter = GetBibleSwordConverter(
        conversion_config, conversion_output_dir, conf_dir=_CONF_DIR
    )
    results = {}
    for sword_name, abbreviation in test_module_map.items():
        contract = contracts / f"{sword_name}.ndjson"
        summary = reader.extract(sword_name, str(sword_root), str(contract))
        assert summary.module_name == sword_name
        version_path = converter.convert(
            str(contract), module_name=sword_name, summary=summary
        )
        with open(version_path, "r", encoding="utf-8") as stream:
            version_data = json.load(stream)
        results[abbreviation] = {
            "sword_name": sword_name,
            "abbreviation": abbreviation,
            "version_path": version_path,
            "version_data": version_data,
            "output_dir": conversion_output_dir,
        }
    return results


@pytest.fixture(scope="session")
def hashed_output(converted_modules, conversion_output_dir):
    from hasher import ContentHasher
    version_hashes, book_hashes, chapter_hashes = ContentHasher(
        conversion_output_dir
    ).hash_all()
    return {
        "version_hashes": version_hashes,
        "book_hashes": book_hashes,
        "chapter_hashes": chapter_hashes,
    }


def _load_test_modules():
    with open(_TEST_MODULES_MAP, "r", encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture(params=list(_load_test_modules().values()))
def per_module(request, converted_modules):
    abbreviation = request.param
    if abbreviation not in converted_modules:
        pytest.skip(f"Module {abbreviation} was not converted")
    return converted_modules[abbreviation]


@pytest.fixture
def random_book(per_module, integration_rng):
    books = per_module["version_data"]["books"]
    assert books
    return integration_rng.choice(books)


@pytest.fixture
def random_chapter(random_book, integration_rng):
    chapters = random_book["chapters"]
    assert chapters
    return integration_rng.choice(chapters)


@pytest.fixture
def random_verse(random_chapter, integration_rng):
    verses = random_chapter["verses"]
    assert verses
    return integration_rng.choice(verses)
