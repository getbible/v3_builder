# GetBible API v3 output contract

Builder produces three distinct layers. They must not be confused because they
serve different trust and compatibility purposes.

## 1. Lossless extraction contract

For every SWORD module, GetBibleSWORD emits:

```text
sword_contracts/<SWORD-module-name>.ndjson
sword_contracts/manifest.json
```

The NDJSON file is the authoritative extraction record. It retains exact byte
envelopes for module configuration, entries, SWORD projections, attributes,
annotations, and module filesystem artifacts. The complete stream is validated
before conversion.

`manifest.json` uses
`getbible.contract-archive-manifest/v1`. For every module it records:

- the NDJSON filename, complete-file size and SHA-256;
- the contract stream SHA-256 declared by GetBibleSWORD;
- producer and SWORD engine versions;
- classification, entry count, artifact count and artifact bytes;
- diagnostic severity counts and unknown record types.

The complete-file hash includes the footer. The stream hash authenticates all
records preceding the footer. This deliberately gives archive tooling both views.

Contracts are build/archive artifacts, not automatically public endpoints. Their
publication must be authorized independently because they can contain original
module files and licensed media.

## 2. Backward-compatible static API

The current public file layout remains:

```text
<abbreviation>.json
<abbreviation>/<book-number>.json
<abbreviation>/<book-number>/<chapter-number>.json
translations.json
<abbreviation>/books.json
<abbreviation>/<book-number>/chapters.json
checksum.json and matching .sha/checksum files
```

### Translation document

`<abbreviation>.json` contains translation, language, direction, encoding,
distribution metadata, every included book/chapter/verse, `source_contract`, and
translation-level `source` metadata. `source` retains the exact module record,
configuration sources, ordered configuration entries, diagnostics, and unknown
contract records.

### Book and chapter documents

Book and chapter files repeat the stable translation metadata required by existing
clients. They also carry `source_contract`, allowing any response to be tied back
to its validated extraction stream.

### Verse document

Every emitted verse keeps the existing fields:

```json
{"chapter":1,"verse":1,"name":"Genesis 1:1","text":"In the beginning..."}
```

It may add normalized `tokens` and `spans` when the source contains supported OSIS
word markup. It always adds `source`, containing:

- contract id, entry ordinal, exact key and canonical scope;
- authoritative raw bytes;
- SWORD's rendered-default and stripped projections;
- all official SWORD attributes;
- every lossless annotation segment.

All byte values are objects containing base64, SHA-256, size, encoding and optional
verified UTF-8. Base64 is authoritative.

Introduction entries are attached at translation, book, or chapter level. Entries
that cannot safely map to a normal Bible scope are retained as `unscoped_entries`
instead of being discarded.

## 3. Planned semantic and interchange projections

The lossless contract is sufficient to derive richer APIs without re-downloading
or guessing at source data. The next additive projections are:

```text
<abbreviation>/manifest.json
<abbreviation>/rich/<book-number>/<chapter-number>.json
<abbreviation>/usj/<book-number>.json
<abbreviation>/scripture-burrito/metadata.json
```

- `manifest.json` will advertise capabilities, canon, contract digests, file
  locations, and publication permissions.
- `rich` will normalize titles, paragraphs, poetry, notes, cross-references,
  variants, figures, milestones, references, words, lemmas and morphology while
  retaining source provenance.
- `usj` will be an industry interchange projection for Scripture text, not the
  authoritative GetBible storage format.
- Scripture Burrito metadata will package identification, language, licensing and
  interchange information.

Commentaries, dictionaries and general books will use their own normalized domain
schemas. They remain representable in the common lossless NDJSON layer.

## Compatibility rules

- Existing v3 fields are not removed or retyped.
- New v3 information is additive.
- Raw byte envelopes are never replaced by a derived text projection.
- Unknown v1 contract records are retained.
- Unsupported contract major versions fail closed.
- A semantic projection never becomes more authoritative than its source contract.
- Publication authorization is evaluated separately for text, annotations,
  contracts and binary artifacts.

Schemas currently available:

- `schema/v3/source.schema.json` — native source extension;
- `schema/archive/contract-manifest.schema.json` — lossless archive inventory.
