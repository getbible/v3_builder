"""Tests for builder.py — main orchestrator argument parsing and config."""

import os
import pytest

from builder import parse_args, _apply_config_file


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.download is True
        assert args.push is False
        assert args.pull is False
        assert args.hash_only is False
        assert args.test is False
        assert args.dry is False
        assert args.verbose is False

    def test_pull_push(self):
        args = parse_args(['--pull', '--push'])
        assert args.pull is True
        assert args.push is True

    def test_no_download(self):
        args = parse_args(['-d'])
        assert args.download is False

    def test_hash_only(self):
        args = parse_args(['--hash-only'])
        assert args.hash_only is True

    def test_test_mode_overrides(self):
        args = parse_args(['--test'])
        assert args.test is True
        assert 'CrosswireModulesMapTest.json' in args.bible_conf
        assert 'v3t' in args.api
        assert 'sword_zipt' in args.zip_dir

    def test_custom_api_path(self):
        args = parse_args(['--api', '/custom/path'])
        assert args.api == '/custom/path'

    def test_custom_zip(self):
        args = parse_args(['--zip', '/my/zips'])
        assert args.zip_dir == '/my/zips'

    def test_custom_bconf(self):
        args = parse_args(['--bconf', '/my/conf.json'])
        assert args.bible_conf == '/my/conf.json'

    def test_repo_urls(self):
        args = parse_args([
            '--repo-hash', 'git@example.com:hash.git',
            '--repo-scripture', 'git@example.com:scripture.git'
        ])
        assert args.repo_hash == 'git@example.com:hash.git'
        assert args.repo_scripture == 'git@example.com:scripture.git'

    def test_verbose(self):
        args = parse_args(['-v'])
        assert args.verbose is True

    def test_github_mode(self):
        args = parse_args(['--github'])
        assert args.github is True

    def test_dry_run(self):
        args = parse_args(['--dry'])
        assert args.dry is True


class TestConfigFile:
    def test_loads_config(self, tmp_path):
        config = tmp_path / '.config'
        config.write_text(
            'getbible.api=/custom/api\n'
            'getbible.zip=/custom/zip\n'
            'getbible.download=0\n'
            'getbible.push=1\n'
        )
        args = parse_args(['--conf', str(config)])
        assert args.api == '/custom/api'
        assert args.zip_dir == '/custom/zip'
        assert args.download is False
        assert args.push is True

    def test_missing_config_ignored(self, tmp_path):
        args = parse_args(['--conf', str(tmp_path / 'nonexistent')])
        # Should not crash, defaults still apply
        assert args.download is True

    def test_config_with_comments(self, tmp_path):
        config = tmp_path / '.config'
        config.write_text(
            '# This is a comment\n'
            'getbible.api=/custom/api\n'
            '# Another comment\n'
        )
        args = parse_args(['--conf', str(config)])
        assert args.api == '/custom/api'
