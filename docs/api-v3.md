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

Chapter and verse objects do not contain `titles`. Builder collects their OSIS
chapter titles, section headings, Psalm superscriptions, and other visible
headings as transient conversion semantics, places them in chapter `editorial`
with their verse anchors, then removes the duplicate title arrays before writing
any endpoint. Consumers therefore have one public source for headings and never
need to reconcile `titles` with `editorial`.

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

The following extraction-only data is deliberately absent from every public API
document:

- `source` and `source_contract` envelopes;
- raw/rendered/stripped byte projections;
- base64 payloads and annotation-segment envelopes;
- module filesystem artifacts and exact configuration-source records.

An unknown contract major version or unmapped v1 record type fails conversion.
This prevents silent semantic loss while keeping the public API small.

## Publication rules

- Scripture, token/span, introduction, and book-title fields are not retyped.
- Chapter and verse `titles` are intentionally omitted in favor of their single,
  position-aware `editorial` representation.
- Semantic fields are additive and deterministic.
- No emitted verse `text` begins with a line-ending character.
- Valid upstream content growth is accepted without comparison to an older build.
- Files at or above 95 MiB fail before hashing or publication.
- Symlinks and special files in generated output fail validation.
- Scripture output is validated and published before its derived hash repository.
- A Git failure exits the workflow nonzero. Permanent remote rejections such as
  GitHub `GH001` are not retried.

The manual `Inspect fresh KJV API output` workflow builds KJV from a fresh module
download, validates the exact `editorial` object shapes and range coverage,
prints bounded structural summaries and representative records for Psalms, John,
and Revelation chapters 1–5, and applies the same envelope and size checks
without publishing anything.
