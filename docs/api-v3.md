# GetBible API v3 output contract

Builder produces a compact static Scripture API from a validated, transient
GetBibleSWORD extraction stream. The extraction stream is a build boundary, not
an API layer or an archive: it is freshly generated for every build and discarded
after conversion.

## Static file layout

The public layout remains compatible with existing clients:

```text
<abbreviation>.json
<abbreviation>/<book-number>.json
<abbreviation>/<book-number>/<chapter-number>.json
translations.json
<abbreviation>/books.json
<abbreviation>/<book-number>/chapters.json
checksum.json and matching .sha/checksum files
```

The translation document contains the language, direction, encoding,
distribution metadata, and its complete book/chapter/verse hierarchy. Book and
chapter documents repeat the stable translation metadata needed when those files
are requested directly.

## Verse and structural semantics

Every emitted verse keeps the established fields:

```json
{"chapter":1,"verse":1,"name":"Genesis 1:1","text":"In the beginning..."}
```

Supported OSIS structure is projected into compact, optional fields:

- `paragraph: true` means the verse begins a paragraph;
- `titles` is an ordered list of visible headings, including chapter titles,
  section headings, and Psalm superscriptions where the module provides them;
- `tokens` retains word-level lexical attributes and visible word positions;
- `spans` retains supported annotations over token and visible-word ranges.

A title contains `text`, its OSIS `type` when supplied, optional `canonical` and
`subtype` values, and title-local `tokens`/`spans` when word markup is available.
Chapter and book titles are attached at their natural structural level. A title
inside a verse is attached to that verse, which lets clients locate the heading
without reconstructing raw OSIS.

Module, testament, book, and chapter introduction prose is normalized into an
`introduction` list at the appropriate level. Structural title-only entries are
promoted to `titles` without duplicating the same string as introduction prose.

## Transient extraction boundary

For each requested module, GetBibleSWORD creates one NDJSON contract in the
working directory. Builder validates the complete successful footer, stream
hash, sequence, every byte envelope, artifact group, count, and diagnostic before
conversion. Conversion then streams entries from that validated file so a large
contract is not retained as a second in-memory copy.

The module ZIP, materialized SWORD installation, and validated NDJSON are deleted
after the build attempt, including failed conversions. They are not cached,
uploaded, committed, or retained as build artifacts.

The following extraction-only data is deliberately absent from every public API
document:

- `source` and `source_contract` envelopes;
- raw/rendered/stripped byte projections;
- base64 payloads and annotation-segment envelopes;
- module filesystem artifacts and exact configuration-source records.

An unknown contract major version or unmapped v1 record type fails conversion.
This prevents silent semantic loss while keeping the public API small.

## Publication rules

- Existing stable API fields are not removed or retyped.
- Semantic fields are additive and deterministic.
- Valid upstream content growth is accepted without comparison to an older build.
- Files at or above 95 MiB fail before hashing or publication.
- Symlinks and special files in generated output fail validation.
- Scripture output is validated and published before its derived hash repository.
- A Git failure exits the workflow nonzero. Permanent remote rejections such as
  GitHub `GH001` are not retried.

The manual `Inspect fresh KJV API output` workflow builds KJV from a fresh module
download, prints bounded structural summaries and representative records for
Psalms, John, and Revelation chapters 1–5, and applies the same envelope and size
checks without publishing anything.
