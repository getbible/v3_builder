#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""getBible JSON API v3 builder using the official SWORD engine boundary."""

import argparse
import glob
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass

_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from converter import ConversionConfig
from contract_archive import write_contract_manifest
from download import download_modules
from file_ops import clean_empty_files, move_public_hash_files
from getbiblesword_converter import GetBibleSwordConverter
from getbiblesword_reader import GetBibleSwordReader, materialize_sword_root
from git_ops import GitRepository, push_all_repos
from hasher import ContentHasher
from publication_policy import PublicationPolicy


log = logging.getLogger("builder")


@dataclass
class BuildConfig:
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
    contracts_dir: str = ""
    sword_root: str = ""
    getbiblesword: str = "getbiblesword"
    publication_policy: str = ""

    @classmethod
    def from_args(cls, argv=None):
        args = _parse_raw_args(argv)
        return cls(
            base_dir=args.base_dir,
            api_path=args.api,
            zip_dir=args.zip_dir,
            bible_conf=args.bible_conf,
            config_file=args.config_file,
            conf_dir=os.path.join(args.base_dir, "conf"),
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
            contracts_dir=args.contracts_dir,
            sword_root=args.sword_root,
            getbiblesword=args.getbiblesword,
            publication_policy=args.publication_policy,
        )


class BuildPipeline:
    """Fail-closed build pipeline rooted in validated getBibleSWORD contracts."""

    def __init__(self, config):
        self._config = config
        self._scripture_path = config.api_path + "_scripture"
        self._hash_path = config.api_path
        self._scripture_repo = GitRepository(self._scripture_path, config.repo_scripture)
        self._hash_repo = GitRepository(self._hash_path, config.repo_hash)

    def run(self):
        start_time = time.time()
        if not self._config.hash_only:
            module_names = self._authorized_modules()
            self._download(module_names)
            self._prepare_scripture_repo()
            contracts = self._extract_contracts(module_names)
            self._convert_contracts(contracts)
            self._clean()
        self._hash()
        self._prepare_hash_repo()
        self._copy_public_files()
        if self._config.push:
            self._push()
        log.info("Build complete in %.1f seconds.", time.time() - start_time)

    def _authorized_modules(self):
        with open(self._config.bible_conf, "r", encoding="utf-8") as stream:
            module_map = json.load(stream)
        policy = PublicationPolicy.from_file(self._config.publication_policy)
        policy.require_approved(module_map)
        return list(module_map)

    def _download(self, module_names):
        if not self._config.download:
            return
        log.info("Downloading %d publication-approved Crosswire modules...", len(module_names))
        if os.path.isdir(self._config.zip_dir):
            shutil.rmtree(self._config.zip_dir)
        os.makedirs(self._config.zip_dir, exist_ok=True)
        download_modules(module_names, self._config.zip_dir)

    def _prepare_scripture_repo(self):
        self._scripture_repo.prepare(pull=self._config.pull)

    def _extract_contracts(self, module_names):
        archive_by_module = {
            os.path.basename(path)[:-4]: path
            for path in glob.glob(os.path.join(self._config.zip_dir, "*.zip"))
        }
        missing = sorted(set(module_names) - archive_by_module.keys())
        if missing:
            raise RuntimeError("downloaded module set is incomplete: " + ", ".join(missing))

        if os.path.isdir(self._config.sword_root):
            shutil.rmtree(self._config.sword_root)
        if os.path.isdir(self._config.contracts_dir):
            shutil.rmtree(self._config.contracts_dir)
        os.makedirs(self._config.contracts_dir, exist_ok=True)
        archives = [archive_by_module[module] for module in module_names]
        materialize_sword_root(archives, self._config.sword_root)

        reader = GetBibleSwordReader(self._config.getbiblesword)
        contracts = []
        summaries = []
        for index, module_name in enumerate(module_names, 1):
            output = os.path.join(self._config.contracts_dir, f"{module_name}.ndjson")
            log.info("[%d/%d] Extracting %s", index, len(module_names), module_name)
            summary = reader.extract(module_name, self._config.sword_root, output)
            log.info(
                "Validated %s: %d entries, %d artifacts, %s",
                module_name,
                summary.entries,
                summary.artifacts,
                summary.stream_sha256,
            )
            contracts.append((module_name, output))
            summaries.append(summary)
        manifest = write_contract_manifest(self._config.contracts_dir, summaries)
        log.info("Wrote validated contract archive manifest: %s", manifest)
        return contracts

    def _convert_contracts(self, contracts):
        conversion_config = ConversionConfig.from_files(
            self._config.conf_dir, self._config.bible_conf
        )
        converter = GetBibleSwordConverter(
            conversion_config, self._scripture_path, conf_dir=self._config.conf_dir
        )
        for index, (module_name, contract) in enumerate(contracts, 1):
            log.info("[%d/%d] Rendering %s", index, len(contracts), module_name)
            converter.convert(contract, module_name=module_name)
        log.info("Native JSON build complete.")

    def _clean(self):
        files_removed, directories_removed = clean_empty_files(self._scripture_path)
        log.info("Cleaned %d files, %d dirs", files_removed, directories_removed)

    def _hash(self):
        ContentHasher(self._scripture_path).hash_all()

    def _prepare_hash_repo(self):
        self._hash_repo.prepare(pull=self._config.pull)

    def _copy_public_files(self):
        copied = move_public_hash_files(self._scripture_path, self._hash_path)
        log.info("Copied %d public hash files", copied)

    def _push(self):
        push_all_repos(self._scripture_repo, self._hash_repo)


def _parse_raw_args(argv=None):
    parser = argparse.ArgumentParser(
        description="getBible JSON API v3 Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_conf = os.path.join(default_base, "conf")
    parser.add_argument("--api", default=os.path.join(default_base, "v3"))
    parser.add_argument("--zip", dest="zip_dir", default=os.path.join(default_base, "sword_zip"))
    parser.add_argument(
        "--bconf", dest="bible_conf",
        default=os.path.join(default_conf, "CrosswireModulesMap.json"),
    )
    parser.add_argument("--conf", dest="config_file", default=os.path.join(default_conf, ".config"))
    parser.add_argument(
        "--contracts", dest="contracts_dir",
        default=os.path.join(default_base, "sword_contracts"),
        help="validated NDJSON contract working directory",
    )
    parser.add_argument(
        "--sword-root", default=os.path.join(default_base, "sword_root"),
        help="materialized SWORD installation working directory",
    )
    parser.add_argument(
        "--getbiblesword", default=os.environ.get("GETBIBLESWORD_BIN", "getbiblesword"),
        help="path or command name for the getBibleSWORD executable",
    )
    parser.add_argument(
        "--publication-policy",
        default=os.path.join(default_conf, "PublicationPolicy.json"),
        help="default-deny module publication approval manifest",
    )
    parser.add_argument("--pull", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("-d", "--no-download", dest="download", action="store_false", default=True)
    parser.add_argument("--hash-only", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--set-active", action="store_true")
    parser.add_argument("--repo-hash", default="git@github.com:getbible/v3.git")
    parser.add_argument("--repo-scripture", default="")
    parser.add_argument("--github", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    args.base_dir = default_base
    if args.test:
        args.bible_conf = os.path.join(default_conf, "CrosswireModulesMapTest.json")
        args.api = os.path.join(default_base, "v3t")
        args.zip_dir = os.path.join(default_base, "sword_zipt")
        args.contracts_dir = os.path.join(default_base, "sword_contractst")
        args.sword_root = os.path.join(default_base, "sword_roott")
    _apply_config_file(args)
    return args


def _apply_config_file(args):
    if not os.path.isfile(args.config_file):
        return
    config = {}
    with open(args.config_file, "r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    mapping = {
        "getbible.api": "api",
        "getbible.zip": "zip_dir",
        "getbible.bconf": "bible_conf",
        "getbible.repo-hash": "repo_hash",
        "getbible.repo-scripture": "repo_scripture",
        "getbible.contracts": "contracts_dir",
        "getbible.sword-root": "sword_root",
        "getbible.getbiblesword": "getbiblesword",
        "getbible.publication-policy": "publication_policy",
    }
    for config_key, attribute in mapping.items():
        if config.get(config_key):
            setattr(args, attribute, config[config_key])
    for config_key, attribute in {
        "getbible.download": "download",
        "getbible.pull": "pull",
        "getbible.push": "push",
        "getbible.hashonly": "hash_only",
    }.items():
        if config_key in config:
            setattr(args, attribute, config[config_key] == "1")


def parse_args(argv=None):
    return _parse_raw_args(argv)


def run_build(args):
    config = BuildConfig.from_args([])
    config.base_dir = args.base_dir
    config.api_path = args.api
    config.zip_dir = args.zip_dir
    config.bible_conf = args.bible_conf
    config.config_file = getattr(args, "config_file", "")
    config.conf_dir = os.path.join(args.base_dir, "conf")
    config.repo_hash = args.repo_hash
    config.repo_scripture = args.repo_scripture
    for name in (
        "download", "pull", "push", "hash_only", "test", "dry", "set_active",
        "github", "verbose", "contracts_dir", "sword_root", "getbiblesword",
        "publication_policy",
    ):
        if hasattr(args, name):
            setattr(config, name, getattr(args, name))
    BuildPipeline(config).run()


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.dry:
        for name in (
            "api", "zip_dir", "bible_conf", "contracts_dir", "sword_root",
            "getbiblesword", "publication_policy", "download", "hash_only", "pull",
            "push", "test", "repo_hash", "repo_scripture",
        ):
            print(f"{name}: {getattr(args, name)}")
        return 0
    if args.set_active:
        from git_ops import set_active
        set_active(args.base_dir)
        return 0
    try:
        run_build(args)
        return 0
    except Exception:
        log.exception("Build failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
