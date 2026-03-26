"""Integration tests for real SWORD module downloads from Crosswire."""

import os
import zipfile

import pytest

pytestmark = pytest.mark.integration


class TestRealDownload:
    """Verify that all test modules download successfully."""

    def test_all_test_modules_downloaded(self, downloaded_modules, test_module_map):
        """Every module in CrosswireModulesMapTest.json was downloaded."""
        assert len(downloaded_modules) == len(test_module_map)

    def test_downloaded_files_are_valid_zips(self, downloaded_modules):
        """Each downloaded file is a valid ZIP archive."""
        for path in downloaded_modules:
            assert os.path.isfile(path), f"Missing: {path}"
            assert zipfile.is_zipfile(path), f"Invalid zip: {path}"

    def test_downloaded_zips_contain_sword_structure(self, downloaded_modules):
        """Each ZIP should contain a mods.d/ directory (SWORD RAW format)."""
        for path in downloaded_modules:
            with zipfile.ZipFile(path, 'r') as zf:
                names = zf.namelist()
                has_modsd = any(n.startswith('mods.d/') for n in names)
                assert has_modsd, (
                    f"{os.path.basename(path)} missing mods.d/ directory. "
                    f"Top entries: {names[:10]}"
                )

    def test_download_caching_works(self, downloaded_modules, test_module_map, sword_cache_dir):
        """Calling download_modules again should return the same paths (cached)."""
        from download import download_modules
        paths = download_modules(test_module_map, sword_cache_dir)
        assert len(paths) == len(downloaded_modules)
        for p in paths:
            assert os.path.isfile(p)
