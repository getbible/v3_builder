# getBible API Builder (v3)

[![Build](https://github.com/getbible/v3_builder/actions/workflows/build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/build.yml)
[![Tests](https://github.com/getbible/v3_builder/actions/workflows/ci.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/ci.yml)

A Python build pipeline that converts [Crosswire SWORD](https://www.crosswire.org/) Bible modules into a static JSON API. Version 3 introduces a **token+span model** for word-level annotations, a fully Python-based pipeline (no shell scripts), and a SOLID class-based architecture.

## What It Does

1. **Downloads** SWORD Bible module `.zip` files from the Crosswire mirror
2. **Converts** each module to structured JSON at three levels (translation, book, chapter)
3. **Extracts** word-level data from OSIS modules into a token+span annotation model
4. **Hashes** all output with SHA1 checksums for change detection
5. **Publishes** hash/checksum files to a public API repository

## Key Changes in v3

- **Token+span model**: OSIS modules with `<w>` word-level markup now produce `tokens` and `spans` arrays using a [standoff annotation](https://en.wikipedia.org/wiki/Standoff_annotation) pattern instead of duplicating spanning element attributes onto every word
- **Pure Python pipeline**: All shell scripts (`run.sh`, `hash_*.sh`, `movePublicHashFiles.sh`, `moveToGithub.sh`, `active.sh`) replaced with Python modules
- **Class-based architecture**: `SwordModuleConverter`, `ContentHasher`, `GitRepository`, `BuildPipeline` with dependency injection
- **Comprehensive test suite**: 200+ pytest tests including API format contract tests

## Guidelines

1. Run the build periodically to sync with the Crosswire Modules.
2. Do not remove the hash methods. They identify changes between builds.
3. Do not host the scripture JSON repository publicly unless it is private, to prevent discrepancies with the Crosswire Modules.
4. If you make the scripture JSON API public, please let us know by [posting the details in an issue](https://git.vdm.dev/getBible/support).
5. If you cannot follow the above requests, please do not distribute any of the JSON or HASH files produced by the project.

If you don't wish to run your own API, you can use the official endpoint directly: https://api.getbible.net/v3/translations.json

Official API documentation: https://getbible.net/docs

## Requirements

- Python 3.12+
- [pysword](https://gitlab.com/tgc-dk/pysword) (for reading SWORD modules at build time)
- [requests](https://docs.python-requests.org/) (for book name resolution at build time)
- [pytest](https://docs.pytest.org/) (for running tests only)

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/getbible/v3_builder.git
cd v3_builder/
```

### 2. Create a Virtual Environment

On Ubuntu/Debian (24.04+), system Python is externally managed, so you must use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

For **running the build pipeline** (downloading and converting SWORD modules):

```bash
pip install -r requirements.txt
```

For **running tests only** (no external dependencies needed beyond pytest):

```bash
pip install -r requirements-dev.txt
```

For **both** (full development setup):

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 4. Run Tests

```bash
python -m pytest tests/ -v
```

### 5. Run a Build

```bash
# Full build (download, convert, hash)
python src/builder.py

# Test mode (6 Bibles only, for quick validation)
python src/builder.py --test

# Full build with git sync
python src/builder.py --pull --push \
  --repo-hash="git@github.com:getbible/v3.git" \
  --repo-scripture="git@github.com:getbible/v3_scripture.git"

# Only re-hash existing JSON files
python src/builder.py --hash-only

# Skip downloading (use existing modules)
python src/builder.py -d

# Show configuration and exit
python src/builder.py --dry

# Verbose logging
python src/builder.py -v
```

> **Note:** When the virtual environment is activated, use `python` (not `python3`). The venv ensures the correct interpreter is used.

### CLI Options

| Option | Description |
|--------|-------------|
| `--api=<path>` | API target folder path (default: `./v3`) |
| `--zip=<path>` | SWORD module ZIP folder (default: `./sword_zip`) |
| `--bconf=<path>` | Bible modules config file (default: `conf/CrosswireModulesMap.json`) |
| `--conf=<path>` | Properties config file (default: `conf/.config`) |
| `--pull` | Clone/pull target repositories before building |
| `--push` | Push changes to GitHub after building |
| `-d` | Skip downloading modules (use existing ZIPs) |
| `--hash-only` | Only hash existing JSON files (skip download + convert) |
| `--test` | Test mode with only 6 Bibles |
| `--dry` | Show configuration and exit without building |
| `--set-active` | Update `.active` file and push (repository keepalive) |
| `--repo-hash=<url>` | Hash repository URL |
| `--repo-scripture=<url>` | Scripture repository URL |
| `-v, --verbose` | Enable debug logging |

### Configuration File

You can set defaults in `conf/.config`:

```properties
getbible.api=/home/bible/v3
getbible.zip=/home/bible/sword_zip
getbible.bconf=/home/bible/conf/CrosswireModulesMap.json
getbible.repo-hash=git@github.com:getbible/v3.git
getbible.repo-scripture=git@github.com:getbible/v3_scripture.git
getbible.pull=1
getbible.push=1
```

### Automation (Cron)

```bash
crontab -e
# Add: run monthly on the 1st at 04:12
12 4 1 * * cd /home/username/v3_builder && python3 src/builder.py --pull --push >> builder.log 2>&1
```

## Running Tests

### Unit Tests (fast, no external dependencies)

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

The unit test suite does **not** require `pysword` or `requests` to be installed -- all external dependencies are lazy-imported and mocked in tests. Only `pytest` is needed. Tests cover:

- **API format contracts** (`test_api_format.py`): Validates JSON output schema at all three levels and the token+span model
- **OSIS parser** (`test_osis_parser.py`): Token extraction, span types, nesting, edge cases
- **Converter** (`test_converter.py`): Module conversion, config loading, word data detection
- **Hasher** (`test_hasher.py`): SHA1 checksums, metadata files, idempotency
- **File operations** (`test_file_ops.py`): Cleaning, public file copying
- **Git operations** (`test_git_ops.py`): Repository prep, push, keepalive
- **Download** (`test_download.py`): Module downloading, ZIP validation
- **Builder** (`test_builder.py`): Argument parsing, config file loading

### Integration Tests (downloads real SWORD modules)

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests_integration/ -v --run-integration
```

Integration tests download real SWORD modules from Crosswire (based on `conf/CrosswireModulesMapTest.json`), convert them, hash the output, and validate the results. They randomly sample different books, chapters, and verses each run for broad coverage.

```bash
# Reproduce a specific random selection
python -m pytest tests_integration/ -v --run-integration --integration-seed=42

# Use a specific download cache directory
python -m pytest tests_integration/ -v --run-integration --integration-cache-dir=/tmp/sword_cache

# Run integration tests for a single module
python -m pytest tests_integration/ -v --run-integration -k "kjv"
```

Downloads are cached in `.sword_cache/` by default, so repeated runs skip the download step.

## Architecture

```
src/
  builder.py        BuildPipeline, BuildConfig (orchestrator + CLI)
  converter.py      SwordModuleConverter, ConversionConfig (SWORD to JSON)
  osis_parser.py    parse_osis_verse() (token+span extraction)
  hasher.py         ContentHasher (SHA1 at 3 levels)
  download.py       download_modules() (Crosswire downloader)
  file_ops.py       clean_empty_files(), move_public_hash_files()
  git_ops.py        GitRepository (clone/pull/push/keepalive)
```

### Build Pipeline Flow

```
BuildPipeline.run()
  |
  |- download_modules()           Download SWORD .zip files from Crosswire
  |- GitRepository.prepare()      Clone/pull scripture repo
  |- SwordModuleConverter.convert()  Convert each .zip to JSON (3 levels)
  |    |- parse_osis_verse()       Extract tokens+spans from OSIS markup
  |- clean_empty_files()           Remove invalid/empty JSON files
  |- ContentHasher.hash_all()      SHA1 at version/book/chapter levels
  |- GitRepository.prepare()      Clone/pull hash repo
  |- move_public_hash_files()     Copy .sha + metadata to hash repo
  |- push_all_repos()             Commit and push both repos
```

### Token+Span Model (v3)

For OSIS modules with word-level markup (`<w>` tags), each verse includes `tokens` and `spans` arrays. The real output for Genesis 1:1 (KJV) looks like this:

```json
{
    "chapter": 1,
    "verse": 1,
    "name": "Genesis 1:1",
    "text": "In the beginning God created the heaven and the earth.",
    "tokens": [
        {
            "token": "In the beginning",
            "lemma": {
                "strong": [
                    "H07225"
                ]
            },
            "word_start": 1,
            "word_end": 3
        },
        {
            "token": "God",
            "lemma": {
                "strong": [
                    "H0430"
                ]
            },
            "word_start": 4,
            "word_end": 4
        },
        {
            "token": "created",
            "lemma": {
                "strong": [
                    "H0853",
                    "H01254"
                ]
            },
            "morph": {
                "strongMorph": [
                    "TH8804"
                ]
            },
            "word_start": 5,
            "word_end": 5
        },
        {
            "token": "the heaven",
            "lemma": {
                "strong": [
                    "H08064"
                ]
            },
            "word_start": 6,
            "word_end": 7
        },
        {
            "token": "and",
            "lemma": {
                "strong": [
                    "H0853"
                ]
            },
            "word_start": 8,
            "word_end": 8
        },
        {
            "token": "the earth",
            "lemma": {
                "strong": [
                    "H0776"
                ]
            },
            "word_start": 9,
            "word_end": 10
        }
    ],
    "spans": []
}
```

#### Tokens

Flat array of words in verse order. Every token carries:

| Field | Type | Description |
|-------|------|-------------|
| `token` | string | Word text exactly as it appears in the verse. One token may cover multiple whitespace-words when several English words translate a single Hebrew/Greek morpheme (e.g. `"In the beginning"`). |
| `word_start` | int | 1-based position of the first whitespace-word the token covers in `text`. |
| `word_end` | int | 1-based position of the last whitespace-word (inclusive). |

Optional intrinsic attributes (present when the OSIS `<w>` element declares them):

| Field | Type | Shape |
|-------|------|-------|
| `lemma` | object | Scheme-keyed dict of code arrays, e.g. `{"strong": ["H0853", "H01254"]}`. |
| `morph` | object | Scheme-keyed dict of code arrays, e.g. `{"strongMorph": ["TH8804"]}` or `{"oshm": ["HVqp3ms"]}`. |
| `xlit` | object | Scheme-keyed dict of transliteration strings, e.g. `{"Latn": ["Elohim"]}`. |
| `src` | array of int | Source-word indices from the original language, e.g. `[7, 8]`. |
| `gloss` | string | Human-readable gloss. |
| `type` / `subType` | string | OSIS-defined classifiers (e.g. `x-split-1227`). |
| `morphSegmented` | bool | `true` when a `<seg type="x-morph">` appears inside the `<w>`. |
| `variant` | bool | `true` when a `<seg type="x-variant">` appears inside the `<w>`. |
| `variantType` | string | Variant sub-type (e.g. `x-1`, `x-2`). |

Consumers wanting the full Strong's-group translation can concatenate adjacent tokens that share a lemma.

#### Spans

Standoff annotations that cover token index ranges (and the corresponding whitespace-word range in the verse `text`). Genesis 1:1 has no spans, but a verse with a quotation or divine name looks like this:

```json
"spans": [
    {
        "tag": "q",
        "span": "In the beginning God created",
        "attrs": {"who": "narrator"},
        "token_start": 0,
        "token_end": 2,
        "word_start": 1,
        "word_end": 5
    }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `tag` | string | OSIS element name (see list below). |
| `span` | string | The exact OSIS-marked text, including any non-tokenized text inside the element. |
| `token_start` / `token_end` | int | 0-based inclusive range into the `tokens` array. |
| `word_start` / `word_end` | int | 1-based inclusive whitespace-word range in `text`. |
| `attrs` | object | OSIS attributes on the element (omitted when empty). |

**Dual addressing**: `word_start`/`word_end` map 1:1 to whitespace-splitting the verse `text` (so highlighting a span in the rendered verse is trivial, and it works for Hebrew/Greek/Latin/Arabic alike). `token_start`/`token_end` index into `tokens[]` for consumers doing lemma-aware work.

Supported span tags: `q`, `divineName`, `hi`, `transChange`, `foreign`, `inscription`, `name`, `speaker`, `number`, `unit`, `seg`

#### When tokens/spans are present

`tokens` and `spans` are emitted only for modules whose SWORD source is OSIS with `<w>` word-level markup. ThML modules and OSIS modules without `<w>` markup produce verses with just `chapter`, `verse`, `name`, and `text` — no `tokens` or `spans` keys at all.

## GitHub Actions

### Required Secrets

| Secret | Description |
|--------|-------------|
| `GETBIBLE_GIT_EMAIL` | Git user email for commits |
| `GETBIBLE_GIT_USER` | Git username for commits |
| `GETBIBLE_GPG_KEY` | GPG private key (from `gpg -a --export-secret-keys`) |
| `GETBIBLE_GPG_USER` | GPG key user name |
| `GETBIBLE_SSH_KEY` | SSH private key (`id_ed25519`) |
| `GETBIBLE_SSH_PUB` | SSH public key (`id_ed25519.pub`) |
| `GETBIBLE_HASH_REPO` | Official hash repository URL |
| `GETBIBLE_HASH_REPO_T` | Test hash repository URL |
| `GETBIBLE_SCRIPTURE_REPO` | Official scripture repository URL |
| `GETBIBLE_SCRIPTURE_REPO_T` | Test scripture repository URL |

### Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `build.yml` | Monthly (1st at 04:12) + manual | Full production build |
| `test.yml` | Push to `staging` + manual | Test build with staging repos |
| `full-test.yml` | Manual only | Full test build against production |
| `ci.yml` | Push to `master`, PR + manual | Run pytest suite |
| `keep-active.yml` | Weekly (Thursday at 02:22) + manual | Repository keepalive |

All workflows support `workflow_dispatch` for manual triggering from any branch via the GitHub Actions UI.

## License

```
Llewellyn van der Merwe <github@vdm.io>
Copyright (C) 2019. All Rights Reserved
GNU/GPL Version 3 or later - https://www.gnu.org/licenses/gpl-3.0.html
```

The SWORD module converter (`converter.py`) is derived from work by
Jake Wasdin (2017) and Llewellyn van der Merwe (2018), originally
licensed under the BSD 2-Clause License.
