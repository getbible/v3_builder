# SPDX-License-Identifier: GPL-2.0-only
"""Project note bodies and references without putting annotation prose in verses.

The XML tree is a semantic representation, not a raw-byte envelope. Every string,
attribute and nested element in a note is retained; nested notes and references
have deterministic links to their chapter-level records. Offsets count Unicode
codepoints in the displayed verse (or in the parent note's ``text``).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from book_identity import BookResolver, OSIS_NUMBERS
from osis_parser import (
    _flat_lexical_segment, _main_text_elements, _normalize_visible_text,
    _ordinary_lexical_word, _parse_osis_fragment,
    _semantic_visible_text, _strip_ns, build_source_alignment,
)


_CROSS_REFERENCE_TYPES = frozenset({
    'crossreference', 'crossreferences', 'xcrossreference', 'xcrossreferences',
    'crossref', 'xcrossref', 'xref', 'xreference',
})
_BLOCKS = frozenset({'p', 'title', 'l', 'lg', 'div', 'row', 'item'})
# These structures contain information that existing token/span/heading/paragraph
# projections cannot express. Unknown element names are preserved as well.
_STRUCTURES = frozenset({
    'l', 'lg', 'lb', 'table', 'row', 'cell', 'figure', 'list', 'item',
    'abbr', 'catchWord', 'index', 'rdg', 'rdgGroup', 'a',
})
_REPRESENTED = frozenset({
    'r', 'osis', 'osisText', 'div', 'chapter', 'verse', 'p', 'milestone',
    'title', 'w', 'seg', 'q', 'hi', 'divineName', 'transChange', 'foreign',
    'inscription', 'name', 'speaker', 'number', 'unit', 'note', 'reference',
})
_BOOK = r'[1-4]?[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*'
_ADDRESS = re.compile(rf'(?P<book>{_BOOK})(?:\.(?P<chapter>\d+))?(?:\.(?P<verse>\d+))?(?:![^\s-]+)?\Z')
_KNOWN_BOOKS = {name.casefold() for name in OSIS_NUMBERS}


def _cross_reference(element):
    return _strip_ns(element.tag) == 'note' and re.sub(
        r'[^a-z]', '', element.get('type', '').lower()
    ) in _CROSS_REFERENCE_TYPES


def _content_text(element):
    """Readable note text and exact node boundaries, with block separators.

    Nested notes have their own bodies. Their placeholders remain in ``content``
    but do not silently become text of the parent note. Titles and paragraph
    text are deliberately included here, unlike the main Scripture projection.
    """
    parts = []
    positions = {}
    size = 0

    def append(value):
        nonlocal size
        if value:
            parts.append(value)
            size += len(value)

    def line():
        if parts and not parts[-1].endswith('\n'):
            append('\n')

    def walk(node, first=False):
        tag = _strip_ns(node.tag)
        if tag in _BLOCKS and not first:
            line()
        positions[node] = size
        if tag == 'note' and not first:
            return
        if tag in {'lb', 'br'}:
            append('\n')
        elif tag == 'milestone' and node.get('type') == 'x-p':
            line()
        if node.text:
            append(node.text)
        for child in node:
            walk(child)
            append(child.tail)
        if tag in _BLOCKS and not first:
            line()

    walk(element, True)
    raw = ''.join(parts)
    start = len(raw) - len(raw.lstrip())
    value = raw.strip()
    return value, {node: max(0, min(len(value), offset - start))
                   for node, offset in positions.items()}


def _osis_address(value, resolver, *, allow_unknown=True):
    match = _ADDRESS.fullmatch(value)
    if match is None:
        return None
    fields = match.groupdict()
    if not allow_unknown and fields['book'].casefold() not in _KNOWN_BOOKS:
        return None
    if any(fields[key] is not None and int(fields[key]) < 1
           for key in ('chapter', 'verse')):
        return None
    identity = resolver.resolve('', osis_reference=fields['book'])
    address = {'book': identity.number}
    for name in ('chapter', 'verse'):
        if fields[name] is not None:
            address[name] = int(fields[name])
    return address


def _target(value, osis, resolver):
    """Resolve explicit OSIS identifiers; free-form labels are never guessed."""
    result = {'value': value, 'scheme': 'unresolved'}
    if not osis:
        if value.startswith('#'):
            result['scheme'] = 'local'
        else:
            try:
                scheme = urlsplit(value).scheme.lower()
            except ValueError:
                scheme = ''
            if scheme in {'http', 'https', 'ftp', 'mailto', 'urn'}:
                result['scheme'] = 'uri'
        return result
    work, separator, address = value.partition(':')
    if not separator:
        address = work
    endpoints = address.split('-')
    if len(endpoints) > 2:
        return result
    # A work-qualified non-Biblical ID may name another document, so cannot be
    # assigned a Scripture book number solely from its spelling.
    begin = _osis_address(endpoints[0], resolver, allow_unknown=not separator)
    end = _osis_address(endpoints[1], resolver, allow_unknown=not separator) if len(endpoints) == 2 else None
    if begin is None or (len(endpoints) == 2 and end is None):
        return result
    result['scheme'] = 'osis'
    result.update(begin)
    if end is not None:
        result['end'] = end
    return result


def extract_study(raw_osis: str, display_text: str, verse: int, *, context: dict | None = None) -> dict:
    """Extract chapter editorial notes, separate references and richer structure.

    ``context`` may provide a shared ``book_resolver`` and ``retain_content=True``
    to retain the complete main tree, including ordinary headings/paragraphs.
    Note bodies still occur only in their own records, linked by placeholders.
    Source bytes must already
    have been decoded using the module encoding and non-OSIS formats normalized
    before this function is called. Malformed/unresolved content is diagnosed;
    a failed display alignment retains the record without inventing an offset.
    """
    result = {'footnotes': [], 'references': [], 'content': [],
              'title_content': [], 'diagnostics': []}
    if not raw_osis:
        return result
    context = context or {}
    root = _parse_osis_fragment(raw_osis)
    if root is None:
        result['diagnostics'].append({
            'code': 'study_markup_unparsed', 'verse': verse,
            'message': 'Study annotations could not be parsed from the source markup.',
        })
        if not display_text or context.get('retain_content'):
            # Uninterpreted Unicode is a content string, never raw-byte metadata
            # and never an XML node that consumers should attempt to execute.
            result['content'] = [raw_osis]
        return result
    resolver = context.get('book_resolver') or BookResolver({}, {})
    source, source_ranges, map_boundary = build_source_alignment(root, display_text)
    parents = {child: parent for parent in root.iter() for child in parent}
    node_ids = {}

    def anchor(node, parent_note=None):
        current = node
        while current not in source_ranges and current in parents:
            current = parents[current]
        source_offset = source_ranges.get(current, (0, 0))[0]
        offset = map_boundary(source_offset, edge='after')
        value = {'verse': verse}
        if offset is None:
            value['alignment'] = 'unresolved'
            result['diagnostics'].append({
                'code': 'study_anchor_unresolved', 'verse': verse,
                'message': 'Source and display text differ; no character offset was invented.',
            })
        else:
            value['offset'] = offset
        if parent_note is not None:
            value['note'] = parent_note['id']
            value['note_offset'] = parent_note['positions'].get(node, 0)
        return value

    def targets(element):
        raw = element.get('osisRef')
        osis = raw is not None
        if raw is None:
            raw = element.get('target') or element.get('passage') or element.get('href') or element.get('refList')
        if raw is None:
            raw = _content_text(element)[0]
        # OSIS uses XML whitespace to separate targets. A native free-form
        # label remains intact: "John 3:16" is not three unrelated references.
        values = raw.split() if osis else [raw]
        output = []
        for value in values:
            if not value:
                continue
            target = _target(value, osis, resolver)
            output.append(target)
            if target['scheme'] == 'unresolved':
                result['diagnostics'].append({
                    'code': 'reference_target_unresolved', 'verse': verse,
                    'value': value,
                    'message': 'Reference target is preserved without a guessed Scripture address.',
                })
        return output

    def node(tag, attrs, children):
        value = {'tag': tag, 'children': children}
        if attrs:
            value['attrs'] = attrs
        return value

    def children(element, parent_note=None, reference_group=None):
        output = []
        if element.text:
            output.append(element.text)
        for child in element:
            output.append(serialize(child, parent_note, reference_group))
            if child.tail:
                output.append(child.tail)
        return output

    def make_reference(element, parent_note=None):
        reference_id = f'ref-{verse}-{len(result["references"]) + 1}'
        node_ids[element] = reference_id
        item = {
            'id': reference_id, 'anchor': anchor(element, parent_note),
            'text': _content_text(element)[0], 'targets': [],
        }
        if element.attrib:
            item['attrs'] = dict(element.attrib)
        result['references'].append(item)
        if _strip_ns(element.tag) == 'reference':
            item['targets'].extend(targets(element))
        else:
            descendants = [child for child in element.iter()
                           if _strip_ns(child.tag) == 'reference']
            if not descendants:
                item['targets'].extend(targets(element))
        item['content'] = children(element, parent_note, item)
        return reference_id

    def serialize(element, parent_note=None, reference_group=None):
        tag = _strip_ns(element.tag)
        attrs = dict(element.attrib)
        if _cross_reference(element):
            reference_id = make_reference(element, parent_note)
            attrs['reference_id'] = reference_id
            return node(element.tag, attrs, [])
        if tag == 'note':
            footnote_id = f'fn-{verse}-{len(result["footnotes"]) + 1}'
            node_ids[element] = footnote_id
            text, positions = _content_text(element)
            item = {
                'type': 'footnote', 'id': footnote_id,
                'anchor': anchor(element, parent_note), 'text': text, 'content': [],
            }
            if attrs:
                item['attrs'] = dict(attrs)
            result['footnotes'].append(item)
            note_context = {'id': footnote_id, 'positions': positions}
            item['content'] = children(element, note_context)
            attrs['footnote_id'] = footnote_id
            return node(element.tag, attrs, [])
        if tag == 'reference':
            if reference_group is not None:
                reference_id = reference_group['id']
                reference_group['targets'].extend(targets(element))
                node_ids[element] = reference_id
            else:
                reference_id = make_reference(element, parent_note)
            attrs['reference_id'] = reference_id
            # Its visible label stays in the host text, while its full nested
            # representation is recorded once by the reference entry.
            content = children(element, parent_note, reference_group) if reference_group is not None else _plain_children(element)
            return node(element.tag, attrs, content)
        return node(element.tag, attrs, children(element, parent_note, reference_group))

    def _plain_children(element):
        """Reuse links already collected for nested notes/references."""
        output = []
        if element.text:
            output.append(element.text)
        for child in element:
            attrs = dict(child.attrib)
            link_id = node_ids.get(child)
            if link_id is not None:
                link = 'footnote_id' if link_id.startswith('fn-') else 'reference_id'
                attrs[link] = link_id
            nested = [] if _strip_ns(child.tag) == 'note' else _plain_children(child)
            output.append(node(child.tag, attrs, nested))
            if child.tail:
                output.append(child.tail)
        return output

    tree = children(root)
    # A title has an independent rendering surface. Expose its linked content
    # for converter reconciliation, so a heading does not duplicate footnote
    # bodies when it supplements its existing text/tokens/spans representation.
    for element in _main_text_elements(root):
        if _strip_ns(element.tag) != 'title':
            continue
        text = _normalize_visible_text(_semantic_visible_text(element))
        if not text:
            continue
        title = {'text': text, 'content': _plain_children(element)}
        if element.attrib:
            title['attrs'] = dict(element.attrib)
        result['title_content'].append(title)
    # Notes are already represented in full by editorial/reference entries;
    # unfamiliar structures inside them must not duplicate the entire verse.
    def needs_structure(element, ignored=()):
        for child in element:
            if child in ignored:
                continue
            tag = _strip_ns(child.tag)
            if tag in {'note', 'title'}:
                continue
            if tag in _STRUCTURES or tag not in _REPRESENTED:
                return True
            if tag == 'div' and (
                child.get('type', '') not in {'', 'x-p', 'paragraph'}
                or set(child.attrib) - {'type'}
            ):
                return True
            if tag == 'milestone' and (
                child.get('type', '') != 'x-p' or set(child.attrib) - {'type'}
            ):
                return True
            if tag == 'seg' and not (
                child.get('type') == 'x-transChange' or child.get('subType') == 'x-added'
            ):
                # Flat lexical segments are represented completely by their
                # actual source span plus the existing lexical tokens. Do not
                # duplicate every word/attribute in a second full verse tree.
                # Empty/mixed/nested segments still need structural content.
                start, end = source_ranges.get(child, (0, 0))
                represented = _flat_lexical_segment(child) and any(
                    span.get('tag') == 'seg'
                    and span.get('attrs', {}) == dict(child.attrib)
                    and span.get('span') == source[start:end].strip()
                    for span in context.get('spans', [])
                )
                if not represented or any(
                    ancestor.tag == 'w'
                    or ancestor.get('{http://www.w3.org/XML/1998/namespace}space') == 'preserve'
                    for ancestor in _ancestors(child, parents)
                ):
                    return True
            if tag in {'osis', 'osisText', 'chapter', 'verse'} and child.attrib:
                return True
            if tag == 'w' and any('}' in name or not value for name, value in child.attrib.items()):
                # Ordinary lexical attributes are completely represented on
                # tokens. Preserve the exceptional namespace/empty-value cases
                # that the historical intrinsic-attribute projection normalizes.
                return True
            if tag == 'p' and (child.attrib or source_ranges.get(child, (0, 0))[0] > 0):
                return True
            if needs_structure(child, ignored):
                return True
        return False

    if context.get('retain_content') or needs_structure(root):
        result['content'] = tree
        if not context.get('retain_content'):
            # A closing chapter marker need not repeat an entire lexical verse.
            # Only compact an empty root-level suffix after fully represented
            # words/segments, and only when its exact display anchor is known.
            suffix = []
            for child in reversed(root):
                is_boundary = (child.tag in {'chapter', 'verse'} or (
                    child.tag == 'div' and child.get('type') == 'book'
                )) and (
                    bool(child.get('sID')) != bool(child.get('eID'))
                )
                if (len(child) or child.text or (child.tail or '').strip()
                        or child.get('{http://www.w3.org/XML/1998/namespace}space') == 'preserve'
                        or not (is_boundary or child.tag == 'seg')):
                    break
                suffix.insert(0, child)
            prefix = list(root)[:len(root) - len(suffix)]
            words = [word for child in prefix
                     for word in ([child] if child.tag == 'w' else list(child))]
            tokens = context.get('tokens', [])
            if (suffix and any(child.tag in {'chapter', 'verse', 'div'} for child in suffix)
                    and prefix and not (root.text or '').strip()
                    and all((_ordinary_lexical_word(child) or _flat_lexical_segment(child))
                            and not (child.tail or '').strip() for child in prefix)
                    and len(words) == len(tokens)
                    and all(word.text == token.get('token') for word, token in zip(words, tokens))
                    and tokens[-1].get('word_start', 0) > 0
                    and not needs_structure(root, suffix)):
                start = source_ranges[suffix[0]][0]
                offset = map_boundary(start, edge='after')
                if offset is None and not source[start:].strip():
                    # SWORD can remove ordinary trailing layout whitespace.
                    # Its preceding aligned character proves the same endpoint.
                    trimmed = len(source[:start].rstrip())
                    candidate = map_boundary(trimmed, edge='after')
                    if candidate == len(display_text):
                        offset = candidate
                if offset == len(display_text):
                    result['content'] = [node(child.tag, dict(child.attrib), []) for child in suffix]
                    result['anchor'] = {'verse': verse, 'offset': offset}
    return result


def _ancestors(element, parents):
    while element in parents:
        element = parents[element]
        yield element
