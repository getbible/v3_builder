"""
OSIS XML Parser for extracting word-level data from SWORD Bible modules.

Parses raw OSIS markup (from pysword's get(clean=False)) and extracts
structured data using the token + span model (standoff annotation pattern).

Returns {"tokens": [...], "spans": [...]} where every token and every
span carries both a 1-based word-position range (anchored in the verse's
clean text) and — for spans — a token-index range (anchored in the
tokens array).

Schema
------

Tokens (ordered by position in the verse):
    {
      "token":        "Lord",                   # word text as it appears
      "lemma":        "strong:H03068",          # intrinsic <w> attributes
      "morph":        "strongMorph:TH8799",     # (preserved as-is)
      "src":          "7 8",                    # optional
      "gloss":        "Lord",                   # optional
      "xlit":         "Latn:Elohim",            # optional
      "type":         "x-split-1227",           # optional
      "subType":      "x-1",                    # optional
      "morphSegmented": true,                   # flag from <seg type=x-morph>
      "variant":      true,                     # flag from <seg type=x-variant>
      "variantType":  "x-1",                    # optional
      "word_start":   18,                       # 1-based whitespace-word range
      "word_end":     18                        # inclusive
    }

Spans (annotations over token ranges):
    {
      "tag":          "divineName",             # OSIS element name
      "span":         "Lord",                   # exact OSIS-marked text
      "attrs":        {"type": "added"},        # omitted when empty
      "word_start":   18,                       # 1-based whitespace-word range
      "word_end":     18,                       # inclusive
      "token_start":  7,                        # index into tokens[]
      "token_end":    7                         # inclusive
    }

Dual addressing rationale
-------------------------

- `word_start` / `word_end` give a locator that corresponds 1:1 to what a
  reader sees in the verse `text` field (counted by splitting on
  whitespace). Works across scripts: Hebrew, Greek, Latin, Afrikaans
  (diacritics), Arabic — anywhere words are separated by whitespace.

- `token_start` / `token_end` give a locator into the tokens array for
  consumers doing lemma-aware work (Strong's groups, morphology). One
  token may cover multiple whitespace-words when several English words
  translate a single Hebrew/Greek morpheme.

Consumers that just want to highlight a span in the text use
`word_start`/`word_end`. Consumers that want the original-language
metadata use `token_start`/`token_end`.

OSIS element handling
---------------------

Word elements:
    <w> with lemma, morph, src, gloss, xlit, n, type, subType attributes

Segment elements:
    <seg> with type variants: x-variant (subType x-1/x-2), x-morph,
    x-transChange (subType x-added), x-caps, x-nested

Context elements (become spans):
    <divineName>   - divine name markers (LORD, GOD, YAH)
    <transChange>  - translator additions (type: added, deleted, tpiAdded)
    <hi>           - highlighting (type: bold, italic, small-caps, etc.)
    <q>            - quotations (who, level, marker attributes)
    <foreign>      - foreign language text
    <inscription>  - inscriptions
    <name>         - proper names (type: person, geographic, etc.)
    <speaker>      - speaker identification
    <number>       - numeric values (type: cardinal, ordinal, fractional)
    <unit>         - units of measurement

Skip elements (content excluded, matching pysword OSISCleaner):
    <note>, <milestone>, <title>, <abbr>, <catchWord>, <index>,
    <rdg>, <rdgGroup>, <figure>
"""

import re
import xml.etree.ElementTree as ET


# Tags whose content should be completely excluded from output
# (matching pysword OSISCleaner remove-content behavior)
SKIP_TAGS = frozenset({
    'note', 'milestone', 'title', 'abbr', 'catchWord',
    'index', 'rdg', 'rdgGroup', 'figure'
})

# Tags that produce spanning annotations.
# Maps OSIS tag name to a function that extracts span attrs from the element.
_SPAN_TAGS = {
    'divineName': lambda el: {},
    'transChange': lambda el: {'type': el.get('type', 'added')},
    'hi': lambda el: {'type': el.get('type', '')} if el.get('type') else {},
    'q': lambda el: {
        k: v for k, v in [
            ('who', el.get('who', '')),
            ('level', el.get('level', '')),
            ('marker', el.get('marker')),
        ] if v is not None and v != ''
    },
    'foreign': lambda el: {
        k: v for k, v in [('n', el.get('n', ''))] if v
    },
    'inscription': lambda el: {},
    'name': lambda el: {'type': el.get('type', '')} if el.get('type') else {},
    'speaker': lambda el: {},
    'number': lambda el: {'type': el.get('type', '')} if el.get('type') else {},
    'unit': lambda el: {'type': el.get('type', '')} if el.get('type') else {},
}


def parse_osis_verse(raw_text, clean_text=None):
    """
    Parse raw OSIS markup and return token+span structured data.

    Args:
        raw_text: Raw OSIS XML string from pysword get(clean=False).
        clean_text: Optional clean verse text from pysword get(clean=True).
            When provided, token and span word positions are anchored to
            this text (so consumers can verify them by whitespace-splitting
            the verse's `text` field). When omitted, word positions are
            derived from joining tokens with single spaces.

    Returns:
        Dict with 'tokens' and 'spans' lists, or None if no word-level
        markup is found or parsing fails.

        Every token carries word_start/word_end (1-based, inclusive).
        Every span carries word_start/word_end (1-based, inclusive) AND
        token_start/token_end (0-based index range into tokens[]).
    """
    if not raw_text or '<w ' not in raw_text:
        return None

    xml_str = '<r>' + raw_text + '</r>'
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        escaped = re.sub(r'&(?!amp;|lt;|gt;|apos;|quot;|#)', '&amp;', raw_text)
        xml_str = '<r>' + escaped + '</r>'
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return None

    tokens = []
    spans = []
    _walk(root, tokens, spans)

    if not tokens:
        return None

    if clean_text is None:
        clean_text = ' '.join(t['token'] for t in tokens)
    _assign_word_positions(clean_text, tokens, spans)

    return {'tokens': tokens, 'spans': spans}


def _walk(element, tokens, spans):
    """
    Recursively walk the XML tree building tokens and spans.

    For span-producing elements, records the token count before and after
    recursing into children, then emits a span covering that range.
    """
    tag = _strip_ns(element.tag)

    if tag in SKIP_TAGS:
        return

    # Check if this element opens a span
    span_maker = _SPAN_TAGS.get(tag)

    if tag == 'w':
        _emit_token(element, tokens, spans)
        return

    # <transChange> without <w> descendants → standalone token + span
    if tag == 'transChange' and not _has_descendant(element, 'w'):
        text = _full_text(element).strip()
        if text:
            idx = len(tokens)
            tokens.append({'token': text})
            span = {
                'tag': 'transChange',
                'span': text,
                'token_start': idx,
                'token_end': idx,
            }
            attrs = {'type': element.get('type', 'added')}
            if attrs:
                span['attrs'] = attrs
            spans.append(span)
        return

    # <seg> standalone without <w> children or child elements → token + span
    if tag == 'seg' and not _has_descendant(element, 'w') and not list(element):
        text = _full_text(element).strip()
        if text:
            idx = len(tokens)
            tokens.append({'token': text})
            attrs = {}
            seg_type = element.get('type', '')
            if seg_type:
                attrs['type'] = seg_type
            seg_sub = element.get('subType', '')
            if seg_sub:
                attrs['subType'] = seg_sub
            span = {
                'tag': 'seg',
                'span': text,
                'token_start': idx,
                'token_end': idx,
            }
            if attrs:
                span['attrs'] = attrs
            spans.append(span)
        return

    # Record token count before recursing
    start_idx = len(tokens)

    # Recurse into children
    for child in element:
        _walk(child, tokens, spans)

    # Close span if this element opened one and tokens were added
    if span_maker is not None and len(tokens) > start_idx:
        end_idx = len(tokens) - 1
        span = {
            'tag': tag,
            'span': _full_text(element).strip(),
            'token_start': start_idx,
            'token_end': end_idx,
        }
        attrs = span_maker(element)
        if attrs:
            span['attrs'] = attrs
        spans.append(span)


def _emit_token(w_elem, tokens, spans):
    """
    Create tokens from a <w> element, plus spans for nested context elements.

    When a sub-<w> span-producing element (<divineName>, <hi type=...>,
    <seg type="x-transChange">) wraps only part of the <w>'s text, the
    <w> is split into multiple tokens so the span can target the exact
    sub-range. This prevents unrelated translator text (e.g. leading
    conjunctions like "And") from being incorrectly tagged as divineName
    when the OSIS source is:

        <w lemma="strong:H03068">And the <divineName>Lord</divineName></w>

    Each emitted sub-token inherits the <w>'s intrinsic attributes (lemma,
    morph, src, gloss, xlit, type, subType). Consumers who want the full
    Strong's-group translation can concatenate adjacent tokens that share
    a lemma; consumers who want to render the divine name accurately use
    the divineName span, which now covers only the marked sub-range.

    Intrinsic <w> attributes go on every sub-token.
    Nested <seg type="x-morph"> and <seg type="x-variant"> do NOT split
    the <w>; they remain as token-level markers (morphSegmented, variant).
    """
    # Gather intrinsic attributes shared by all sub-tokens. Multi-valued
    # OSIS attributes (lemma, morph, xlit, src) are transformed into
    # structured shapes — see _transform_intrinsic — so consumers never
    # have to re-parse space-delimited, scheme-prefixed strings.
    intrinsic = {}
    for attr_name, attr_value in w_elem.attrib.items():
        name = _strip_ns(attr_name)
        if name and attr_value:
            intrinsic[name] = _transform_intrinsic(name, attr_value)

    # Collect token-level marker flags that do NOT cause splitting
    extras = {}
    for desc in w_elem.iter():
        if desc is w_elem:
            continue
        if _strip_ns(desc.tag) != 'seg':
            continue
        seg_type = desc.get('type', '')
        seg_sub = desc.get('subType', '')
        if seg_type == 'x-morph':
            extras['morphSegmented'] = True
        if 'x-variant' in seg_type:
            extras['variant'] = True
            if seg_sub:
                extras['variantType'] = seg_sub

    # Linearize <w>'s content into (text, sub_span_info | None) segments
    raw_segments = _linearize_w_content(w_elem)
    segments = _merge_adjacent_segments(raw_segments)

    for seg_text, sub_span in segments:
        text = seg_text.strip()
        if not text:
            continue
        idx = len(tokens)
        token = {'token': text}
        token.update(intrinsic)
        token.update(extras)
        tokens.append(token)
        if sub_span is not None:
            span_entry = {
                'tag': sub_span['tag'],
                'span': text,
                'token_start': idx,
                'token_end': idx,
            }
            attrs = sub_span.get('attrs') or {}
            if attrs:
                span_entry['attrs'] = attrs
            spans.append(span_entry)


# Sub-<w> span producers: elements that, when nested INSIDE a <w>,
# create a span over only the sub-range of the word text they enclose.
# Returning None means the element is not a sub-<w> span producer.
#
# The generic rule is "any element in _SPAN_TAGS produces a sub-<w> span
# when nested inside a <w>." This covers every OSIS 2.1.1 context element
# we promote to a span: divineName, transChange, hi, q, foreign,
# inscription, name, speaker, number, unit.
#
# <seg> has its own logic: only x-transChange / subType=x-added produce
# a sub-<w> span. Other seg variants (x-morph, x-variant, x-caps, etc.)
# are token-level markers handled elsewhere in _emit_token.
def _sub_w_span_info(elem):
    tag = _strip_ns(elem.tag)
    if tag == 'seg':
        seg_type = elem.get('type', '')
        seg_sub = elem.get('subType', '')
        if seg_type == 'x-transChange' or seg_sub == 'x-added':
            return {'tag': 'transChange', 'attrs': {'type': 'added'}}
        return None
    extractor = _SPAN_TAGS.get(tag)
    if extractor is None:
        return None
    return {'tag': tag, 'attrs': extractor(elem)}


def _linearize_w_content(w_elem):
    """
    Flatten the content of a <w> element into (text, span_info | None)
    segments in document order. Each segment's span_info indicates whether
    that text fragment falls inside a sub-<w> span-producing element.

    SKIP_TAGS descendants contribute no text.
    """
    segments = []

    def walk(elem, current_span):
        tag = _strip_ns(elem.tag)
        if tag in SKIP_TAGS:
            return
        # If this element produces a sub-<w> span, its direct text falls
        # into that new span; otherwise it inherits the caller's context.
        sub_span = _sub_w_span_info(elem)
        text_span = sub_span if sub_span is not None else current_span

        if elem.text:
            segments.append((elem.text, text_span))
        for child in elem:
            walk(child, text_span)
            if child.tail:
                # Tail text is emitted after the child closes, back in
                # the parent's (text_span) context — not the child's.
                segments.append((child.tail, text_span))

    # The <w> element itself is not span-producing; start with span=None.
    if w_elem.text:
        segments.append((w_elem.text, None))
    for child in w_elem:
        walk(child, None)
        if child.tail:
            segments.append((child.tail, None))
    return segments


def _merge_adjacent_segments(segments):
    """Coalesce adjacent segments that share the same span_info."""
    merged = []
    for text, span_info in segments:
        if merged and _same_span(merged[-1][1], span_info):
            merged[-1] = (merged[-1][0] + text, span_info)
        else:
            merged.append((text, span_info))
    return merged


def _same_span(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.get('tag') == b.get('tag') and a.get('attrs', {}) == b.get('attrs', {})


def _has_descendant(element, tag_name):
    """Check if element has any descendant with the given tag name."""
    for desc in element.iter():
        if desc is not element and _strip_ns(desc.tag) == tag_name:
            return True
    return False


# =============================================================================
# Intrinsic attribute shape transforms
#
# OSIS packs multi-valued attributes into space-delimited strings:
#   lemma="strong:H03027 strong:H0931"
#   morph="oshm:HTp oshm:HVqp3ms"
#   xlit="Latn:Elohim"
#   src="7 8"
#
# Returning those as strings forces every API consumer to re-implement the
# same split-by-space, split-by-colon, group-by-scheme logic and to handle a
# type union (sometimes one value, sometimes many). We commit to the
# collection type up front:
#
#   lemma / morph / xlit → dict keyed by OSIS scheme, values as arrays
#   src                  → array of integers
#
# Scheme-less values (no colon in the token) fall under the key "default".
# This preserves data from any non-compliant OSIS source without losing it.
# =============================================================================

def _parse_scheme_grouped(value):
    """
    Parse a space-delimited, scheme-prefixed OSIS attribute into a dict
    keyed by scheme, with values as arrays.

    Examples:
        "strong:H03027 strong:H0931"
            → {"strong": ["H03027", "H0931"]}
        "strong:G520 lemma.TR:apostle"
            → {"strong": ["G520"], "lemma.TR": ["apostle"]}
        "Latn:Elohim"
            → {"Latn": ["Elohim"]}

    Order within each scheme is preserved. Values without a scheme prefix
    (malformed OSIS) are grouped under the key "default" so no data is lost.
    """
    result = {}
    for part in value.split():
        if ':' in part:
            scheme, code = part.split(':', 1)
            if not scheme:
                scheme = 'default'
        else:
            scheme, code = 'default', part
        result.setdefault(scheme, []).append(code)
    return result


def _parse_space_ints(value):
    """
    Parse a space-delimited OSIS ``src`` attribute into a list of integers.

    Examples:
        "7"     → [7]
        "7 8"   → [7, 8]
        "5 6"   → [5, 6]

    If any part is not a valid integer, the whole value falls back to a
    list of strings so array element types stay homogeneous.
    """
    parts = value.split()
    try:
        return [int(p) for p in parts]
    except ValueError:
        return parts


# Map from intrinsic attribute name to its shape transformer. Any <w>
# attribute not in this map is kept as a plain string.
_INTRINSIC_TRANSFORMS = {
    'lemma': _parse_scheme_grouped,
    'morph': _parse_scheme_grouped,
    'xlit': _parse_scheme_grouped,
    'src': _parse_space_ints,
}


def _transform_intrinsic(name, value):
    """Apply the shape transform for a given intrinsic attribute name,
    or return the value unchanged when no transform is registered."""
    transform = _INTRINSIC_TRANSFORMS.get(name)
    if transform is None:
        return value
    return transform(value)


def _full_text(element):
    """
    Get all text content from an element and its descendants,
    excluding content from SKIP_TAGS elements.
    """
    parts = []
    _collect_text(element, parts)
    return ''.join(parts)


def _collect_text(element, parts):
    """Recursively collect text, skipping content from excluded tags."""
    tag = _strip_ns(element.tag)
    if tag in SKIP_TAGS:
        return
    if element.text:
        parts.append(element.text)
    for child in element:
        _collect_text(child, parts)
        if child.tail:
            parts.append(child.tail)


def _strip_ns(tag):
    """Strip XML namespace prefix from tag name."""
    if tag and '}' in tag:
        return tag.split('}', 1)[1]
    return tag or ''


# =============================================================================
# Word-position alignment
# =============================================================================

def _assign_word_positions(clean_text, tokens, spans):
    """
    Assign 1-based whitespace-word positions to every token and span.

    Each whitespace-separated run of non-space characters in clean_text is
    one word. Word 1 is the first such run. Punctuation attached to a word
    (e.g. "Lord,") is part of that word — consumers who want just "Lord"
    can trim trailing punctuation on render.

    Algorithm
    ---------

    1. Index clean_text's whitespace-words: for each word, record its
       (char_start, char_end, word_number).
    2. For each token, substring-match its text against clean_text starting
       from the last matched position. Map the matched char range to a
       word-number range.
    3. For each span, locate its ``span`` text in clean_text (starting near
       the first token's char position). This catches OSIS elements like
       <q> whose marked text extends past the last <w> into trailing
       non-tokenized text (e.g. "…know not of." where "of." lies outside
       any <w>). Falls back to the token-derived range when the span's
       text can't be matched.

    Degenerate cases (text not found in clean_text) fall back to
    word_start = word_end = 0. This is rare and signals an OSIS/clean-text
    mismatch worth investigating, not silently corrupted data.
    """
    # Build (char_start, char_end, word_number) for each whitespace-word
    word_ranges = []
    for match in re.finditer(r'\S+', clean_text):
        word_ranges.append((match.start(), match.end(), len(word_ranges) + 1))

    # Align tokens to clean_text and remember each token's char range so
    # span alignment can use it as a search anchor.
    token_char_ranges = []
    cursor = 0
    for token in tokens:
        tok_text = token['token']
        idx = clean_text.find(tok_text, cursor)
        if idx == -1:
            token_char_ranges.append((-1, -1))
            token['word_start'] = 0
            token['word_end'] = 0
            continue
        char_end = idx + len(tok_text)
        token_char_ranges.append((idx, char_end))
        ws, we = _char_range_to_words(idx, char_end, word_ranges)
        token['word_start'] = ws
        token['word_end'] = we
        cursor = char_end

    for span in spans:
        ts = span.get('token_start', -1)
        te = span.get('token_end', -1)
        if not (0 <= ts < len(tokens) and 0 <= te < len(tokens)):
            span['word_start'] = 0
            span['word_end'] = 0
            continue

        first_char, _ = token_char_ranges[ts]
        _, last_char = token_char_ranges[te]

        # Prefer locating the span's exact text in clean_text — this
        # captures any non-tokenized text inside the OSIS element
        # (e.g. trailing "of." that falls outside every <w> but still
        # sits inside the <q>…</q>).
        char_start = char_end = -1
        span_text = span.get('span', '')
        if span_text and first_char >= 0:
            char_start, char_end = _locate_span_chars(
                clean_text, span_text, first_char, last_char
            )

        # Fall back to the token-derived char range when the span's text
        # cannot be matched directly against the clean text.
        if char_start < 0:
            char_start = first_char
            char_end = last_char

        ws, we = _char_range_to_words(char_start, char_end, word_ranges)
        span['word_start'] = ws
        span['word_end'] = we


def _char_range_to_words(char_start, char_end, word_ranges):
    """Map an inclusive-start, exclusive-end char range to a 1-based
    inclusive whitespace-word range. Returns (0, 0) when the range is
    empty or out of bounds."""
    if char_start < 0 or char_end < 0:
        return (0, 0)
    ws = we = 0
    for w_start, w_end, w_num in word_ranges:
        if w_end <= char_start:
            continue
        if w_start >= char_end:
            break
        if ws == 0:
            ws = w_num
        we = w_num
    return (ws, we)


def _locate_span_chars(clean_text, span_text, first_token_char, last_token_char):
    """
    Locate ``span_text`` in ``clean_text`` preferring the occurrence that
    brackets the token range.

    Returns (char_start, char_end), or (-1, -1) if no plausible match is
    found. ``span_text`` is matched with collapsed internal whitespace to
    tolerate minor formatting differences between the OSIS source and the
    clean verse text.
    """
    if not span_text or not clean_text:
        return (-1, -1)

    # Exact match first (cheap path)
    search_from = max(0, first_token_char - len(span_text))
    idx = clean_text.find(span_text, search_from)
    while idx != -1:
        end = idx + len(span_text)
        # Match must bracket the token range
        if idx <= first_token_char and end >= last_token_char:
            return (idx, end)
        idx = clean_text.find(span_text, idx + 1)

    # Whitespace-normalized match (tolerant of line-break/indent differences)
    collapsed_span = re.sub(r'\s+', ' ', span_text).strip()
    if not collapsed_span:
        return (-1, -1)
    # Build a regex that matches the span text with any whitespace run
    escaped = re.escape(collapsed_span).replace(r'\ ', r'\s+')
    for match in re.finditer(escaped, clean_text):
        if match.start() <= first_token_char and match.end() >= last_token_char:
            return (match.start(), match.end())

    return (-1, -1)
