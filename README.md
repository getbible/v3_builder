# getBible API Builder v3

[![Build](https://github.com/getbible/v3_builder/actions/workflows/build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/build.yml)
[![Tests](https://github.com/getbible/v3_builder/actions/workflows/ci.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/ci.yml)
[![Native Smoke](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml/badge.svg?branch=getbiblesword)](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml)

Builder v3 produces getBible's static Scripture JSON API from CrossWire SWORD
modules. The `getbiblesword` branch replaces PySword in the production path with
the official SWORD 1.9 engine through the separately released
[`getbiblesword`](https://github.com/getbible/getbiblesword) executable.

## What the native pipeline changes

- Extracts every approved module into deterministic `getbiblesword.ndjson/v1`.
- Independently verifies framing, byte envelopes, artifacts, counts, diagnostics,
  and the footer SHA-256 before Python sees trusted records.
- Preserves raw entries, SWORD rendering/stripping, official attributes, lexical
  annotations, exact configuration sources, and module artifacts.
- Keeps the existing API shape and token/span fields while adding lossless `source`
  envelopes and introduction records.
- Applies a default-deny publication policy before a module can enter a build.
- Keeps C++ extraction and Python API generation as independently releasable and
  testable projects.

The current integration pins GetBibleSWORD `0.1.0`. It is a review pipeline, not a
production promotion, until the conformance and comparison gates in
[`docs/getbiblesword-pipeline.md`](docs/getbiblesword-pipeline.md) pass.

## Pipeline

```text
CrossWire ZIPs
  -> safe explicit SWORD root
  -> getbiblesword subprocess
  -> one lossless NDJSON contract per module
  -> independent Python validator
  -> translation/book/chapter JSON
  -> existing hashes and publication repositories
```

The build is fail-closed: one missing, unauthorized, corrupt, or unsuccessful
module aborts the publishing build instead of producing a partial catalog.

## Requirements

- Python 3.12+
- Linux x86-64 or ARM64 for the published GetBibleSWORD release
- GetBibleSWORD 0.1.0
- `requests` for legacy configuration helpers
- `pytest` for tests

PySword remains only in the legacy converter and historical unit comparisons. It
is not installed or called by the native build pipeline.

## Quick start

```bash
git clone https://github.com/getbible/v3_builder.git
cd v3_builder
git switch getbiblesword

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Install the pinned public release; no GitHub token is required:

```bash
python scripts/install_getbiblesword.py
export GETBIBLESWORD_BIN="$PWD/.tools/getbiblesword"
```

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

## API compatibility and preservation

Translation, book, chapter, and verse fields used by current clients are retained.
Every verse now also has a `source` object containing:

- the exact SWORD key and canonical scope;
- raw bytes as a verified base64 envelope, with UTF-8 when valid;
- rendered-default and stripped projections;
- all official entry attributes;
- lossless annotation segments;
- the native entry ordinal and contract identifier.

The converter still derives `tokens` and `spans` from OSIS word markup. Module,
testament, book, and chapter introduction entries are retained at their natural
structural level. Exact configuration files and interpreted configuration entries
are retained on the translation document.

This means titles, paragraphs, notes, cross-references, poetry, variant readings,
milestones, figures, references, word data, and unknown annotations are no longer
discarded at the extraction boundary. Some are initially exposed through the
lossless source envelope rather than a finalized semantic API field; that mapping
can evolve without re-extracting or guessing at the source.

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
footer verification, byte envelopes, artifact hashing, ZIP traversal/conflicts,
publication authorization, and API source preservation. Integration tests require
the pinned executable and download the real six-module test catalog.

The `Native GetBibleSWORD Smoke Test` workflow performs this real binary-backed
integration automatically on the `getbiblesword` branch. Once the workflow is on
the default branch, it can also be started manually from GitHub Actions.

## Security and release notes

- The exporter receives no shell command and no stdin.
- Module names are passed as individual subprocess arguments.
- Module ZIPs and release tarballs are extracted with path/link checks.
- Release assets are pinned by version and verified with their published SHA-256.
- Artifact symlinks are validated as metadata but never created by Builder.
- Unknown contract major versions are rejected; unknown v1 records are retained.

See [`AGENTS.md`](AGENTS.md) for contributor invariants.

## License

Builder v3 is licensed under `GPL-2.0-only`. The legacy Python converter contains
BSD-2-Clause-derived work that is compatible with distribution under GPL v2.
Individual SWORD modules retain their own distribution terms; inclusion in this
software's approval manifest does not relicense module content.
