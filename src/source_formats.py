# SPDX-License-Identifier: GPL-2.0-only
"""Normalize SWORD source markup, never the authoritative source byte envelope.

The adapters use the source conventions implemented by SWORD's GBF/ThML/TEI
filters and BibleTime's corresponding filters. They retain unrecognized source
elements and attributes instead of passing through an HTML rendering (which
would discard notes, headings and lexical information). Well-formed OSIS is
passed through unchanged; entry-local containers are recovered with explicit
boundary markers. References remain strings unless the source supplies OSIS IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape, unescape
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET


_MAX_DEPTH = 128
_MAX_SOURCE = 16 * 1024 * 1024
_MAX_DIAGNOSTICS = 100
_ATTRIBUTE = re.compile(r'''([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''')
_GBF_TOKEN = re.compile(r'<([^<>]*)>')
_BLOCKS = {'title', 'note', 'p', 'div', 'milestone', 'l', 'lg', 'table', 'row', 'cell', 'figure'}
_VOID = {'br', 'hr', 'img', 'sync', 'milestone', 'lb', 'pb', 'graphic', 'ptr'}
_KNOWN = {
    'osis', 'osistext', 'div', 'verse', 'chapter', 'w', 'p', 'title', 'note',
    'reference', 'hi', 'q', 'foreign', 'seg', 'divineName', 'transChange',
    'milestone', 'lg', 'l', 'lb', 'list', 'item', 'table', 'row', 'cell',
    'figure', 'caption', 'name', 'speaker', 'abbr', 'date', 'number', 'unit',
    'inscription', 'catchWord', 'rdg', 'rdgGroup', 'index',
}


def decode_source(data: bytes, encoding: str = 'UTF-8') -> str:
    """Decode the declared source encoding with a total legacy-byte fallback.

    BOMs and explicit UTF-16/Latin-1/Windows-1252 declarations take precedence.
    Otherwise valid UTF-8 sequences stay unchanged and only undecodable bytes
    use Windows-1252, with Latin-1 for its five undefined byte values. SCSU is
    decoded by the native extractor into ``normalized_raw``, not guessed here.
    Invalid explicitly encoded Unicode raises ``UnicodeError``; an unknown
    codec raises ``ValueError`` so the caller can report the source diagnostic.
    """
    if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return data.decode('utf-32')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    if data.startswith(b'\xef\xbb\xbf'):
        data, encoding = data[3:], 'UTF-8'
    label = re.sub(r'[^a-z0-9]', '', encoding.lower())
    if label in {'utf16', 'utf16le'}:
        return data.decode('utf-16-le')
    if label == 'utf16be':
        return data.decode('utf-16-be')
    if label in {'latin1', 'iso88591', 'isoir100', 'l1'}:
        return data.decode('latin-1')
    if label in {'windows1252', 'cp1252', 'msansi'}:
        return ''.join(_legacy_byte(value) for value in data)
    if label == 'scsu':
        raise UnicodeError('SCSU source requires the native normalized_raw UTF-8 projection')
    if label not in {'', 'utf8', 'utf8sig'}:
        try:
            return data.decode(encoding)
        except LookupError as error:
            raise ValueError(f'Unsupported source encoding: {encoding}') from error
    decoded = data.decode('utf-8', errors='surrogateescape')
    return ''.join(
        _legacy_byte(ord(char) - 0xDC00)
        if 0xDC80 <= ord(char) <= 0xDCFF else char for char in decoded
    )


def _legacy_byte(value: int) -> str:
    try:
        return bytes((value,)).decode('cp1252')
    except UnicodeDecodeError:
        return chr(value)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)


def _diagnostic(diagnostics, code, message):
    item = {'code': code, 'message': message}
    if len(diagnostics) < _MAX_DIAGNOSTICS and item not in diagnostics:
        diagnostics.append(item)


def _attrs(source):
    return {
        match.group(1): unescape(next(value for value in match.groups()[1:] if value is not None))
        for match in _ATTRIBUTE.finditer(source)
    }


def _text(node):
    if isinstance(node, str):
        return node
    return ''.join(_text(child) for child in node.children)


def _serialize(node):
    if isinstance(node, str):
        return escape(node, quote=False)
    attrs = ''.join(f' {key}="{escape(value, quote=True)}"' for key, value in node.attrs.items())
    if not node.children:
        return f'<{node.tag}{attrs}/>'
    return f'<{node.tag}{attrs}>' + ''.join(_serialize(child) for child in node.children) + f'</{node.tag}>'


def _from_et(element, depth=0):
    if depth > _MAX_DEPTH:
        raise ValueError('source nesting exceeds the normalization limit')
    # Namespace URIs survive in xmlns declarations, with local names used by
    # the common semantic parser. Explicit attributes retain their namespaces.
    tag = element.tag
    attrs = dict(element.attrib)
    if tag.startswith('{'):
        namespace, tag = tag[1:].split('}', 1)
        attrs.setdefault('xmlns', namespace)
    for key in list(attrs):
        if key.startswith('{'):
            namespace, local = key[1:].split('}', 1)
            prefix = 'xml' if namespace == 'http://www.w3.org/XML/1998/namespace' else f'ns{len(attrs)}'
            if prefix != 'xml':
                attrs[f'xmlns:{prefix}'] = namespace
            attrs[f'{prefix}:{local}'] = attrs.pop(key)
    node = _Node(tag, attrs)
    if element.text:
        node.children.append(element.text)
    for child in element:
        node.children.append(_from_et(child, depth + 1))
        if child.tail:
            node.children.append(child.tail)
    return node


class _FragmentParser(HTMLParser):
    """Bounded recovery for HTML-like ThML and entry-local XML fragments."""

    def __init__(self, diagnostics, *, xml_boundaries=False):
        super().__init__(convert_charrefs=True)
        self.root = _Node('root')
        self.stack = [self.root]
        self.diagnostics = diagnostics
        self.xml_boundaries = xml_boundaries
        self._closing_name = ''
        if xml_boundaries:
            self.CDATA_CONTENT_ELEMENTS = ()

    def handle_starttag(self, tag, attrs):
        source = self.get_starttag_text()
        name = re.match(r'<\s*([^\s/>]+)', source).group(1)
        node = _Node(name, _attrs(source))
        self.stack[-1].children.append(node)
        is_void = (tag in {'milestone', 'lb'} or 'sID' in node.attrs or 'eID' in node.attrs) if self.xml_boundaries else tag in _VOID
        if not is_void:
            if len(self.stack) >= _MAX_DEPTH:
                raise ValueError('source nesting exceeds the normalization limit')
            # HTML permits omitted paragraph/list-item closing tags.
            if not self.xml_boundaries and tag in {'p', 'li'} and self.stack[-1].tag.lower() == tag:
                self.stack[-1].children.pop()
                self.stack.pop()
                self.stack[-1].children.append(node)
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        previous = self.stack[-1]
        self.handle_starttag(tag, attrs)
        if self.stack[-1] is not previous:
            self.stack.pop()

    def handle_endtag(self, tag):
        name = self._closing_name if self.xml_boundaries else tag
        for index in range(len(self.stack) - 1, 0, -1):
            candidate = self.stack[index].tag if self.xml_boundaries else self.stack[index].tag.lower()
            if candidate == name:
                self._mark_open(self.stack[index + 1:])
                del self.stack[index:]
                return
        _diagnostic(self.diagnostics, 'unmatched_source_boundary', f'Unmatched closing source tag: {name}')
        self.stack[-1].children.append(_Node('milestone', {'type': 'x-source-end', 'n': name}))

    def parse_endtag(self, index):
        # HTMLParser lowercases end-tag callbacks. Keep the original XML name
        # for case-sensitive matching and a faithful unmatched boundary marker.
        match = re.match(r'</\s*([^\s/>]+)', self.rawdata[index:])
        self._closing_name = match[1] if match else ''
        return super().parse_endtag(index)

    def _mark_open(self, nodes):
        if not self.xml_boundaries:
            return
        for node in nodes:
            node.children.append(_Node('milestone', {'type': 'x-source-open', 'n': node.tag}))
            _diagnostic(self.diagnostics, 'unpaired_source_container', f'Source {node.tag} container remains open at an entry boundary; its continuation is not inferred.')

    def finish(self):
        self.close()
        self._mark_open(self.stack[1:])
        return self.root

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)

    def handle_comment(self, data):
        # Comments are source metadata, never scripture words.
        self.stack[-1].children.append(_Node('milestone', {'type': 'x-source-comment', 'n': data}))

    def handle_decl(self, decl):
        _diagnostic(self.diagnostics, 'unsupported_source_declaration', f'Source declaration retained: {decl[:160]}')
        self.stack[-1].children.append(_Node('milestone', {'type': 'x-source-declaration', 'n': decl}))


    def unknown_decl(self, decl):
        if decl.startswith('CDATA['):
            self.handle_data(decl[6:])
        else:
            self.handle_decl(decl)

    def handle_pi(self, data):
        self.stack[-1].children.append(_Node('milestone', {'type': 'x-source-instruction', 'n': data}))


def _xml_fragment(raw, diagnostics, *, xml_boundaries=False):
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
        raise ValueError('DTD and entity declarations are not evaluated')
    # HTML's named entities occur in ThML. Decode only entity tokens so that
    # escaped literal markup remains literal text, never newly active markup.
    if not xml_boundaries:
        raw = re.sub(r'&([A-Za-z][A-Za-z0-9]+);', lambda m: escape(unescape(m[0]), quote=False), raw)
    try:
        return _from_et(ET.fromstring(f'<root>{raw}</root>'))
    except ET.ParseError:
        _diagnostic(diagnostics, 'recovered_source_markup', 'Recovered a legacy or entry-local XML fragment with unbalanced/HTML-style tags.')
        parser = _FragmentParser(diagnostics, xml_boundaries=xml_boundaries)
        parser.feed(raw)
        return parser.finish()


def _mapping(node, markup, diagnostics):
    if isinstance(node, str):
        return node
    tag = node.tag.lower()
    attrs = node.attrs.copy()
    children = [_mapping(child, markup, diagnostics) for child in node.children]
    target = node.tag
    if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head'}:
        target = 'title'
        attrs.setdefault('type', 'section')
    elif tag == 'div' and attrs.get('class', '').lower() in {'sechead', 'title'}:
        target = 'title'
        attrs.setdefault('type', 'main' if attrs['class'].lower() == 'title' else 'section')
    elif tag == 'div' and attrs.get('type', '').lower() == 'variant':
        target = 'seg'
        attrs['type'] = 'x-variant'
        if attrs.get('class'):
            attrs.setdefault('subType', 'x-class:' + attrs['class'])
    elif tag in {'scripref', 'ref', 'ptr', 'a'}:
        target = 'reference'
        destination = attrs.get('passage') or attrs.get('target') or attrs.get('href')
        if destination:
            attrs.setdefault('target', destination)
        elif tag == 'scripref' and _text(node).strip():
            attrs.setdefault('target', _text(node).strip())
    elif tag in {'i', 'em', 'b', 'strong', 'u', 'sup', 'sub', 'small'}:
        target = 'hi'
        attrs.setdefault('type', {
            'i': 'italic', 'em': 'italic', 'b': 'bold', 'strong': 'bold',
            'u': 'underline', 'sup': 'super', 'sub': 'sub', 'small': 'small',
        }[tag])
    elif tag == 'hi' and attrs.get('rend'):
        attrs.setdefault('type', attrs['rend'])
    elif tag in {'blockquote', 'quote'}:
        target = 'q'
    elif tag in {'br', 'lb'}:
        target = 'lb'
    elif tag in {'img', 'graphic'}:
        target = 'figure'
        if attrs.get('url'):
            attrs.setdefault('src', attrs['url'])
    elif tag in {'tr', 'td', 'th', 'ul', 'ol', 'li'}:
        target = {'tr': 'row', 'td': 'cell', 'th': 'cell', 'ul': 'list', 'ol': 'list', 'li': 'item'}[tag]
        if tag == 'th':
            attrs.setdefault('role', 'label')
        elif tag in {'ul', 'ol'}:
            attrs.setdefault('type', 'ordered' if tag == 'ol' else 'unordered')
    elif tag == 'sync' and markup == 'thml':
        kind = attrs.get('type', '').lower()
        value = attrs.get('value', '').strip()
        if kind in {'strongs', 'morph', 'lemma'} and value:
            scheme = attrs.get('class') or ('strong' if kind == 'strongs' else 'robinson' if kind == 'morph' else '')
            lexical = ' '.join(part if ':' in part or not scheme else f'{scheme}:{part}' for part in re.split(r'[|\s]+', value) if part)
            mapped = {'morph' if kind == 'morph' else 'lemma': lexical}
            mapped.update({f'x-source-sync-{key}': value for key, value in attrs.items() if key not in {'type', 'class', 'value'}})
            return _Node('_lexical', mapped)
        _diagnostic(diagnostics, 'unsupported_source_element', f'Unmapped ThML sync retained: {attrs}')
    elif tag == 'foreign' and attrs.get('lang'):
        attrs.setdefault('xml:lang', attrs['lang'])
    elif tag == 'w' and markup == 'tei':
        if attrs.get('ana') and not attrs.get('morph'):
            # TEI ana points to analyses, not necessarily morphological codes.
            _diagnostic(diagnostics, 'unresolved_tei_analysis', 'TEI w@ana retained as a source pointer without inventing a morphology scheme.')
    elif tag not in {'root', '_lexical'} and node.tag not in _KNOWN and tag not in _KNOWN:
        _diagnostic(diagnostics, 'unsupported_source_element', f'Unmapped {markup} element retained: {node.tag}')
    return _Node(target, attrs, children)


def _postfix(node, diagnostics):
    """Attach legacy postfix lexical groups without crossing a block or note."""
    for child in node.children:
        if isinstance(child, _Node):
            _postfix(child, diagnostics)
    output = []
    start = 0
    last_word = None
    for child in node.children:
        if isinstance(child, _Node) and child.tag == '_lexical':
            pending = output[start:]
            visible = ''.join(_text(item) for item in pending)
            if not visible.strip(' \t\n\r.,;:!?()[]\"\u201c\u201d') and last_word is not None:
                word = last_word
            elif visible.strip():
                # Keep whitespace and leading punctuation outside the lexical
                # group, matching BibleTime's postfix convention.
                while pending and isinstance(pending[0], str) and not pending[0]:
                    pending.pop(0)
                prefix = ''
                if pending and isinstance(pending[0], str):
                    match = re.match(r'^[\s.,;:!?()\[\]"\u201c\u201d]*', pending[0])
                    prefix = match[0]
                    pending[0] = pending[0][len(prefix):]
                if len(pending) == 1 and isinstance(pending[0], _Node) and pending[0].tag == 'w':
                    word = pending[0]
                else:
                    word = _Node('w', children=pending)
                output[start:] = ([prefix] if prefix else []) + [word]
            else:
                _diagnostic(diagnostics, 'orphan_lexical_marker', 'A postfix lexical marker has no preceding text; its metadata is retained.')
                output.append(_Node('milestone', {'type': 'x-lexical', **child.attrs}))
                start = len(output)
                continue
            for key, value in child.attrs.items():
                word.attrs[key] = ' '.join(filter(None, (word.attrs.get(key), value)))
            last_word = word
            start = len(output)
        else:
            output.append(child)
            if isinstance(child, _Node) and child.tag in _BLOCKS:
                start = len(output)
                last_word = None
    node.children = output


_GBF_OPEN = {
    'FI': ('hi', {'type': 'italic'}), 'FB': ('hi', {'type': 'bold'}),
    'FU': ('hi', {'type': 'underline'}), 'FS': ('hi', {'type': 'super'}),
    'FV': ('hi', {'type': 'sub'}), 'FR': ('q', {'who': 'Jesus'}),
    'FO': ('q', {'type': 'x-OldTestament'}),
    'TT': ('title', {'type': 'main'}), 'TS': ('title', {'type': 'section'}),
    'TH': ('title', {'type': 'psalm'}), 'RF': ('note', {}),
    'PP': ('lg', {}), 'JR': ('seg', {'type': 'x-align-right'}),
    'JC': ('seg', {'type': 'x-align-center'}),
    'RB': ('seg', {'type': 'x-footnote-catchword'}),
    'FA': ('hi', {'type': 'x-footnote-emphasis'}),
}
_GBF_CLOSE = {tag[0] + tag[1].lower(): tag for tag in _GBF_OPEN}
_GBF_CLOSE.update({'JL': ('JR', 'JC'), 'Fn': 'FN', 'Rx': 'RX'})


def _gbf(raw, diagnostics):
    root = _Node('root')
    stack = [('root', root)]

    def close(names):
        if isinstance(names, str):
            names = (names,)
        for index in range(len(stack) - 1, 0, -1):
            if stack[index][0] in names:
                del stack[index:]
                return True
        return False

    position = 0
    for match in _GBF_TOKEN.finditer(raw):
        if match.start() > position:
            stack[-1][1].children.append(unescape(raw[position:match.start()]))
        position = match.end()
        token = match[1].strip()
        name = token.split(None, 1)[0] if token else ''
        attrs = _attrs(token)
        if name in _GBF_CLOSE:
            if not close(_GBF_CLOSE[name]):
                _diagnostic(diagnostics, 'unmatched_source_boundary', f'Unmatched GBF closing marker retained: {name}')
                stack[-1][1].children.append(_Node('milestone', {'type': 'x-gbf-end', 'n': name}))
            continue
        if token.startswith(('WG', 'WH', 'WT')):
            value = token[2:].strip(' "')
            if token.startswith(('WG', 'WH')) and value:
                lexical = {'lemma': f'strong:{token[1]}{value}'}
            elif value:
                lexical = {'morph': f'strongMorph:T{value}' if value[0:1] in {'G', 'H'} and value[1:].isdigit() else f'robinson:{value}'}
            else:
                lexical = {}
            stack[-1][1].children.append(_Node('_lexical', lexical))
            continue
        if name in {'CL', 'CM'}:
            stack[-1][1].children.append(_Node('lb' if name == 'CL' else 'milestone', {} if name == 'CL' else {'type': 'x-p'}))
            continue
        if name in {'CT', 'CG'}:
            stack[-1][1].children.append('<' if name == 'CT' else '>')
            continue
        if name in _GBF_OPEN or token.startswith(('FN', 'RX')):
            if name == 'RF' and any(entry[0] == 'RB' for entry in stack):
                close('RB')
            if token.startswith('FN'):
                name, target, defaults = 'FN', 'hi', {'type': 'x-font', 'face': token[2:].strip(' "')}
            elif token.startswith('RX'):
                name, target, defaults = 'RX', 'reference', {'target': token[2:].strip(' "')}
            else:
                target, defaults = _GBF_OPEN[name]
            node = _Node(target, {**defaults, **attrs})
            stack[-1][1].children.append(node)
            if len(stack) >= _MAX_DEPTH:
                raise ValueError('source nesting exceeds the normalization limit')
            stack.append((name, node))
            continue
        # Several historic GBF modules embed XML references/notes/word tags.
        xml_name = name.rstrip('/').lstrip('/')
        if xml_name.lower() in {'scripref', 'note', 'w', 'p', 'img', 'b', 'i', 'table', 'tr', 'td'}:
            if name.startswith('/'):
                if not close(xml_name):
                    _diagnostic(diagnostics, 'unmatched_source_boundary', f'Unmatched embedded GBF XML close: {xml_name}')
                continue
            node = _Node(xml_name, attrs)
            stack[-1][1].children.append(node)
            if not token.endswith('/') and xml_name not in _VOID:
                if len(stack) >= _MAX_DEPTH:
                    raise ValueError('source nesting exceeds the normalization limit')
                stack.append((xml_name, node))
            continue
        _diagnostic(diagnostics, 'unsupported_gbf_tag', f'Unmapped GBF marker retained: <{token[:160]}>')
        stack[-1][1].children.append(_Node('milestone', {'type': 'x-gbf', 'n': token}))
    if position < len(raw):
        stack[-1][1].children.append(unescape(raw[position:]))
    if len(stack) > 1:
        _diagnostic(diagnostics, 'recovered_source_markup', 'Closed GBF containers at the end of the source entry.')
    return root


def normalize_source(raw: str, markup: str) -> tuple[str, list[dict[str, str]]]:
    """Return a shared semantic fragment and deterministic bounded diagnostics.

    Plain text is escaped, never parsed as markup. Unsupported source formats
    and unsafe/deep fragments are retained as escaped text with a diagnostic;
    native stripped display text remains available independently to the caller.
    """
    diagnostics = []
    label = re.sub(r'[^a-z0-9]', '', markup.lower())
    if label in {'', 'plain', 'plaintext', 'text'}:
        return escape(raw, quote=False), diagnostics
    if label not in {'osis', 'gbf', 'thml', 'tei'}:
        _diagnostic(diagnostics, 'unsupported_source_format', f'Unmapped source format retained as text: {markup}')
        return escape(raw, quote=False), diagnostics
    try:
        if len(raw) > _MAX_SOURCE:
            raise ValueError('source fragment exceeds the normalization size limit')
        if any(ord(char) < 32 and char not in '\t\n\r' for char in raw):
            raise ValueError('source contains XML-incompatible control characters')
        if label == 'osis':
            if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I):
                raise ValueError('DTD and entity declarations are not evaluated')
            try:
                parsed = ET.fromstring(f'<root>{raw}</root>')
                # Depth is checked iteratively, retaining the exact original
                # spelling/whitespace/namespaces for ordinary OSIS fragments.
                pending = [(parsed, 0)]
                while pending:
                    element, depth = pending.pop()
                    if depth > _MAX_DEPTH:
                        raise ValueError('source nesting exceeds the normalization limit')
                    pending.extend((child, depth + 1) for child in element)
                return raw, diagnostics
            except ET.ParseError:
                root = _xml_fragment(raw, diagnostics, xml_boundaries=True)
                return ''.join(_serialize(child) for child in root.children), diagnostics
        root = _gbf(raw, diagnostics) if label == 'gbf' else _xml_fragment(raw, diagnostics)
        root = _mapping(root, label, diagnostics)
        _postfix(root, diagnostics)
        return ''.join(_serialize(child) for child in root.children), diagnostics
    except (ValueError, RecursionError, AssertionError) as error:
        _diagnostic(diagnostics, 'unparsed_source_fragment', str(error))
        return escape(raw, quote=False), diagnostics
