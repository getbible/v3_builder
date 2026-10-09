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
checksum.json at every level, and the checksum/translations/books/chapters listings
openapi.json
a .sha sibling beside every one of the JSON documents above
```

The translation document contains the language, direction, encoding,
distribution metadata, and its complete book/chapter/verse hierarchy. Book and
chapter documents repeat the stable translation metadata needed when those files
are read on their own.

Every JSON document, the index and checksum documents and `openapi.json`
included, has a `.sha` sibling holding the SHA-1 of its bytes as forty
hexadecimal digits and a line feed. A reader watches a document for changes
through that small sibling, so no JSON document is written without one; the
KJV inspection fails on any that lacks or mismatches it.

Every source book with publishable content is retained, including books with
only titles or introductions. Existing addresses remain stable: Genesis is 1,
Matthew 40, Revelation 66, and established additional books continue to 89.
`src/book_identity.py` recognizes source OSIS identifiers independently of display
names, then verified configured name aliases when OSIS is absent. Thus native
`I Enoch`, the older `1 Enoch` spelling and OSIS `1En` resolve to book 87.

Previously unseen OSIS identities receive numbers in the reserved range
1,000,000–281,474,977,710,655. The address is 1,000,000 plus the unsigned first six
bytes of SHA-256 over UTF-8 `getbible-book/v1\0` followed by the normalized identity
key (for example `osis:newbook`; `\0` denotes one NUL byte). OSIS identity keys use
Unicode NFC, collapsed whitespace and case folding, with numeric chapter/verse
suffixes removed. Dotted work identifiers remain intact. These numbers are exact
in JavaScript and do not depend on module order, display names or the selected
catalog. When OSIS is absent, an unrecognized source name supplies a `name:` key;
an abbreviation is the final fallback. Such fallback identities necessarily
depend on the supplied name or abbreviation because no independent identity exists.

Number collisions, contradictory known identities, or distinct native book
positions claiming the same address raise a diagnostic instead of silently
merging or dropping content. Extension-number derivation is a versioned address
contract: future compatibility mappings must preserve addresses already assigned
by it. Empty canon positions do not create phantom books or chapters. Each
translation's `books.json` lists every emitted book, including books with an empty
`chapters` array when their own introduction or title is the supplied content.

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
checksum index and `.sha` text, and the verse, token, span, editorial, title,
introduction, reference, anchor, and recursive content objects they contain. A change to what the converter or hasher
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

`order` is a contiguous zero-based integer across every editorial
entry in chapter reading order.

A heading keeps these required fields:

- `order`;
- `type: "heading"`;
- `anchor.verse`, an emitted verse number in the current chapter;
- `anchor.edge: "before"`;
- `text`;
- `heading_type`, copied from the source title type in OSIS-equivalent semantics, or `unspecified` when the
  source supplies no type;
- `canonical`, which is `true` only when the source OSIS explicitly marks the
  title canonical and is otherwise `false`.

A heading can additionally retain `content`, string-valued source `attrs`,
`subtype`, and paired `tokens`/`spans`. Their positions address the heading text,
not the associated verse. Existing heading anchors do not change.

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
paragraph entries. The entire `editorial` field is omitted when a chapter has no editorial entries.

## Full study content and references

`editorial` also admits `footnote` and `structure` entries. A footnote contains
`order`, `type: "footnote"`, a deterministic chapter-local `id`, `anchor`, `text`,
`content`, and optional string-valued `attrs`. Internal headings and paragraphs
remain inside the note; they never become main-text headings or paragraph starts.
A structure contains `order`, `type: "structure"`, `anchor`, and `content`. This
preserves poetry, tables, figures, and otherwise unrecognized source elements.

A flat segment of complete lexical words, such as an OSIS variant reading, uses
the existing verse `spans` array: `tag: "seg"`, every segment attribute, its source
text, and the inclusive token range. The tokens retain each word's lexical data.
When this represents the complete segment, no duplicate structure tree is needed.
Empty segments, mixed prose, nested markup, and other details not represented by
that span retain their source content. Unresolved display positions remain `0`
in the existing word ranges; source text and token ranges are still retained.
Likewise, an empty terminal book, chapter, or verse marker can retain just its ordered
marker content at a verified Unicode end offset when the preceding lexical data
is already represented. If the endpoint or surrounding source structure cannot
be represented faithfully, the complete source tree remains present.

`content` is an ordered recursive array of strings or elements with `tag`,
optional string-valued `attrs`, and `children` (another content array). It retains
source structure and metadata as data, not executable HTML. Consumers must render
supported tags deliberately. Unknown constructs are preserved and the build
reports unsupported interpretation instead of silently dropping them.

Cross-reference bodies and destinations occur only in a chapter's optional
`reference` object, whose `items` array is in source order. Each item contains:

- `id`, a deterministic identifier local to the chapter;
- `anchor`, locating the occurrence in the source;
- `text`, its visible text;
- `targets`, an array retaining each source `value` and `scheme` (`osis`, `uri`,
  `local`, or `unresolved`);
- optional `content` and string-valued source `attrs`.

Safely resolved Scripture targets add `book`, optional `chapter` and `verse`,
and optional inclusive `end` with its own book/chapter/verse. Book addresses use
the existing GetBible identity mapping. The original target remains present when
resolution is incomplete; no destination is guessed. A consumer must account for
the source translation's versification before navigating another translation.

Content nodes link to cross-reference entries through `attrs.reference_id`.
Nested-note placeholders link through `attrs.footnote_id` and have empty
`children`; each full note body has its own editorial entry. IDs are stable for
repeated builds of identical source, not permanent across upstream source edits.

Study anchors differ deliberately from existing heading and token addresses:

- Normally `{ "verse": 1, "offset": 4 }` locates a zero-based Unicode code-point
  position in the published verse `text`. The end-of-text position is valid.
- An unalignable occurrence uses `{ "verse": 1, "alignment": "unresolved" }`
  and omits `offset`. A consumer must not invent a character position.
- A reference inside a footnote additionally carries `note` (the footnote ID)
  and `note_offset`, a zero-based code-point position in that footnote's `text`.
- A chapter-introduction occurrence uses `scope: "introduction"`, `verse: 0`,
  and an `introduction` index. Its offset addresses that introduction entry's
  text. These are study anchors, never new scripture verse addresses.
- An original verse with no public display text can retain study material through
  `scope: "source"` and `alignment: "unresolved"`; its source verse number is
  preserved without manufacturing a public verse or an offset.

Footnote and structure entries sort after headings and paragraph starts at each
verse, then by offset and source order. Unresolved offsets sort with offset zero without implying a known text position.
Chapter introductions sort before scripture verses. Existing paragraph coverage
and heading-before-verse semantics remain unchanged. A chapter with no published
verses can retain footnote/structure editorial entries and references, alongside
its introduction/title metadata, without obtaining a standalone chapter file.

The same editorial and reference data appear in the chapter nested in translation
and book documents and in its standalone chapter document. `chapters.json`
retains them as chapter metadata. No reference list is added to verse fields or
mixed into editorial. The schemas and the generated OpenAPI description carry
this additive contract with every built tree.

## Verse and book-title semantics

Every emitted verse keeps the established fields:

```json
{"chapter":1,"verse":1,"name":"Genesis 1:1","text":"In the beginning..."}
```

`text` never begins with `LF` (`\n`), `CR` (`\r`), or a `CRLF` pair. Builder
removes repeated leading line endings introduced by source paragraph formatting
but preserves line endings that occur inside the verse.

Supported source formats map into OSIS-equivalent semantics and compact, optional fields:

- `paragraph: true` means the verse begins a paragraph;
- `tokens` retains word-level lexical attributes and visible word positions;
- `spans` retains supported annotations over token and visible-word ranges.

The book object may contain an ordered `titles` list for title metadata belonging
to that book. A book title contains `text`, its OSIS `type` when supplied,
optional `canonical` and `subtype` values, and title-local `tokens`/`spans` when
word markup is available. Optional `content` and `attrs` preserve title structure
and source attributes.

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
string as introduction prose. Each introduction may add `content`, preserving
source paragraphs, annotations and relationships within the same entry. A
note-only introduction can have empty `text` only when its `content` is non-empty,
keeping a source location without inventing prose.

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
- raw/rendered/stripped byte projections and intermediate `normalized_raw`;
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
- Every JSON document has a `.sha` sibling holding the SHA-1 of its bytes.
- Every source book with text, titles or introductions is retained. Known OSIS
  identities and verified name aliases preserve established addresses; new
  identities receive deterministic extension numbers. A collision or conflicting
  source identity fails explicitly instead of silently merging books.
- Declared books and chapters with verses must have their standalone files;
  missing files fail indexing rather than producing an incomplete tree.
- A publication repository is reset to its preserved files after it is cloned
  or pulled, so files an earlier build published are not carried forward.
- `openapi.json` is generated from the hashed tree on every build, never
  hand-edited, and the hasher never treats it as a translation.
- Valid upstream content growth is accepted without comparison to an older build.
- Files at or above 95 MiB fail before hashing or publication.
- Symlinks and special files in generated output fail validation.
- Scripture output is validated and published before its derived hash repository.
- A Git failure exits the workflow nonzero. Permanent remote rejections such as
  GitHub `GH001` are not retried.

The manual `Inspect fresh KJV API output` workflow builds KJV from a fresh module
download, validates the `editorial` object shapes, reference links, anchor bounds, and range coverage,
checks the generated tree description, prints bounded structural summaries and
representative records for Psalms, John, and Revelation chapters 1–5, and
applies the same envelope and size checks without publishing anything.
