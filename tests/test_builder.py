"""Tests for builder.py — main orchestrator argument parsing and config."""

import hashlib
import json
import os
import pytest

from builder import BuildConfig, BuildPipeline, parse_args
from hasher import DEFAULT_API_BASE_URL, RESERVED_ROOT_NAMES
from git_ops import GitPushError
from publication_safety import PublicationSafetyError


SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'schema')


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
        'schema_dir': SCHEMA_DIR,
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

    def test_api_base_url_default_and_override(self):
        assert parse_args([]).api_base_url == DEFAULT_API_BASE_URL
        args = parse_args(['--api-base-url', 'https://example.test/v1'])
        assert args.api_base_url == 'https://example.test/v1'

    def test_config_carries_the_schema_directory(self):
        config = BuildConfig.from_args([])
        assert config.schema_dir == os.path.join(config.base_dir, 'schema')
        assert os.path.isdir(config.schema_dir)
        assert config.api_base_url == DEFAULT_API_BASE_URL

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

    def test_config_sets_api_base_url(self, tmp_path):
        config = tmp_path / '.config'
        config.write_text('getbible.api-base-url=https://example.test/v1\n')
        args = parse_args(['--conf', str(config)])
        assert args.api_base_url == 'https://example.test/v1'

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
        monkeypatch.setattr(pipeline, '_describe', lambda: None)
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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n', encoding='utf-8')


def _minimal_tree(root):
    meta = {
        'translation': 'King James Version', 'abbreviation': 'kjv', 'lang': 'en',
        'language': 'English', 'direction': 'LTR', 'encoding': 'UTF-8',
    }
    verses = [{'chapter': 1, 'verse': 1, 'name': 'Genesis 1:1', 'text': 'In the beginning.'}]
    chapters = [{'chapter': 1, 'name': 'Genesis 1', 'verses': verses}]
    _write_json(root / 'kjv.json', {
        **meta, 'description': 'KJV', 'books': [{'nr': 1, 'name': 'Genesis', 'chapters': chapters}],
        'distribution_lcsh': '', 'distribution_version': '', 'distribution_version_date': '',
        'distribution_abbreviation': 'KJV', 'distribution_about': '', 'distribution_license': '',
        'distribution_sourcetype': '', 'distribution_source': '', 'distribution_versification': '',
        'distribution_history': {},
    })
    _write_json(root / 'kjv' / '1.json', {**meta, 'nr': 1, 'name': 'Genesis', 'chapters': chapters})
    _write_json(root / 'kjv' / '1' / '1.json', {
        **meta, 'book_nr': 1, 'book_name': 'Genesis', 'chapter': 1, 'name': 'Genesis 1',
        'verses': verses,
    })


class TestTreeDescription:
    def test_hash_only_build_describes_the_tree_and_publishes_the_description(self, tmp_path):
        config = _config(tmp_path, hash_only=True)
        scripture = tmp_path / 'v3_scripture'
        _minimal_tree(scripture)

        BuildPipeline(config).run()

        document = json.loads((scripture / 'openapi.json').read_text(encoding='utf-8'))
        assert document['openapi'] == '3.1.0'
        assert all(path.startswith('/v3/') for path in document['paths'])
        assert document['components']['parameters']['translation']['schema']['enum'] == ['kjv']
        digest = hashlib.sha1((scripture / 'openapi.json').read_bytes()).hexdigest()
        assert (scripture / 'openapi.sha').read_text(encoding='utf-8') == digest + '\n'
        # The description is not a translation to the hasher.
        index = json.loads((scripture / 'translations.json').read_text(encoding='utf-8'))
        assert list(index) == ['kjv']
        assert index['kjv']['url'] == 'https://api.getbible.net/v3/kjv.json'
        # It travels to the public hash tree with its checksum, like every index.
        public = tmp_path / 'v3'
        assert (public / 'openapi.json').read_bytes() == (scripture / 'openapi.json').read_bytes()
        assert (public / 'openapi.sha').read_text(encoding='utf-8') == digest + '\n'
        assert not (public / 'kjv.json').exists()
        # Every JSON document in both trees has a matching .sha sibling.
        for tree in (scripture, public):
            documents = sorted(tree.rglob('*.json'))
            assert documents
            for document in documents:
                assert document.with_suffix('.sha').read_text(encoding='utf-8') == (
                    hashlib.sha1(document.read_bytes()).hexdigest() + '\n'
                )
        assert (public / 'translations.sha').exists()
        assert (public / 'kjv' / 'books.sha').exists()
        assert (public / 'kjv' / '1' / 'chapters.sha').exists()

    def test_the_base_url_moves_the_mount_and_the_index_urls_together(self, tmp_path):
        config = _config(tmp_path, hash_only=True, api_base_url='https://example.test/v1')
        scripture = tmp_path / 'v3_scripture'
        _minimal_tree(scripture)

        BuildPipeline(config).run()

        document = json.loads((scripture / 'openapi.json').read_text(encoding='utf-8'))
        assert all(path.startswith('/v1/') for path in document['paths'])
        assert document['info']['version'] == '1'
        index = json.loads((scripture / 'translations.json').read_text(encoding='utf-8'))
        assert index['kjv']['url'] == 'https://example.test/v1/kjv.json'
        assert 'example.test' not in (scripture / 'openapi.json').read_text(encoding='utf-8')

    @pytest.mark.parametrize('base_url', [
        'https://example.test/', 'https://example.test/bible/v1', 'example.test/v3',
    ])
    def test_an_unusable_base_url_fails_before_any_module_is_downloaded(
        self, tmp_path, monkeypatch, base_url,
    ):
        config = _config(tmp_path, api_base_url=base_url)
        pipeline = BuildPipeline(config)
        for step in ('_authorized_modules', '_download', '_prepare_scripture_repo', '_hash'):
            monkeypatch.setattr(
                pipeline, step,
                lambda *args, step=step: pytest.fail(f'{step} ran with an unusable base URL'),
            )
        monkeypatch.setattr(
            pipeline._scripture_repo, 'validate_output',
            lambda: pytest.fail('validation ran with an unusable base URL'),
        )

        with pytest.raises(ValueError, match='exactly one version segment'):
            pipeline.run()

        assert not (tmp_path / 'v3_scripture').exists()

    def test_a_trailing_slash_does_not_split_the_index_urls_from_the_mount(self, tmp_path):
        config = _config(tmp_path, hash_only=True, api_base_url='https://example.test/v1/')
        scripture = tmp_path / 'v3_scripture'
        _minimal_tree(scripture)

        BuildPipeline(config).run()

        index = json.loads((scripture / 'translations.json').read_text(encoding='utf-8'))
        assert index['kjv']['url'] == 'https://example.test/v1/kjv.json'
        books = json.loads((scripture / 'kjv' / 'books.json').read_text(encoding='utf-8'))
        assert books['1']['url'] == 'https://example.test/v1/1.json'.replace('/1.json', '/kjv/1.json')
        document = json.loads((scripture / 'openapi.json').read_text(encoding='utf-8'))
        assert list(document['paths'])[0] == '/v1/translations.json'

    def test_a_reserved_root_name_cannot_be_an_abbreviation(self, tmp_path):
        config = _config(tmp_path)
        _write_json(tmp_path / 'modules.json', {'KJV': 'kjv', 'WEB': 'openapi'})
        _write_json(tmp_path / 'policy.json', {
            'schema_version': 1, 'default': 'deny', 'approved_modules': ['KJV', 'WEB'],
        })
        pipeline = BuildPipeline(config)

        with pytest.raises(RuntimeError, match='reserved root document name.*openapi'):
            pipeline._authorized_modules()

        _write_json(tmp_path / 'modules.json', {'KJV': 'kjv', 'WEB': 'web'})
        assert pipeline._authorized_modules() == ['KJV', 'WEB']
        assert 'openapi' in RESERVED_ROOT_NAMES
