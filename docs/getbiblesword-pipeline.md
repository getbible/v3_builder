# GetBibleSWORD pipeline

Builder v3 uses the official CrossWire SWORD engine through the separately released
`getbiblesword` executable. The executable is a subprocess dependency, not an
in-process Python extension. This keeps the C++/GPL boundary explicit and lets both
projects release, test, and evolve independently.

## Trust flow

1. Builder loads `conf/CrosswireModulesMap.json`.
2. `conf/PublicationPolicy.json` must explicitly approve every requested module.
3. Builder downloads the approved CrossWire ZIP files.
4. ZIPs are installed into a fresh explicit SWORD root. Absolute paths, traversal,
   links, oversized archives, and conflicting files are rejected.
5. The pinned `getbiblesword` release extracts each module into one NDJSON file.
6. Builder independently verifies the v1 contract before conversion:
   zero-based sequence, LF framing, every byte envelope, every artifact group,
   record counts, diagnostics, successful footer, and exact stream SHA-256.
7. Python derives the static API from only validated contracts.
8. The existing hashing and publication stages run unchanged.

The build fails closed. It does not publish a partial catalog when a module is
missing, extraction fails, an error diagnostic is present, or validation fails.

## Lossless and derived representations

The NDJSON contract is the source of truth. Its base64 values are authoritative;
the optional UTF-8 member is only a convenience projection. Builder never replaces
raw bytes with SWORD's rendered or stripped views.

The API retains its existing translation/book/chapter/verse fields and token/span
model. It adds:

- `source_contract` on translation, book, and chapter documents;
- `source` on every verse, containing raw bytes, rendered and stripped projections,
  official entry attributes, annotation segments, key, and canonical scope;
- `source` on the translation document for exact module/configuration metadata;
- `introductions` or `introduction` at translation, book, and chapter scope.

This is intentionally additive. Existing API clients do not need to understand the
new fields. New consumers can recover titles, paragraph milestones, notes,
cross-references, poetry, variants, figures, references, words, and unknown markup
from the exact entry envelope even before a higher-level semantic projection exists.
The extension is documented as JSON Schema in `schema/v3/source.schema.json`.

## Version and production gate

The branch pins getBibleSWORD `0.1.0`, whose own documentation calls it an
engineering preview. The Builder integration therefore remains a review branch
until both projects' production gates are met:

- a redistributable driver-spanning conformance corpus;
- this independent validator/reassembler passing that corpus;
- deterministic repeated extraction on supported architectures;
- maintainer review of the classification and public API projection;
- successful comparison against the current published API for all approved Bibles.

Unknown v1 record types are retained in translation source metadata. An unknown
contract major version is rejected.

## Release installation

`scripts/install_getbiblesword.py` downloads the exact architecture asset and its
`.sha256` companion from release `v0.1.0`, verifies the checksum, rejects unsafe tar
members, and installs only `usr/bin/getbiblesword`.

The GetBibleSWORD repository and its release assets are public, so neither local
builds nor GitHub Actions need an access token. Local builds may use the verified
installer or pass `--getbiblesword=/absolute/path`.

## Working directories

- `sword_zip/`: downloaded module archives;
- `sword_root/`: fresh explicit SWORD installation;
- `sword_contracts/`: validated NDJSON contracts;
- `v3_scripture/`: private generated Scripture JSON;
- `v3/`: public hash/index repository.

The first three are reproducible working data and are gitignored.
