"""Tests for builder.py — main orchestrator argument parsing and config."""

import os
import pytest

from builder import BuildConfig, BuildPipeline, parse_args
from git_ops import GitPushError
from publication_safety import PublicationSafetyError


def _config(tmp_path, **overrides):
    values = {
        'base_dir': str(tmp_path),
        'api_path': str(tmp_path / 'v3'),
        'zip_dir': str(tmp_path / 'zips'),
        'bible_conf': str(tmp_path / 'modules.json'),
        'config_file': str(tmp_path / '.config'),
        'conf_dir': str(tmp_path / 'conf'),
        'repo_hash': 'hash.git',
        'repo_scripture': 'scripture.git',
        'download': False,
        'contracts_dir': str(tmp_path / 'contracts'),
        'sword_root': str(tmp_path / 'sword-root'),
        'publication_policy': str(tmp_path / 'policy.json'),
    }
    values.update(overrides)
    return BuildConfig(**values)


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

class TestBuildPublicationSafety:
    def test_file_gate_runs_before_hashing(self, tmp_path, monkeypatch):
        config = _config(tmp_path, hash_only=True)
        scripture = tmp_path / 'v3_scripture'
        scripture.mkdir()
        (scripture / 'kjv.json').write_bytes(b'x' * 10)
        pipeline = BuildPipeline(config)
        pipeline._scripture_repo._max_file_bytes = 10
        monkeypatch.setattr(
            pipeline,
            '_hash',
            lambda: pytest.fail('hashing must not start after a size-gate failure'),
        )

        with pytest.raises(PublicationSafetyError, match='hard ceiling'):
            pipeline.run()

    def test_push_failure_propagates_out_of_pipeline(self, tmp_path, monkeypatch):
        config = _config(tmp_path, hash_only=True, push=True)
        scripture = tmp_path / 'v3_scripture'
        scripture.mkdir()
        pipeline = BuildPipeline(config)
        monkeypatch.setattr(pipeline._scripture_repo, 'validate_output', lambda: None)
        monkeypatch.setattr(pipeline, '_hash', lambda: None)
        monkeypatch.setattr(pipeline, '_prepare_hash_repo', lambda: None)
        monkeypatch.setattr(pipeline, '_copy_public_files', lambda: None)
        error = GitPushError('/scripture', 'GH001', attempts=1, permanent=True)
        monkeypatch.setattr('builder.push_all_repos', lambda *args: (_ for _ in ()).throw(error))

        with pytest.raises(GitPushError):
            pipeline.run()


class TestTransientInputCleanup:
    @staticmethod
    def _create_inputs(config):
        for path in (config.zip_dir, config.sword_root, config.contracts_dir):
            os.makedirs(path)
            with open(os.path.join(path, 'transient'), 'w', encoding='utf-8') as stream:
                stream.write('discard me')

    def test_discards_all_transient_inputs_after_extraction_failure(
        self, tmp_path, monkeypatch,
    ):
        config = _config(tmp_path)
        self._create_inputs(config)
        pipeline = BuildPipeline(config)
        monkeypatch.setattr(pipeline, '_authorized_modules', lambda: ['KJV'])
        monkeypatch.setattr(pipeline, '_download', lambda modules: None)
        monkeypatch.setattr(pipeline, '_prepare_scripture_repo', lambda: None)
        monkeypatch.setattr(
            pipeline,
            '_extract_contracts',
            lambda modules: (_ for _ in ()).throw(RuntimeError('extract failed')),
        )

        with pytest.raises(RuntimeError, match='extract failed'):
            pipeline.run()

        assert not os.path.exists(config.zip_dir)
        assert not os.path.exists(config.sword_root)
        assert not os.path.exists(config.contracts_dir)

    @pytest.mark.parametrize('unsafe_kind', ['repository-parent', 'inside-scripture'])
    def test_refuses_transient_path_overlapping_repository_or_output(
        self, tmp_path, unsafe_kind,
    ):
        config = _config(tmp_path)
        if unsafe_kind == 'repository-parent':
            config.zip_dir = str(tmp_path.parent)
        else:
            config.zip_dir = str(tmp_path / 'v3_scripture' / 'zips')
        pipeline = BuildPipeline(config)

        with pytest.raises(RuntimeError, match='unsafe transient'):
            pipeline._cleanup_transient_inputs()

    def test_refuses_symlinked_transient_path(self, tmp_path):
        config = _config(tmp_path)
        target = tmp_path / 'real-zips'
        target.mkdir()
        linked = tmp_path / 'linked-zips'
        linked.symlink_to(target, target_is_directory=True)
        config.zip_dir = str(linked)
        pipeline = BuildPipeline(config)

        with pytest.raises(RuntimeError, match='symlinked transient'):
            pipeline._cleanup_transient_inputs()

        assert target.exists()
