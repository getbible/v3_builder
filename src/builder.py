#!/usr/bin/env python3
"""
getBible JSON API v3 Builder

Main entry point that orchestrates the entire build pipeline:
1. Download Crosswire SWORD modules
2. Convert SWORD modules to JSON (via SwordModuleConverter)
3. Clean empty/invalid files
4. Hash all files at version, book, and chapter levels
5. Copy public hash files to API repository
6. Optionally push to GitHub

Usage:
    python3 src/builder.py [options]

    python3 src/builder.py --test          # Test with 3 Bibles
    python3 src/builder.py --hash-only     # Only rehash existing files
    python3 src/builder.py --pull --push   # Full build with git sync
"""

import argparse
import glob
import json
import logging
import os
import sys
import time
from dataclasses import dataclass

# Ensure src/ is on the path when run directly
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from download import download_modules
from hasher import ContentHasher
from file_ops import clean_empty_files, move_public_hash_files
from git_ops import GitRepository, push_all_repos
from converter import ConversionConfig, SwordModuleConverter

log = logging.getLogger('builder')


@dataclass
class BuildConfig:
    """Typed configuration for the build pipeline.

    Replaces the untyped argparse.Namespace with explicit fields
    for IDE support, type safety, and clear documentation.
    """

    base_dir: str
    api_path: str
    zip_dir: str
    bible_conf: str
    config_file: str
    conf_dir: str
    repo_hash: str
    repo_scripture: str
    download: bool = True
    pull: bool = False
    push: bool = False
    hash_only: bool = False
    test: bool = False
    dry: bool = False
    set_active: bool = False
    github: bool = False
    verbose: bool = False

    @classmethod
    def from_args(cls, argv=None):
        """Parse command-line arguments into a BuildConfig.

        Handles argument defaults, test mode overrides, and config
        file loading in the correct priority order.
        """
        args = _parse_raw_args(argv)
        config = cls(
            base_dir=args.base_dir,
            api_path=args.api,
            zip_dir=args.zip_dir,
            bible_conf=args.bible_conf,
            config_file=args.config_file,
            conf_dir=os.path.join(args.base_dir, 'conf'),
            repo_hash=args.repo_hash,
            repo_scripture=args.repo_scripture,
            download=args.download,
            pull=args.pull,
            push=args.push,
            hash_only=args.hash_only,
            test=args.test,
            dry=args.dry,
            set_active=args.set_active,
            github=args.github,
            verbose=args.verbose,
        )
        return config


class BuildPipeline:
    """Orchestrates the full getBible build pipeline.

    Wires together all components (downloader, converter, hasher,
    file ops, git repos) and executes the build steps in sequence.

    Args:
        config: BuildConfig with all pipeline settings.
    """

    def __init__(self, config):
        self._config = config
        self._scripture_path = config.api_path + '_scripture'
        self._hash_path = config.api_path

        self._scripture_repo = GitRepository(
            self._scripture_path, config.repo_scripture
        )
        self._hash_repo = GitRepository(
            self._hash_path, config.repo_hash
        )

    def run(self):
        """Execute the full build pipeline."""
        start_time = time.time()

        if not self._config.hash_only:
            self._download()
            self._prepare_scripture_repo()
            self._convert_modules()
            self._clean()

        self._hash()
        self._prepare_hash_repo()
        self._copy_public_files()

        if self._config.push:
            self._push()

        elapsed = time.time() - start_time
        log.info('Build complete in %.1f seconds.', elapsed)

    def _download(self):
        if not self._config.download:
            return

        log.info('Downloading Crosswire modules...')
        if os.path.isdir(self._config.zip_dir):
            import shutil
            shutil.rmtree(self._config.zip_dir)
        os.makedirs(self._config.zip_dir, exist_ok=True)

        with open(self._config.bible_conf, 'r') as f:
            module_names = json.load(f)

        download_modules(module_names, self._config.zip_dir)
        log.info('Download complete.')

    def _prepare_scripture_repo(self):
        self._scripture_repo.prepare(pull=self._config.pull)

    def _convert_modules(self):
        zip_files = sorted(glob.glob(os.path.join(self._config.zip_dir, '*.zip')))
        if not zip_files:
            log.warning('No .zip files found in %s', self._config.zip_dir)
            return

        total = len(zip_files)
        log.info('Building JSON for %d modules...', total)

        conversion_config = ConversionConfig.from_files(
            self._config.conf_dir, self._config.bible_conf
        )
        converter = SwordModuleConverter(
            conversion_config,
            self._scripture_path,
            conf_dir=self._config.conf_dir,
        )

        for i, zip_file in enumerate(zip_files, 1):
            name = os.path.basename(zip_file)
            log.info('[%d/%d] Converting %s', i, total, name)
            try:
                converter.convert(zip_file)
            except Exception:
                log.exception('Failed to convert %s', name)

        log.info('JSON build complete.')

    def _clean(self):
        files_rm, dirs_rm = clean_empty_files(self._scripture_path)
        log.info('Cleaned %d files, %d dirs', files_rm, dirs_rm)

    def _hash(self):
        hasher = ContentHasher(self._scripture_path)
        hasher.hash_all()

    def _prepare_hash_repo(self):
        self._hash_repo.prepare(pull=self._config.pull)

    def _copy_public_files(self):
        copied = move_public_hash_files(self._scripture_path, self._hash_path)
        log.info('Copied %d public hash files', copied)

    def _push(self):
        push_all_repos(self._scripture_repo, self._hash_repo)


# ── CLI argument parsing ─────────────────────────────────────────────────────

def _parse_raw_args(argv=None):
    """Parse raw command-line arguments into an argparse Namespace."""
    parser = argparse.ArgumentParser(
        description='getBible JSON API v3 Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    default_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_conf = os.path.join(default_base, 'conf')

    parser.add_argument(
        '--api', default=os.path.join(default_base, 'v3'),
        help='API target folder path (default: %(default)s)')
    parser.add_argument(
        '--zip', dest='zip_dir', default=os.path.join(default_base, 'sword_zip'),
        help='SWORD module ZIP folder (default: %(default)s)')
    parser.add_argument(
        '--bconf', dest='bible_conf',
        default=os.path.join(default_conf, 'CrosswireModulesMap.json'),
        help='Bible modules config file (default: %(default)s)')
    parser.add_argument(
        '--conf', dest='config_file',
        default=os.path.join(default_conf, '.config'),
        help='Properties config file (default: %(default)s)')

    parser.add_argument('--pull', action='store_true',
                        help='Clone/pull target repositories')
    parser.add_argument('--push', action='store_true',
                        help='Push changes to GitHub')
    parser.add_argument('-d', '--no-download', dest='download',
                        action='store_false', default=True,
                        help='Skip downloading modules')
    parser.add_argument('--hash-only', action='store_true',
                        help='Only hash existing JSON files')
    parser.add_argument('--test', action='store_true',
                        help='Test mode with only 3 Bibles')
    parser.add_argument('--dry', action='store_true',
                        help='Show config and exit')
    parser.add_argument('--set-active', action='store_true',
                        help='Update .active file and push (repository keepalive)')

    parser.add_argument('--repo-hash', default='git@github.com:getbible/v3.git',
                        help='Hash repository URL')
    parser.add_argument('--repo-scripture', default='',
                        help='Scripture repository URL')

    parser.add_argument('--github', action='store_true',
                        help='GitHub Actions mode (quiet logging)')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose logging')

    args = parser.parse_args(argv)
    args.base_dir = default_base

    if args.test:
        args.bible_conf = os.path.join(default_conf, 'CrosswireModulesMapTest.json')
        args.api = os.path.join(default_base, 'v3t')
        args.zip_dir = os.path.join(default_base, 'sword_zipt')

    _apply_config_file(args)

    return args


def _apply_config_file(args):
    """Apply defaults from config file if it exists."""
    if not os.path.isfile(args.config_file):
        return

    config = {}
    with open(args.config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()

    mapping = {
        'getbible.api': 'api',
        'getbible.zip': 'zip_dir',
        'getbible.bconf': 'bible_conf',
        'getbible.repo-hash': 'repo_hash',
        'getbible.repo-scripture': 'repo_scripture',
    }
    for config_key, attr_name in mapping.items():
        if config_key in config and config[config_key]:
            setattr(args, attr_name, config[config_key])

    bool_mapping = {
        'getbible.download': 'download',
        'getbible.pull': 'pull',
        'getbible.push': 'push',
        'getbible.hashonly': 'hash_only',
    }
    for config_key, attr_name in bool_mapping.items():
        if config_key in config:
            setattr(args, attr_name, config[config_key] == '1')


# ── Backward-compatible functions for existing tests ─────────────────────────

def parse_args(argv=None):
    """Parse command-line arguments. Returns an object with all config attributes."""
    return _parse_raw_args(argv)


def run_build(args):
    """Execute the full build pipeline from an argparse namespace."""
    config = BuildConfig(
        base_dir=args.base_dir,
        api_path=args.api,
        zip_dir=args.zip_dir,
        bible_conf=args.bible_conf,
        config_file=getattr(args, 'config_file', ''),
        conf_dir=os.path.join(args.base_dir, 'conf'),
        repo_hash=args.repo_hash,
        repo_scripture=args.repo_scripture,
        download=args.download,
        pull=args.pull,
        push=args.push,
        hash_only=args.hash_only,
        test=args.test,
    )
    pipeline = BuildPipeline(config)
    pipeline.run()


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )

    if args.dry:
        print('getBible JSON API v3 Builder')
        print('=' * 50)
        print(f'  api:            {args.api}')
        print(f'  zip_dir:        {args.zip_dir}')
        print(f'  bible_conf:     {args.bible_conf}')
        print(f'  config_file:    {args.config_file}')
        print(f'  download:       {args.download}')
        print(f'  hash_only:      {args.hash_only}')
        print(f'  pull:           {args.pull}')
        print(f'  push:           {args.push}')
        print(f'  test:           {args.test}')
        print(f'  repo_hash:      {args.repo_hash}')
        print(f'  repo_scripture: {args.repo_scripture}')
        print('=' * 50)
        return 0

    if args.set_active:
        from git_ops import set_active
        set_active(args.base_dir)
        return 0

    try:
        run_build(args)
        return 0
    except Exception:
        log.exception('Build failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
