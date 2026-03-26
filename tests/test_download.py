"""Tests for download.py — SWORD module downloader."""

import os
import zipfile
from unittest import mock

import pytest

from download import (
    download_modules,
    parse_args,
    _download_raw,
    _create_zip,
)


class TestParseArgs:
    def test_required_args(self):
        args = parse_args([
            '--output_path', '/path/to/output',
            '--bible_conf', '/path/to/conf.json',
        ])
        assert args.output_path == '/path/to/output'
        assert args.bible_conf == '/path/to/conf.json'
        assert args.verbose is False

    def test_verbose(self):
        args = parse_args([
            '--output_path', '/out',
            '--bible_conf', '/conf.json',
            '-v',
        ])
        assert args.verbose is True

    def test_missing_required_exits(self):
        with pytest.raises(SystemExit):
            parse_args([])


class TestCreateZip:
    def test_creates_valid_zip(self, tmp_path):
        src = tmp_path / 'source'
        src.mkdir()
        (src / 'file.txt').write_text('hello')
        (src / 'sub').mkdir()
        (src / 'sub' / 'nested.txt').write_text('world')

        zip_path = str(tmp_path / 'output.zip')
        _create_zip(str(src), zip_path)

        assert os.path.exists(zip_path)
        assert zipfile.is_zipfile(zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            assert 'file.txt' in names
            assert os.path.join('sub', 'nested.txt') in names


class TestDownloadModules:
    def test_skips_existing_valid_zips(self, tmp_path):
        """Already-downloaded valid zips should be skipped."""
        output = str(tmp_path)
        # Create a valid zip
        zip_path = tmp_path / 'TestMod.zip'
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr('mods.d/testmod.conf', '[TestMod]')

        result = download_modules({'TestMod': 'testmod'}, output)
        assert str(zip_path) in result

    def test_returns_empty_on_all_failures(self, tmp_path):
        """If all downloads fail, returns empty list."""
        import urllib.error
        err = urllib.error.HTTPError(
            'http://example.com', 404, 'Not Found', {}, None
        )
        with mock.patch('download.urllib.request.urlretrieve', side_effect=err):
            result = download_modules({'BadMod': 'badmod'}, str(tmp_path))
        assert result == []

    def test_creates_output_dir(self, tmp_path):
        output = str(tmp_path / 'new_dir')
        with mock.patch('download.urllib.request.urlretrieve',
                        side_effect=Exception('network error')):
            download_modules({}, output)
        assert os.path.isdir(output)

    def test_successful_raw_download(self, tmp_path):
        """Simulate a successful RAW format download."""
        output = str(tmp_path)

        def fake_retrieve(url, path):
            # Create a valid zip
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('mods.d/testmod.conf', '[TestMod]')

        with mock.patch('download.urllib.request.urlretrieve', side_effect=fake_retrieve):
            result = download_modules({'TestMod': 'testmod'}, output)

        assert len(result) == 1
        assert 'TestMod.zip' in result[0]
