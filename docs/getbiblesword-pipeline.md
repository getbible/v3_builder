# GetBibleSWORD pipeline

Builder v3 uses the official CrossWire SWORD engine through the separately
released `getbiblesword` executable. The executable is a subprocess dependency,
not an in-process Python extension, so the C++/GPL extraction boundary remains
explicit and both projects can release and test independently.

## Trust and publication flow

1. Builder loads the requested module map.
2. `conf/PublicationPolicy.json` must explicitly approve every module.
3. Builder freshly downloads the approved CrossWire ZIP files; no prior module or
   extraction cache is used.
4. ZIPs are installed into a fresh explicit SWORD root. Absolute paths, traversal,
   links, oversized archives, and conflicting files are rejected.
5. The latest stable GetBibleSWORD release extracts each module to a transient
   NDJSON contract.
6. Builder independently validates the complete v1 stream: zero-based sequence,
   LF framing, byte envelopes, artifact groups, counts, diagnostics, successful
   footer, and exact stream SHA-256.
7. Python streams the validated entries into compact translation, book, chapter,
   and verse JSON. It retains text, complete token/span data, paragraph markers,
   headings, and introduction semantics—not extraction envelopes.
8. The module ZIPs, SWORD root, and contracts are discarded after the build
   attempt, including a failed conversion.
9. Generated Scripture files pass hard-size, historical-growth, and filesystem
   safety gates before hashing.
10. Scripture publication completes before the derived hash repository is
    attempted. Any Git error fails the workflow; permanent remote rejections are
    not retried.

The build therefore fails closed. It does not publish a partial catalog when a
module is missing, extraction or validation fails, an unmapped record appears, a
generated file is unsafe or too large, or publication fails.

When a reviewed API enrichment intentionally grows tracked JSON by more than
25%, manually dispatch the publishing workflow with `allow_output_growth`
enabled. The override applies only to that run and never bypasses the 95 MiB
hard file ceiling. After the enlarged files are published, their committed sizes
become the baseline and subsequent scheduled or manual builds run with the
growth gate enabled normally.

Publishing workflows are serialized per destination repository. JSON files are
written through same-directory temporary files and atomically replaced; an
incomplete temporary write is a publication error and cannot enter hashes or a
Git commit.

## Lossless input, lean output

The NDJSON contract is authoritative only while a build is running. Its base64
values preserve exact bytes for validation and semantic derivation; optional UTF-8
members are convenience projections. The contract is not an archive and never
becomes a public endpoint.

The static API keeps its established translation/book/chapter/verse fields and
complete token/span model. It additionally projects supported OSIS structure:

- `paragraph: true` on a verse that begins a paragraph;
- ordered `titles` for chapter headings, section headings, Psalm superscriptions,
  and other typed titles supplied by the module;
- normalized `introduction` prose at its natural structural level.

Raw bytes, rendered/stripped projections, base64 values, annotation segments,
module files, exact configuration-source records, `source`, and `source_contract`
are never copied into the API. Unknown v1 record types fail until an explicit,
reviewed semantic mapping exists; unknown contract major versions are rejected.

## Release installation

`scripts/install_getbiblesword.py` resolves GitHub's latest stable release,
downloads the matching architecture asset and `.sha256` companion, verifies the
checksum and GitHub asset digest, rejects unsafe tar members, and installs only
`usr/bin/getbiblesword`. It writes exact release provenance to
`.tools/getbiblesword-release.json`.

`--version X.Y.Z` remains available for deterministic reproduction or incident
investigation. Production and scheduled conformance workflows use the default
latest-stable policy so a newly published extractor release is tested without
waiting for a Builder commit.

## Working directories

- `sword_zip/`: freshly downloaded module archives;
- `sword_root/`: fresh explicit SWORD installation;
- `sword_contracts/`: validated NDJSON working files;
- `v3_scripture/`: generated Scripture JSON repository;
- `v3/`: generated public hash/index repository.

The first three are reproducible transient inputs. Builder removes them after
each build attempt and workflows neither cache nor upload them.

## Inspectable workflows

`.github/workflows/preview-build.yml` builds the six-module test catalog with the
latest stable binary and uploads only the generated API preview. It never pushes
to public repositories.

`.github/workflows/inspect-kjv-api.yml` is a manual, KJV-only diagnostic build. It
starts from a fresh module download and prints size reports, structural summaries,
and representative full verse records for Psalms, John, and Revelation chapters
1–5. It rejects missing data, malformed token/span ranges, source-envelope leaks,
symlinks, and files at or above 95 MiB. It does not cache, upload, or publish the
result.

## Production gate

The Builder integration remains under review until these gates are met:

- a redistributable driver-spanning conformance corpus;
- this independent validator/reassembler passing that corpus;
- deterministic repeated extraction on supported architectures;
- maintainer review of classification and public API projection;
- successful comparison against the current published API for all approved Bibles.
