# getBible API Builder v3

[![Build](https://github.com/getbible/v3_builder/actions/workflows/build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/build.yml)
[![Tests](https://github.com/getbible/v3_builder/actions/workflows/ci.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/ci.yml)
[![Native Smoke](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml/badge.svg?branch=master)](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml)
[![Preview](https://github.com/getbible/v3_builder/actions/workflows/preview-build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/preview-build.yml)

Builder v3 produces getBible's static Scripture JSON API from CrossWire SWORD
modules. The master branch replaces PySword in the production path with the
official SWORD engine through the separately released
[`getbiblesword`](https://github.com/getbible/getbiblesword) executable.

## What the native pipeline changes

- Extracts every approved module into deterministic `getbiblesword.ndjson/v1`.
- Independently verifies framing, byte envelopes, artifacts, counts, diagnostics,
  and the footer SHA-256 before Python sees trusted records.
- Validates raw entries, SWORD rendering/stripping, official attributes, lexical
  annotations, exact configuration sources, and module artifacts before conversion.
- Keeps the existing API shape and complete token/span fields while deriving compact
  paragraph, title, and introduction semantics.
- Treats module ZIPs, the SWORD installation, and lossless contracts as transient
  working data and discards them after every build.
- Applies a default-deny publication policy before a module can enter a build.
- Keeps C++ extraction and Python API generation as independently releasable and
  testable projects.

The integration follows the latest stable GetBibleSWORD release and records the
exact resolved version and checksum for every build. It remains a review pipeline,
not a production promotion, until the conformance and comparison gates in
[`docs/getbiblesword-pipeline.md`](docs/getbiblesword-pipeline.md) pass.

## Pipeline

```text
CrossWire ZIPs
  -> safe explicit SWORD root
  -> getbiblesword subprocess
  -> one transient lossless NDJSON contract per module
  -> independent Python validator
  -> translation/book/chapter JSON
  -> existing hashes and publication repositories
```

The build is fail-closed: one missing, unauthorized, corrupt, or unsuccessful
module aborts the publishing build instead of producing a partial catalog.

## Requirements

- Python 3.12+
- Linux x86-64 or ARM64 for the published GetBibleSWORD release
- Latest stable GetBibleSWORD release
- `requests` for legacy configuration helpers
- `pytest` for tests

PySword remains only in the legacy converter and historical unit comparisons. It
is not installed or called by the native build pipeline.

## Quick start

```bash
git clone https://github.com/getbible/v3_builder.git
cd v3_builder

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Install the latest stable public release; no GitHub token is required:

```bash
python scripts/install_getbiblesword.py
export GETBIBLESWORD_BIN="$PWD/.tools/getbiblesword"
```

The installer follows the latest stable release by default, verifies both the
release checksum file and GitHub asset digest, and writes the resolved provenance
to `.tools/getbiblesword-release.json`. Reproduction and incident investigation
can request an exact release with `--version 0.1.1`.

Run the six-module validation build or the full catalog:

```bash
python src/builder.py --test
python src/builder.py
```

Existing build modes remain available:

```bash
python src/builder.py --pull --push \
  --repo-hash="git@github.com:getbible/v3.git" \
  --repo-scripture="git@github.com:getbible/v3_scripture.git"

python src/builder.py --hash-only
python src/builder.py --no-download
python src/builder.py --dry
python src/builder.py --verbose
```

Important native options:

| Option | Purpose |
|---|---|
| `--getbiblesword` | Executable path or command name |
| `--contracts` | Validated NDJSON working directory |
| `--sword-root` | Fresh explicit SWORD installation |
| `--publication-policy` | Default-deny approval manifest |
| `--bconf` | Requested SWORD-module-to-API map |

These can also be set in `conf/.config` as `getbible.getbiblesword`,
`getbible.contracts`, `getbible.sword-root`, and
`getbible.publication-policy`.

## API compatibility and semantic enrichment

Translation, book, chapter, and verse fields used by current clients are retained.
The converter derives complete `tokens` and `spans` from OSIS word markup and
promotes supported structural markup into compact API fields:

- `paragraph: true` marks a verse that begins a new paragraph;
- `titles` contains typed visible headings such as chapter titles and Psalm
  superscriptions, including title token/span data when available;
- module, testament, book, and chapter introduction text remains attached at its
  natural structural level.

Raw, rendered, stripped, configuration, annotation-segment, and filesystem byte
envelopes are never copied into the published API. They are validated and used only
while deriving the JSON, then discarded. Unknown contract records fail closed until
a reviewed semantic mapping exists, preventing silent data loss without bloating
every API response.

## Publication authorization

`conf/PublicationPolicy.json` starts with the 117 translations already present in
the v3 catalog. New translations need a separate rights review and explicit
approval. See [`docs/publication-policy.md`](docs/publication-policy.md).

The policy belongs in Builder, not the generic extractor: permission to read a
locally installed module is not the same as permission to publish transformed
artifacts.

## Tests

```bash
python -m pytest tests/ -v
python -m pytest tests_integration/ -v --run-integration
```

Unit tests require no native executable. They cover corrupt streams, sequence and
footer verification, byte envelopes, ZIP traversal/conflicts, publication
authorization, semantic projection, publication size limits, and fail-closed Git
behavior. Integration tests require the resolved latest stable executable and
download the real six-module test catalog.

The `Native GetBibleSWORD Smoke Test` workflow performs this real binary-backed
integration on master, on a daily schedule, and by manual dispatch. The schedule
is deliberate: a newly published GetBibleSWORD release is tested even when Builder
has not changed.

The `Test Build` workflow builds six real modules and uploads only the generated
static API preview. Lossless contracts are not uploaded or cached. The manual
`Inspect fresh KJV API output` workflow performs a fresh KJV-only build and prints
bounded structure reports plus representative records for Psalms, John, and
Revelation chapters 1–5 directly in the job log.

## Security and release notes

- The exporter receives no shell command and no stdin.
- Module names are passed as individual subprocess arguments.
- Module ZIPs and release tarballs are extracted with path/link checks.
- The latest stable release is resolved once per job; its exact asset is then
  checksum-verified and recorded before execution.
- Artifact symlinks are validated as metadata but never created by Builder.
- Unknown contract major versions and unmapped v1 records are rejected.
- Generated files at or above 95 MiB are rejected before hashing or publication.
- Scripture publication must complete before the hash repository is attempted.

See [`AGENTS.md`](AGENTS.md) for contributor invariants.
See [`docs/api-v3.md`](docs/api-v3.md) for the exact output layers and file layout.

## License

Builder v3 is licensed under `GPL-2.0-only`. The legacy Python converter contains
BSD-2-Clause-derived work that is compatible with distribution under GPL v2.
Individual SWORD modules retain their own distribution terms; inclusion in this
software's approval manifest does not relicense module content.
