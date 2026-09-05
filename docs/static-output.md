# Generated static output

Builder produces a compact tree of static Scripture JSON documents from a
validated, transient GetBibleSWORD extraction stream. The extraction stream is
a build boundary, not an output layer or an archive: it is freshly generated
for every build and discarded after conversion.

## File layout

The generated tree keeps its established layout:

```text
<abbreviation>.json
<abbreviation>/<book-number>.json
<abbreviation>/<book-number>/<chapter-number>.json
translations.json
<abbreviation>/books.json
<abbreviation>/<book-number>/chapters.json
checksum.json and matching .sha/checksum files
openapi.json and openapi.sha
```

The translation document contains the language, direction, encoding,
distribution metadata, and its complete book/chapter/verse hierarchy. Book and
chapter documents repeat the stable translation metadata needed when those files
are read on their own.

A chapter for which the source supplies an introduction but no verse text stays
nested in its book and translation documents with an empty `verses` array and
its title metadata; it has no standalone document and no entry in
`chapters.json`.

## Tree description

`openapi.json` at the root of the generated tree is the OpenAPI 3.1 description
of every document path, its parameters, and the JSON Schema of each response.
`src/openapi.py` generates it after hashing, from the translations the build
produced and the schemas checked in under `schema/`. Those schemas are embedded
under `components`, so the description stands alone, and its prose carries the
reading rules below. `openapi.sha` holds its SHA-1 like every other document's
checksum, and both files are copied to the hash repository with the index files.

The description names no host. Its paths start at the version segment of the
builder's public base URL (`--api-base-url` or `getbible.api-base-url`), the
same URL the index files record in their `url` fields, so the tree and its
description move together when the base URL changes. The base URL must name
a host and end in exactly one version segment, `/v3` by default; a trailing
slash is dropped, and any other value fails the build before a module is
downloaded.

`schema/*.schema.json` is the source of truth for every document type:
translation, book and chapter documents, the three index documents, the
checksum index and `.sha` text, and the verse, token, span, editorial, title
and introduction objects they contain. A change to what the converter or hasher
emits is a change to the matching schema. The unit tests validate generated
documents against the embedded schemas, and the KJV inspection checks that the
description is a host-free document that lists the built translation, embeds
every schema, keeps every path under one version segment, and matches its
checksum.

The extension-less tab-separated listings (`translations`, `books`, `chapters`
and `checksum`) are text companions of the JSON indexes and are not described.

## Chapter editorial semantics

Each chapter may contain an optional ordered `editorial` array:

```json
{
  "chapter": 1,
  "name": "Genesis 1",
  "editorial": [
    {
      "order": 0,
      "type": "heading",
      "anchor": {
        "verse": 1,
        "edge": "before"
      },
      "text": "The Creation",
      "heading_type": "section",
      "canonical": false
    },
    {
      "order": 1,
      "type": "paragraph",
      "start": 1,
      "end": 2
    }
  ],
  "verses": []
}
```

The same `editorial` array is present in all three representations of that
chapter:

- the chapter nested in `<abbreviation>.json`;
- the chapter nested in `<abbreviation>/<book-number>.json`;
- the standalone
  `<abbreviation>/<book-number>/<chapter-number>.json` document.

`order` is a contiguous zero-based integer across every heading and paragraph
entry in chapter reading order.

A heading has exactly these fields:

- `order`;
- `type: "heading"`;
- `anchor.verse`, an emitted verse number in the current chapter;
- `anchor.edge: "before"`;
- `text`;
- `heading_type`, copied from the OSIS title type, or `unspecified` when the
  source supplies no type;
- `canonical`, which is `true` only when the source OSIS explicitly marks the
  title canonical and is otherwise `false`.

`canonical` records source metadata. Builder does not make a theological
judgment about whether a heading is inspired.

A paragraph has exactly `order`, `type: "paragraph"`, `start`, and `end`.
`start` and `end` are inclusive emitted verse numbers in the current chapter.
There are no word positions and no cross-chapter continuation fields.

Paragraph ranges are derived from explicit OSIS paragraph starts. Builder
recognizes `milestone type="x-p"`, opening `p`, and CrossWire's opening
`div type="x-p|paragraph"` encodings. An element carrying `eID` closes a
milestone and never starts a paragraph:

1. If the chapter has no explicit paragraph start, Builder emits no paragraph
   editorial entries.
2. If at least one explicit start exists, paragraph ranges completely and
   contiguously cover the chapter's emitted verses.
3. When the first explicit start is after the first emitted verse, Builder adds
   the implicit opening range needed to cover the earlier verses.
4. Each range ends at the actual emitted verse immediately before the next
   start. The final range ends at the chapter's final emitted verse.

This makes the compact ranges directly usable for paragraph rendering while
remaining faithful to the source markers. Headings may be emitted without
paragraph entries. The entire `editorial` field is omitted when a chapter has
neither headings nor explicit paragraph markers.

## Verse and book-title semantics

Every emitted verse keeps the established fields:

```json
{"chapter":1,"verse":1,"name":"Genesis 1:1","text":"In the beginning..."}
```

`text` never begins with `LF` (`\n`), `CR` (`\r`), or a `CRLF` pair. Builder
removes repeated leading line endings introduced by source paragraph formatting
but preserves line endings that occur inside the verse.

Supported OSIS structure is projected into compact, optional fields:

- `paragraph: true` means the verse begins a paragraph;
- `tokens` retains word-level lexical attributes and visible word positions;
- `spans` retains supported annotations over token and visible-word ranges.

The book object may contain an ordered `titles` list for title metadata belonging
to that book. A book title contains `text`, its OSIS `type` when supplied,
optional `canonical` and `subtype` values, and title-local `tokens`/`spans` when
word markup is available.

Chapters with verses, and verse objects, do not contain `titles`. Builder
collects their OSIS
chapter titles, section headings, Psalm superscriptions, and other visible
headings as transient conversion semantics, places them in chapter `editorial`
with their verse anchors, then removes the duplicate title arrays before writing
any document. Headings therefore have one representation, and `titles` never
has to be reconciled with `editorial`.

Module, testament, book, and chapter introduction prose is normalized into an
`introduction` list at the appropriate level. Structural title-only entries are
promoted to book `titles` or chapter `editorial` without duplicating the same
string as introduction prose.

## Transient extraction boundary

For each requested module, GetBibleSWORD creates one NDJSON contract in the
working directory. Builder validates the complete successful footer, stream
hash, sequence, every byte envelope, artifact group, count, and diagnostic before
conversion. Conversion then streams entries from that validated file so a large
contract is not retained as a second in-memory copy.

The module ZIP, materialized SWORD installation, and validated NDJSON are deleted
after the build attempt, including failed conversions. They are not cached,
uploaded, committed, or retained as build artifacts.

The following extraction-only data is deliberately absent from every generated
document:

- `source` and `source_contract` envelopes;
- raw/rendered/stripped byte projections;
- base64 payloads and annotation-segment envelopes;
- module filesystem artifacts and exact configuration-source records.

An unknown contract major version or unmapped v1 record type fails conversion.
This prevents silent semantic loss while keeping the generated documents small.

## Publication rules

- Scripture, token/span, introduction, and book-title fields are not retyped.
- Chapter and verse `titles` are intentionally omitted in favor of their single,
  position-aware `editorial` representation; only a chapter without published
  verses keeps its `titles`, nested in its book and translation documents.
- A module map that abbreviates a translation with a reserved root document
  name (`translations`, `checksum`, `books`, `chapters`, `openapi`) fails
  before download.
- Semantic fields are additive and deterministic.
- No emitted verse `text` begins with a line-ending character.
- `openapi.json` is generated from the hashed tree on every build, never
  hand-edited, and the hasher never treats it as a translation.
- Valid upstream content growth is accepted without comparison to an older build.
- Files at or above 95 MiB fail before hashing or publication.
- Symlinks and special files in generated output fail validation.
- Scripture output is validated and published before its derived hash repository.
- A Git failure exits the workflow nonzero. Permanent remote rejections such as
  GitHub `GH001` are not retried.

The manual `Inspect fresh KJV API output` workflow builds KJV from a fresh module
download, validates the exact `editorial` object shapes and range coverage,
checks the generated tree description, prints bounded structural summaries and
representative records for Psalms, John, and Revelation chapters 1–5, and
applies the same envelope and size checks without publishing anything.
