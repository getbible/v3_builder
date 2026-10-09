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
      "lemma":        {"strong": ["H03068"]},   # intrinsic <w> attributes
      "morph":        {"strongMorph": ["TH8799"]},
      "src":          [7, 8],                    # optional
      "gloss":        "Lord",                   # optional
      "xlit":         {"Latn": ["Elohim"]},      # optional
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

Skip elements (content excluded from verse tokens, matching pysword
OSISCleaner):
    <note>, <milestone>, <title>, <abbr>, <catchWord>, <index>,
    <rdg>, <rdgGroup>, <figure>

Semantic elements:
    <milestone type="x-p">, opening <p> elements, and CrossWire's opening
    <div type="x-p|paragraph"> milestones mark paragraph starts.
    <title> elements are exposed separately with their visible text, type,
    canonical flag, and any word-level token/span data they contain.
    <chapter chapterTitle="..."> supplies a chapter title when no equivalent
    <title type="chapter"> element is present.
"""

from difflib import SequenceMatcher
import json
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

_DIV_PARAGRAPH_TYPES = frozenset({'x-p', 'paragraph'})


def parse_osis_verse(raw_text, clean_text=None, state=None, diagnostics=None):
    """Parse OSIS using source positions, retaining the existing token/span shape.

    ``clean_text`` is the display projection to which whitespace-word positions
    refer. Without it the complete visible source text, including unmarked text,
    supplies those positions. ``state`` may be a dictionary retained between
    consecutive entries of one book; its ``quotes`` list preserves open OSIS
    quotation milestones. Callers must reset it when changing books/modules.
    Optional ``diagnostics`` receives structured parsing/alignment problems;
    diagnostics and private source coordinates never enter public annotations.
    """
    if not raw_text:
        return None
    root = _parse_osis_fragment(raw_text)
    if root is None:
        _diagnose(diagnostics, 'invalid_osis', 'OSIS fragment could not be parsed')
        return None

    source, ranges = _source_layout(root)
    tokens, spans = [], []
    _walk(root, tokens, spans, source, ranges)
    _quotation_milestones(root, source, ranges, tokens, spans, state, diagnostics)
    if not tokens:
        return None
    if clean_text is None:
        clean_text = source
    _assign_word_positions(clean_text, tokens, spans, source, diagnostics)
    return {'tokens': tokens, 'spans': spans}


def _diagnose(diagnostics, code, message, **details):
    if diagnostics is not None:
        diagnostics.append({'code': code, 'message': message, **details})


def parse_osis_semantics(raw_text):
    """Extract compact structural semantics from an OSIS entry.

    The lossless GetBibleSWORD byte envelopes are build inputs rather than API
    fields.  This projection keeps the small pieces a reader needs after those
    envelopes are discarded:

    - ``paragraph`` is true when the entry starts a paragraph through an OSIS
      ``x-p`` milestone, an opening ``p`` element, or CrossWire's opening
      ``div`` milestones with type ``x-p`` or ``paragraph``;
    - ``titles`` contains ordered title objects with normalized visible text,
      semantic type, optional canonical/subtype metadata, and word-level data;
    - ``chapterTitle`` is promoted to an equivalent chapter title when a
      module supplies only the chapter milestone attribute.

    Invalid XML is ignored here.  Structural enrichment is additive and must
    not prevent a validated entry with a usable stripped projection from being
    emitted.
    """

    if not raw_text:
        return {}
    root = _parse_osis_fragment(raw_text)
    if root is None:
        return {}

    semantic = {}
    titles = []
    seen_titles = set()
    chapter_titles = []

    for element in _main_text_elements(root):
        tag = _strip_ns(element.tag)
        if _is_paragraph_start(element, tag):
            semantic['paragraph'] = True
        elif tag == 'title':
            title = _semantic_title(element)
            if title is not None:
                identity = _title_identity(title)
                if identity not in seen_titles:
                    seen_titles.add(identity)
                    titles.append(title)
        elif tag == 'chapter' and element.get('eID') is None:
            chapter_title = _normalize_visible_text(
                element.get('chapterTitle', '')
            )
            if chapter_title:
                chapter_titles.append(chapter_title)

    for text in chapter_titles:
        fallback = {'type': 'chapter', 'text': text}
        if not any(title.get('type') == 'chapter' and title['text'] == text
                   for title in titles):
            seen_titles.add(_title_identity(fallback))
            titles.append(fallback)

    if titles:
        semantic['titles'] = titles
    return semantic


def _is_paragraph_start(element, tag):
    """Return whether *element* is an opening OSIS paragraph boundary.

    CrossWire modules use both container elements and milestone pairs.  A
    closing milestone carries ``eID`` and must never start a second paragraph.
    """

    if element.get('eID') is not None:
        return False
    element_type = (element.get('type') or '').strip().lower()
    if tag == 'milestone':
        return element_type == 'x-p'
    if tag == 'p':
        return True
    return tag == 'div' and element_type in _DIV_PARAGRAPH_TYPES


def _semantic_title(element):
    """Build one compact title object from a parsed OSIS title element."""

    text = _normalize_visible_text(_semantic_visible_text(element))
    if not text:
        return None

    title = {}
    title_type = element.get('type')
    if title_type:
        title['type'] = title_type
    title['text'] = text
    if element.attrib:
        title['attrs'] = dict(element.attrib)
    if list(element):
        title['content'] = _semantic_content(element)

    canonical = _xml_boolean(element.get('canonical'))
    if canonical is not None:
        title['canonical'] = canonical
    subtype = element.get('subType')
    if subtype:
        title['subtype'] = subtype

    # A title has its own coordinate space. Its containing title tag must not
    # trigger the ordinary verse policy that excludes title contents.
    wrapper = ET.Element('r')
    wrapper.text = element.text
    wrapper.extend(list(element))
    source, ranges = _source_layout(wrapper, _SEMANTIC_TEXT_SKIP_TAGS)
    tokens, spans = [], []
    for child in wrapper:
        _walk(child, tokens, spans, source, ranges)
    if tokens:
        _assign_word_positions(text, tokens, spans, source)
        title['tokens'] = tokens
        title['spans'] = spans
    return title


def _semantic_content(element):
    """Preserve nested title text and metadata as semantic JSON nodes."""
    output = [element.text] if element.text else []
    for child in element:
        node = {'tag': child.tag, 'children': _semantic_content(child)}
        if child.attrib:
            node['attrs'] = dict(child.attrib)
        output.append(node)
        if child.tail:
            output.append(child.tail)
    return output


def _span_attributes(element):
    """Keep source attributes alongside existing interpreted/default values.

    Namespace-qualified names remain distinct, so xml:lang or extension attrs
    cannot overwrite an unrelated unqualified source attribute.
    """
    attrs = _SPAN_TAGS[_strip_ns(element.tag)](element)
    for name, value in element.attrib.items():
        attrs.setdefault(name, value)
    return attrs


def _title_identity(title):
    """Return the semantic identity used to suppress duplicate milestones."""

    return (
        title.get('type'),
        title.get('text'),
        title.get('canonical'),
        title.get('subtype'),
        json.dumps(title.get('attrs', {}), sort_keys=True, ensure_ascii=False),
        json.dumps(title.get('content', []), sort_keys=True, ensure_ascii=False),
    )


def _xml_boolean(value):
    """Parse the standard XML boolean spellings, ignoring unknown values."""

    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {'true', '1', 'yes'}:
        return True
    if normalized in {'false', '0', 'no'}:
        return False
    return None


def osis_plain_text(raw_text):
    """Return the visible text of an OSIS fragment, or ``None`` if invalid.

    This is a conservative fallback for the rare case where SWORD's stripped
    projection is not valid UTF-8 while the authoritative raw OSIS remains
    valid.  Elements excluded by the existing parser policy (notes, titles,
    figures, readings, and milestones) do not contribute display text.
    """

    if not raw_text:
        return ""
    root = _parse_osis_fragment(raw_text)
    if root is None:
        return None
    return _full_text(root)


def _parse_osis_fragment(raw_text):
    """Parse an OSIS fragment using the same entity recovery everywhere."""

    xml_str = '<r>' + raw_text + '</r>'
    try:
        return ET.fromstring(xml_str)
    except ET.ParseError:
        escaped = re.sub(r'&(?!amp;|lt;|gt;|apos;|quot;|#)', '&amp;', raw_text)
        try:
            return ET.fromstring('<r>' + escaped + '</r>')
        except ET.ParseError:
            return None


def _main_text_elements(element):
    """Visit structure without promoting structure inside notes or variants."""
    if _strip_ns(element.tag) in {'note', 'figure', 'index', 'rdg', 'rdgGroup'}:
        return
    yield element
    if _strip_ns(element.tag) == 'title':
        return
    for child in element:
        yield from _main_text_elements(child)


def _source_layout(root, skip_tags=SKIP_TAGS):
    """Return visible source text and exact element character intervals.

    Recording unmarked text as well as markup is essential: searching for an
    isolated token such as 'he' can otherwise select the substring in 'the'.
    Intervals intentionally allow multiple subwords to share one display word.
    """
    parts, ranges, offset = [], {}, 0

    def append(text):
        nonlocal offset
        if text:
            parts.append(text)
            offset += len(text)

    def visit(element):
        if _strip_ns(element.tag) in skip_tags:
            ranges[element] = (offset, offset)
            return
        start = offset
        append(element.text)
        for child in element:
            visit(child)
            append(child.tail)
        ranges[element] = (start, offset)

    visit(root)
    return ''.join(parts), ranges


def _trim_interval(source, start, end):
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return start, end


def _span_from_range(info, start, end, source, tokens):
    start, end = _trim_interval(source, start, end)
    indices = [i for i, token in enumerate(tokens)
               if token['_source_end'] > start and token['_source_start'] < end]
    if not indices or start >= end:
        return None
    span = {'tag': info['tag'], 'span': source[start:end],
            'token_start': indices[0], 'token_end': indices[-1],
            '_source_start': start, '_source_end': end}
    if info.get('attrs'):
        span['attrs'] = info['attrs']
    return span


def _ordinary_lexical_word(element):
    return (
        element.tag == 'w' and not len(element)
        and bool((element.text or '').strip())
        and element.text == element.text.strip()
        and all(value and '}' not in name and name not in {
            'token', 'word_start', 'word_end', 'morphSegmented',
            'variant', 'variantType', '_source_start', '_source_end',
        } for name, value in element.attrib.items())
    )


def _flat_lexical_segment(element):
    """Whether a segment can be reconstructed from one span and its words.

    Bare prose, nested markup, and empty words need the retained source tree.
    Whitespace between complete lexical words remains in the span's text.
    """
    return (
        element.tag == 'seg'
        and element.get('{http://www.w3.org/XML/1998/namespace}space') != 'preserve'
        and bool(len(element))
        and not (element.text or '').strip()
        and all(_ordinary_lexical_word(child)
                and not (child.tail or '').strip()
                for child in element)
    )


def _walk(element, tokens, spans, source, ranges):
    """Collect lexical tokens and every independently nested annotation."""
    tag = _strip_ns(element.tag)
    if tag in SKIP_TAGS or element not in ranges:
        return
    if tag == 'q' and (element.get('sID') or element.get('eID')):
        return  # These ranges are paired in document order after this walk.
    maker = _SPAN_TAGS.get(tag)
    if tag == 'w' or (
        (maker is not None or tag == 'seg')
        and not _has_descendant(element, 'w')
    ):
        _emit_token(element, tokens, spans, source, ranges)
        return
    for child in element:
        _walk(child, tokens, spans, source, ranges)
    if maker is not None or _flat_lexical_segment(element):
        attrs = dict(element.attrib) if tag == 'seg' else _span_attributes(element)
        span = _span_from_range({'tag': tag, 'attrs': attrs},
                                *ranges[element], source, tokens)
        if span:
            spans.append(span)


def _emit_token(element, tokens, spans, source, ranges):
    """Split lexical groups at annotation boundaries, preserving all layers.

    Every piece retains the containing word's lexical attributes. Each nested
    annotation is emitted once over its complete interval, so an italic divine
    name remains both italic and divine, including when inner markup subdivides
    only part of the outer annotation.
    """
    intrinsic = {}
    is_word = _strip_ns(element.tag) == 'w'
    if is_word:
        intrinsic = {_strip_ns(name): _transform_intrinsic(_strip_ns(name), value)
                     for name, value in element.attrib.items() if value}
    extras, annotations = {}, []
    start, end = ranges[element]
    boundaries = {start, end}
    for desc in element.iter():
        if desc not in ranges:
            continue
        tag = _strip_ns(desc.tag)
        if tag == 'seg':
            seg_type, seg_sub = desc.get('type', ''), desc.get('subType', '')
            if seg_type == 'x-morph':
                extras['morphSegmented'] = True
            if 'x-variant' in seg_type:
                extras['variant'] = True
                if seg_sub:
                    extras['variantType'] = seg_sub
        if desc is element and is_word:
            continue
        if tag == 'q' and (desc.get('sID') or desc.get('eID')):
            boundaries.add(ranges[desc][0])
            continue
        info = _sub_w_span_info(desc)
        if desc is element and tag == 'seg' and info is None:
            info = {'tag': 'seg', 'attrs': {k: v for k, v in desc.attrib.items()
                                          if k in {'type', 'subType'}}}
        if info is not None:
            lo, hi = ranges[desc]
            boundaries.update((lo, hi))
            annotations.append((info, lo, hi))
    ordered = sorted(boundaries)
    for lo, hi in zip(ordered, ordered[1:]):
        lo, hi = _trim_interval(source, lo, hi)
        if lo < hi:
            tokens.append({'token': source[lo:hi], **intrinsic, **extras,
                           '_source_start': lo, '_source_end': hi})
    for info, lo, hi in annotations:
        span = _span_from_range(info, lo, hi, source, tokens)
        if span:
            spans.append(span)


def _sub_w_span_info(element):
    tag = _strip_ns(element.tag)
    if tag == 'seg':
        if element.get('type') == 'x-transChange' or element.get('subType') == 'x-added':
            return {'tag': 'transChange', 'attrs': {**element.attrib, 'type': 'added'}}
        return None
    maker = _SPAN_TAGS.get(tag)
    return {'tag': tag, 'attrs': _span_attributes(element)} if maker is not None else None


def _quotation_milestones(root, source, ranges, tokens, spans, state, diagnostics):
    """Pair q milestones and emit verse-local ranges, retaining open context."""
    active = [dict(quote, start=0) for quote in (state or {}).get('quotes', [])]
    closed = []
    for element in root.iter():
        if element not in ranges or _strip_ns(element.tag) != 'q':
            continue
        start_id, end_id = element.get('sID'), element.get('eID')
        at = ranges[element][0]
        if end_id:
            match = next((q for q in reversed(active) if q['id'] == end_id), None)
            if match is None:
                _diagnose(diagnostics, 'unmatched_quote_end',
                          'Quotation end has no corresponding start', identifier=end_id)
            else:
                active.remove(match)
                closed.append((match, at))
        if start_id:
            previous = next((q for q in active if q['id'] == start_id), None)
            if previous is not None:
                _diagnose(diagnostics, 'duplicate_quote_start',
                          'Quotation identifier was opened twice', identifier=start_id)
                active.remove(previous)
                closed.append((previous, at))
            active.append({'id': start_id, 'attrs': _span_attributes(element), 'start': at})
    intervals = closed + [(quote, len(source)) for quote in active]
    if state is not None:
        state['quotes'] = [{'id': q['id'], 'attrs': q['attrs']} for q in active]
    elif active:
        _diagnose(diagnostics, 'open_quote',
                  'Quotation continues beyond this entry; retain state to continue it')

    # A quotation may have no lexical <w> tags (many translations do not).
    # Materialize only its otherwise unrepresented interval as a plain token.
    original_tokens = list(tokens)
    for quote, end in intervals:
        start, end = _trim_interval(source, quote['start'], end)
        if start < end and not any(t['_source_end'] > start and t['_source_start'] < end
                                   for t in tokens):
            tokens.append({'token': source[start:end], '_source_start': start,
                           '_source_end': end})
    if len(tokens) != len(original_tokens):
        tokens.sort(key=lambda token: (token['_source_start'], token['_source_end']))
        indices = {id(token): index for index, token in enumerate(tokens)}
        for span in spans:
            span['token_start'] = indices[id(original_tokens[span['token_start']])]
            span['token_end'] = indices[id(original_tokens[span['token_end']])]
    for quote, end in intervals:
        span = _span_from_range({'tag': 'q', 'attrs': quote['attrs']},
                                quote['start'], end, source, tokens)
        if span:
            spans.append(span)


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

def _parse_scheme_grouped(value, legacy_pipes=False):
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
    separator = r'[\s|]+' if legacy_pipes else r'\s+'
    for part in re.split(separator, value.strip()):
        if not part:
            continue
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
    'lemma': lambda value: _parse_scheme_grouped(value, legacy_pipes=True),
    'morph': lambda value: _parse_scheme_grouped(value, legacy_pipes=True),
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


_SEMANTIC_TEXT_SKIP_TAGS = frozenset({'note', 'figure', 'index'})


def _semantic_visible_text(element):
    """Return visible title text while retaining inline abbreviations.

    Verse token extraction deliberately excludes ``title`` and ``abbr`` to
    mirror the legacy cleaner.  A semantic title needs the opposite behavior:
    ``ST.`` in a book title and Strong's-tagged Psalm superscriptions are part
    of the title, while notes and non-textual figures are not.
    """

    parts = []
    _collect_semantic_text(element, parts)
    return ''.join(parts)


def _collect_semantic_text(element, parts):
    """Recursively collect visible semantic text in document order."""

    if _strip_ns(element.tag) in _SEMANTIC_TEXT_SKIP_TAGS:
        return
    if element.text:
        parts.append(element.text)
    for child in element:
        _collect_semantic_text(child, parts)
        if child.tail:
            parts.append(child.tail)


def _normalize_visible_text(value):
    """Collapse XML layout whitespace without altering visible characters."""

    return re.sub(r'\s+', ' ', value).strip()


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

def _normalized_characters(text):
    """Collapse whitespace, retaining the original interval for each character."""
    characters, positions = [], []
    for match in re.finditer(r'\s+|\S', text):
        characters.append(' ' if match.group().isspace() else match.group())
        positions.append((match.start(), match.end()))
    return ''.join(characters), positions


def _character_alignment(source, clean_text):
    raw, raw_positions = _normalized_characters(source)
    clean, clean_positions = _normalized_characters(clean_text)
    mapping = {}
    if raw == clean:
        mapping = dict(enumerate(range(len(raw))))
    else:
        # SWORD can uppercase divine names in its display projection. Compare
        # one case-folded key per original character, never a case-folded whole
        # string: expansions such as ß -> ss must not shift Unicode offsets or
        # invent a one-to-many character correspondence.
        raw_keys = [character.casefold() for character in raw]
        clean_keys = [character.casefold() for character in clean]
        for block in SequenceMatcher(None, raw_keys, clean_keys, autojunk=False).get_matching_blocks():
            mapping.update((block.a + i, block.b + i) for i in range(block.size))
    return raw_positions, clean_positions, mapping


def build_source_alignment(root, display_text):
    """Return ``(source, element_ranges, map_boundary)`` for study anchors.

    The element ranges use Unicode character offsets into visible source text.
    Skipped note/title/figure elements have zero-width ranges at their insertion
    point; their descendants are deliberately absent. ``map_boundary(offset,
    edge='after')`` maps that insertion point to a Unicode offset in display
    text; ``before`` selects the following side if rendering inserts characters.
    An adjacent unmatched source character returns ``None`` instead of guessing.
    """
    source, ranges = _source_layout(root)
    raw_positions, clean_positions, mapping = _character_alignment(source, display_text)

    def boundary(offset, edge='after'):
        if offset < 0 or offset > len(source):
            return None
        if not source:
            return 0 if not display_text else None
        for index, (start, end) in enumerate(raw_positions):
            if start < offset < end:  # A boundary inside collapsed whitespace.
                if index not in mapping:
                    return None
                interval = clean_positions[mapping[index]]
                return interval[1] if edge == 'after' else interval[0]
        previous = next((i for i in range(len(raw_positions) - 1, -1, -1)
                         if raw_positions[i][1] <= offset), None)
        following = next((i for i, (start, _) in enumerate(raw_positions)
                          if start >= offset), None)
        candidate = previous if edge == 'after' else following
        if candidate is None:
            candidate = following if edge == 'after' else previous
            selected_edge = 'before' if edge == 'after' else 'after'
        else:
            selected_edge = edge
        if candidate not in mapping:
            return None
        interval = clean_positions[mapping[candidate]]
        return interval[1] if selected_edge == 'after' else interval[0]

    return source, ranges, boundary


def _assign_word_positions(clean_text, tokens, spans, source, diagnostics=None):
    """Project complete source coordinates into the rendered display text.

    Whole-entry alignment accounts for unmarked prefixes, repeated words,
    punctuation inserted by SWORD, and whitespace normalization. It never treats
    a lexical token as an independent substring search. Characters that cannot
    be reconciled keep the established zero-position sentinel and are reported.
    """
    raw_positions, clean_positions, mapping = _character_alignment(source, clean_text)
    source_indices = {start: index for index, (start, _) in enumerate(raw_positions)}
    word_ranges = [(m.start(), m.end(), i + 1)
                   for i, m in enumerate(re.finditer(r'\S+', clean_text))]

    def project(start, end):
        indices = [source_indices[index] for index in range(start, end)
                   if index in source_indices and not source[index].isspace()]
        if not indices or any(index not in mapping for index in indices):
            return -1, -1
        first, last = mapping[indices[0]], mapping[indices[-1]]
        return clean_positions[first][0], clean_positions[last][1]

    for record in tokens + spans:
        start, end = record.pop('_source_start'), record.pop('_source_end')
        first, last = project(start, end)
        record['word_start'], record['word_end'] = _char_range_to_words(first, last, word_ranges)
        if first < 0:
            _diagnose(diagnostics, 'unaligned_annotation',
                      'Source annotation could not be aligned with display text',
                      annotation=record.get('token', record.get('span', '')))


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
