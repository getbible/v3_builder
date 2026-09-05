# getBible Scripture Builder v3

[![Build](https://github.com/getbible/v3_builder/actions/workflows/build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/build.yml)
[![Tests](https://github.com/getbible/v3_builder/actions/workflows/ci.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/ci.yml)
[![Native Smoke](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml/badge.svg?branch=master)](https://github.com/getbible/v3_builder/actions/workflows/native-smoke.yml)
[![Preview](https://github.com/getbible/v3_builder/actions/workflows/preview-build.yml/badge.svg)](https://github.com/getbible/v3_builder/actions/workflows/preview-build.yml)

Builder v3 produces getBible's static Scripture JSON tree from CrossWire SWORD
modules: one document per translation, book, and chapter, the index and
checksum files beside them, and an OpenAPI description of the whole tree. The
master branch replaces PySword in the production path with the
official SWORD engine through the separately released
[`getbiblesword`](https://github.com/getbible/getbiblesword) executable.

## What the native pipeline changes

- Extracts every approved module into deterministic `getbiblesword.ndjson/v1`.
- Independently verifies framing, byte envelopes, artifacts, counts, diagnostics,
  and the footer SHA-256 before Python sees trusted records.
- Verifies raw entries, SWORD projections, attributes, configuration sources, and
  artifacts as transport data before conversion.
- Keeps the existing document shape and complete token/span fields while deriving
  compact chapter editorial, paragraph, title, and introduction semantics.
- Describes the generated tree in a host-free `openapi.json` with every document
  schema embedded, written beside the index files after hashing.
- Treats display text as multilingual content: valid UTF-8 is preserved and isolated
  legacy Windows-1252/Latin-1 bytes are converted instead of rejecting the catalog.
- Treats module ZIPs, the SWORD installation, and lossless contracts as transient
  working data and discards them after every build.
- Applies a default-deny publication policy before a module can enter a build.
- Keeps C++ extraction and Python JSON generation as independently releasable and
  testable projects.

Every workflow reads `conf/GetBibleSwordRelease.json`. Its `version: "latest"`
policy resolves the latest stable GetBibleSWORD release once per build, then
records the exact immutable version, release, asset, and checksum used. The
integration remains under review until the conformance and comparison gates in
[`docs/getbiblesword-pipeline.md`](docs/getbiblesword-pipeline.md) pass.

## Pipeline

```text
CrossWire ZIPs
  -> safe explicit SWORD root
  -> getbiblesword subprocess
  -> one transient lossless NDJSON contract per module
  -> independent Python validator
  -> translation/book/chapter JSON
  -> existing hashes and index files
  -> openapi.json describing the hashed tree
  -> publication repositories
```

Transport and publication remain fail-closed: a missing, unauthorized, incomplete,
or unsuccessful extraction cannot produce a partial catalog. Content projection is
tolerant; an encoding irregularity or unusable optional markup cannot suppress an
otherwise addressable verse.

## Requirements

- Python 3.12+
- Linux x86-64 or ARM64 for the published GetBibleSWORD release
- Latest stable GetBibleSWORD release selected by the checked-in release policy
- `requests` for legacy configuration helpers
- `pytest` and `jsonschema` for the unit tests

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

The installer reads `conf/GetBibleSwordRelease.json`, verifies both the release
checksum file and GitHub asset digest, and writes the resolved provenance to
`.tools/getbiblesword-release.json`. The policy currently follows the latest
stable release, which resolves to `0.3.0` at the time of this change. Reproduction
and incident investigation can override it explicitly with `--version 0.3.0`.

Run the representative-module validation build or the full catalog:

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
| `--bconf` | Requested SWORD-module-to-abbreviation map |
| `--api-base-url` | Public base URL, a host plus one version segment, recorded in index `url` fields; that segment mounts the tree description |

These can also be set in `conf/.config` as `getbible.getbiblesword`,
`getbible.contracts`, `getbible.sword-root`, `getbible.publication-policy`,
and `getbible.api-base-url`.

## Output compatibility and semantic enrichment

The established translation, book, chapter, and verse fields are retained. The
converter derives complete `tokens` and `spans` from OSIS word markup and
promotes supported structural markup into compact fields:

- `editorial` is the ordered chapter-level reading-layout contract. Headings
  identify a verse and the `before` edge; paragraphs use only inclusive integer
  `start` and `end` verse numbers;
- `paragraph: true` marks a verse that begins a new paragraph;
- book-level `titles` retains title metadata belonging to the book itself;
- module, testament, book, and chapter introduction text remains attached at its
  natural structural level.

`editorial` is emitted identically in the nested chapter objects of translation
and book documents and in the standalone chapter document. Chapter- and
verse-level `titles` arrays are deliberately omitted so headings have one
unambiguous public representation; book-level `titles` and verse-level
`paragraph` markers remain. See
[`docs/static-output.md`](docs/static-output.md#chapter-editorial-semantics) for
its exact schema and derivation rules.

Raw, rendered, stripped, configuration, annotation-segment, and filesystem byte
envelopes are never copied into the generated documents. They are validated and
used only while deriving the JSON, then discarded. Unknown contract records fail
closed until a reviewed semantic mapping exists, preventing silent data loss
without bloating every generated document.

Text envelopes are decoded independently from transport validation. Valid UTF-8
sequences remain unchanged. If a historic module contains isolated single-byte text
despite declaring UTF-8, undecodable bytes use the SWORD-compatible Windows-1252
mapping with a total Latin-1 fallback. OSIS tokens and structure remain best-effort
enrichment and are omitted when their source markup is not safe to parse. Repeated
leading `LF`, `CR`, or `CRLF` characters supplied as paragraph formatting are
removed from every verse `text` value; line endings inside the verse are preserved.

## Tree description

After hashing, Builder writes `openapi.json` and `openapi.sha` at the root of the
generated tree and copies them to the hash repository with the index files. The
description is generated by `src/openapi.py` from the translations of the build
and the JSON Schemas under `schema/`, which describe every document type the
converter and hasher emit. It is an OpenAPI 3.1 document that names no host:
its paths start at the version segment of `--api-base-url`, the same URL the
index files record, and every schema is embedded so the description stands
alone. The base URL must be a host plus exactly one version segment; anything
else fails the build before a module is downloaded. A change to what a
document holds is a change to its schema; the unit tests validate generated
documents against the embedded schemas. See
[`docs/static-output.md`](docs/static-output.md#tree-description).

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
authorization, semantic projection, the generated tree description and its
schemas, publication size limits, and fail-closed Git behavior. Integration tests
require the resolved latest stable executable and
download a representative catalog that includes legacy GBF/Windows-1252 content
plus real `div type="x-p"` and `div type="paragraph"` Revelation fixtures.

The `Native GetBibleSWORD Smoke Test` workflow performs this real binary-backed
integration on master, on a daily schedule, and by manual dispatch. The schedule
is deliberate: a newly published GetBibleSWORD release is tested even when Builder
has not changed. Publication and preview workflows consume that same central
latest-stable policy.

The `Test Build` workflow builds the representative real modules and uploads only
the generated tree preview. Lossless contracts are not uploaded or cached. The
manual `Inspect fresh KJV API output` workflow performs a fresh KJV-only build,
prints bounded structure reports, validates the exact chapter `editorial`
contract, checks that `openapi.json` describes the tree it sits in, and prints
representative records for Psalms, John, and Revelation chapters 1–5 directly
in the job log.

## Security and release notes

- The exporter receives no shell command and no stdin.
- Module names are passed as individual subprocess arguments.
- Module ZIPs are extracted with path/link checks. Every release-tar path is
  validated, only the regular `usr/bin/getbiblesword` member is read, and all
  unrelated members—including library links—are ignored rather than extracted.
- The central release policy is resolved once per job; its exact stable version
  and asset are checksum-verified and recorded before execution.
- Artifact symlinks are validated as metadata but never created by Builder.
- Unknown contract major versions and unmapped v1 records are rejected.
- Generated files at or above 95 MiB are rejected before hashing or publication.
- Scripture publication must complete before the hash repository is attempted.

See [`AGENTS.md`](AGENTS.md) for contributor invariants.
See [`docs/static-output.md`](docs/static-output.md) for the exact output layers,
file layout, and tree description.

## License

Builder v3 is licensed under `GPL-2.0-only`. The legacy Python converter contains
BSD-2-Clause-derived work that is compatible with distribution under GPL v2.
Individual SWORD modules retain their own distribution terms; inclusion in this
software's approval manifest does not relicense module content.
