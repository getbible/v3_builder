# Source-faithful study data

This work extends API3 additively. Existing paths, book numbers, verse fields,
heading and paragraph meanings, and token/span addressing remain stable.

The implementation preserves source headings and study content as follows:

- Chapter `editorial` continues to contain ordered headings and paragraph ranges.
  Footnote entries add stable chapter-local identifiers, source anchors, plain
  text and recursive content retaining internal headings, paragraphs and inline
  annotations. Other source structures can use additional editorial entry types.
- Optional chapter `reference` is an object with an ordered `items` collection.
  Cross-reference bodies and targets belong here, including references occurring
  inside a footnote. They are not placed in verse fields or mixed into editorial.
- New anchors use a verse number and zero-based Unicode code-point offset into
  that verse's published text. Note-local references additionally identify their
  note and offset within its text. Existing heading anchors remain unchanged.
- Source-format adapters map OSIS, ThML, GBF, TEI and plain text into common
  semantics. Source attributes and unrecognized constructs remain inspectable;
  unsupported interpretation produces deterministic diagnostics.
- Module encoding is respected. Exact extraction bytes remain distinct from
  normalized UTF-8 markup. Native SWORD attributes supplement the source rather
  than replacing it, and are captured before subsequent filtering mutates state.

Regression fixtures cover source-to-display alignment, quotation milestones,
nested annotations, note-local structure, lexical alternatives, source formats,
encoding, references and repeated-build determinism. Real-module validation and
JSON Schema validation protect the complete generated tree.
