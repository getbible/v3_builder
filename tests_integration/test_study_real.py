# SPDX-License-Identifier: GPL-2.0-only
"""Source-to-output study fidelity checks against freshly extracted real modules.

Assertions use source XML and emitted documents independently: they do not call
Builder's study parser to manufacture expected output. Probes are deterministic
and bounded; missing source annotations do not become invented expectations.
"""

import json
import os
from itertools import islice
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from book_identity import BookResolver
from getbiblesword_contract import decode_byte_value, iter_contract

pytestmark = pytest.mark.integration


def _text(envelope):
    return decode_byte_value(envelope).decode('utf-8') if envelope is not None else ''


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _words(value):
    return ' '.join(value.split())


def _tree_text(content):
    return ''.join(value if isinstance(value, str) else _tree_text(value['children'])
                   for value in content)


def _entries(module):
    for record in iter_contract(module['contract_path']):
        if record['type'] == 'entry' and record['scope'].get('intro_scope') == 'verse':
            yield record


def _study_probe_entries(module):
    entries = _entries(module)
    yield from islice(entries, 500)
    if module['sword_name'] == 'WEB':
        # This real module carries its cross-reference as a note with a native
        # human-readable target, rather than an OSIS <reference> element.
        # Include the first Matthew citation so the separate reference contract
        # is exercised even when the opening Genesis sample has no references.
        for record in entries:
            if _text(record['scope']['osis_reference']) == 'Matt.1.23':
                yield record
                break


def _chapter(module, record):
    scope = record['scope']
    number = BookResolver({}, {}).resolve('', _text(scope['osis_reference'])).number
    book = next(item for item in module['version_data']['books'] if item['nr'] == number)
    chapter = next(item for item in book['chapters'] if item['chapter'] == scope['chapter'])
    return book, chapter


def _source_tree(record):
    value = record.get('normalized_raw') or record['raw']
    try:
        return ET.fromstring('<r>' + _text(value) + '</r>')
    except (UnicodeDecodeError, ET.ParseError):
        return None


@pytest.fixture(scope='session')
def real_study_samples(converted_modules):
    """Find up to one example of each annotation kind per module, from source."""
    samples = {'notes': [], 'headings': [], 'references': []}
    for module in converted_modules.values():
        found = set()
        for record in _study_probe_entries(module):
            root = _source_tree(record)
            if root is None:
                continue
            parents = {child: parent for parent in root.iter() for child in parent}
            for element in root.iter():
                kind = {'note': 'notes', 'title': 'headings', 'reference': 'references'}.get(_tag(element))
                if _tag(element) == 'note' and 'cross' in element.get('type', '').lower():
                    kind = 'references'
                if kind is None or kind in found or not ''.join(element.itertext()).strip():
                    continue
                ancestors = []
                parent = parents.get(element)
                while parent is not None:
                    ancestors.append(_tag(parent))
                    parent = parents.get(parent)
                if kind == 'headings' and (
                    any(tag in {'note', 'figure', 'rdg', 'rdgGroup'} for tag in ancestors)
                    or any(_tag(child) in {'note', 'figure', 'index'} for child in element.iter()
                           if child is not element)
                ):
                    continue
                if kind == 'notes' and (
                    'cross' in element.get('type', '').lower()
                    or any(_tag(child) == 'note' for child in element.iter() if child is not element)
                ):
                    continue
                book, chapter = _chapter(module, record)
                samples[kind].append((module, record, element, book, chapter))
                found.add(kind)
            if len(found) == 3:
                break
    return samples


def test_kjv_native_utf8_views_and_entry_attributes_are_populated(converted_modules):
    module = converted_modules['kjv']
    records = list(islice(_entries(module), 16))
    assert len(records) == 16, 'KJV extraction lacks its opening verses'
    required = os.environ.get('GETBIBLESWORD_REQUIRE_NORMALIZED') == '1'
    has_normalized = all('normalized_raw' in record and 'normalized_stripped' in record
                         for record in records)
    if not has_normalized and not required and module['producer_version'].lstrip('v') == '0.3.0':
        pytest.skip('Stable 0.3.0 predates normalized views and corrected native attribute collection')
    assert has_normalized, 'Candidate extraction must include both normalized projections'
    groups = set()
    for record in records:
        normalized = record['normalized_raw']
        stripped = record['normalized_stripped']
        assert normalized is not None and stripped is not None, _text(record['scope']['osis_reference'])
        # KJV's opening source is valid UTF-8: normalizing it must preserve every
        # byte, including lexical markup and footnote bodies.
        assert _text(normalized) == _text(record['raw'])
        assert '\ufffd' not in _text(normalized)
        assert '\ufffd' not in _text(stripped)
        for group in record['official_attributes']:
            name = _text(group['name'])
            if group['lists']:
                groups.add(name)
                assert any(item['values'] for item in group['lists']), name
    assert {'Word', 'Footnote'} <= groups, (
        'Rendering the current real KJV entry must populate lexical and note attributes; '
        f'got {sorted(groups)}'
    )


def test_real_note_bodies_and_nested_source_markup_are_preserved(real_study_samples):
    assert real_study_samples['notes'], 'Representative sources should contain actual study notes'
    for module, record, source, _, chapter in real_study_samples['notes']:
        verse_number = record['scope']['verse']
        expected = _words(''.join(source.itertext()))
        candidates = [item for item in chapter.get('editorial', [])
                      if item['type'] == 'footnote' and item['anchor']['verse'] == verse_number]
        matches = [item for item in candidates if _words(_tree_text(item['content'])) == expected]
        assert matches, (module['abbreviation'], _text(record['scope']['osis_reference']), expected)
        note = matches[0]
        # Readable note text may introduce paragraph/line separators that were
        # structural XML rather than literal whitespace in the source.
        assert ''.join(note['text'].split()) == ''.join(expected.split())
        assert note.get('attrs', {}) == source.attrib
        for source_child in source:
            assert any(isinstance(item, dict) and item['tag'] == source_child.tag
                       and all(item.get('attrs', {}).get(name) == value
                               for name, value in source_child.attrib.items())
                       for item in note['content']), source_child.tag
        verse = next(item for item in chapter['verses'] if item['verse'] == verse_number)
        if note['anchor'].get('alignment') == 'unresolved':
            assert 'offset' not in note['anchor']
        else:
            assert 0 <= note['anchor']['offset'] <= len(verse['text'])


def test_kjv_first_study_note_remains_after_its_scripture_text(converted_modules):
    module = converted_modules['kjv']
    record = next(record for record in _entries(module)
                  if _text(record['scope']['osis_reference']) == 'Gen.1.4')
    root = _source_tree(record)
    assert root is not None
    source_note = next(element for element in root if _tag(element) == 'note')
    assert root[-1] is source_note and not (source_note.tail or '').strip()
    _, chapter = _chapter(module, record)
    verse = next(item for item in chapter['verses'] if item['verse'] == 4)
    expected = _words(''.join(source_note.itertext()))
    note = next(item for item in chapter['editorial']
                if item['type'] == 'footnote' and _words(item['text']) == expected)
    assert note['anchor'] == {'verse': 4, 'offset': len(verse['text'])}
    assert expected not in verse['text']
    assert any(isinstance(child, dict) and child['tag'] == 'catchWord' for child in note['content'])
    assert any(isinstance(child, dict) and child['tag'] == 'rdg' for child in note['content'])


def test_real_headings_keep_source_text_and_verse_anchors(real_study_samples):
    assert real_study_samples['headings'], 'Representative source catalog should contain headings'
    for module, record, source, _, chapter in real_study_samples['headings']:
        expected = _words(''.join(source.itertext()))
        headings = [item for item in chapter.get('editorial', []) if item['type'] == 'heading']
        match = next((item for item in headings if item['text'] == expected), None)
        assert match is not None, (module['abbreviation'], expected)
        assert match['anchor']['verse'] == record['scope']['verse']
        assert match['anchor']['edge'] == 'before'
        assert match['heading_type'] == source.get('type', 'unspecified')


def test_real_references_keep_explicit_targets_at_chapter_level(real_study_samples):
    if not real_study_samples['references']:
        pytest.skip('Bounded representative source sample supplies no reference elements')
    for module, record, source, _, chapter in real_study_samples['references']:
        references = chapter.get('reference', {}).get('items', [])
        assert references, (module['abbreviation'], _text(record['scope']['osis_reference']))
        value = source.get('osisRef') or source.get('target') or source.get('passage')
        if value is None:
            value = ''.join(source.itertext())
        expected = value.split() if source.get('osisRef') is not None else [value]
        matching = [item for item in references if item['anchor']['verse'] == record['scope']['verse']]
        actual = {target['value'] for item in matching for target in item['targets']}
        assert set(expected) <= actual
        assert all(item['type'] != 'reference' for item in chapter.get('editorial', []))
        assert all('reference' not in verse for verse in chapter['verses'])


def test_real_study_links_resolve_and_documents_agree(real_study_samples):
    visited = set()
    for sample_group in real_study_samples.values():
        for module, _, _, book, chapter in sample_group:
            key = (module['abbreviation'], book['nr'], chapter['chapter'])
            if key in visited:
                continue
            visited.add(key)
            notes = {item['id']: item for item in chapter.get('editorial', []) if item['type'] == 'footnote'}
            references = {item['id']: item for item in chapter.get('reference', {}).get('items', [])}

            def check(value):
                if isinstance(value, list):
                    for item in value:
                        check(item)
                elif isinstance(value, dict):
                    attrs = value.get('attrs', {})
                    if 'footnote_id' in attrs:
                        assert attrs['footnote_id'] in notes
                        assert value['children'] == []
                    if 'reference_id' in attrs:
                        assert attrs['reference_id'] in references
                    anchor = value.get('anchor', {})
                    if 'note' in anchor:
                        assert anchor['note'] in notes
                        assert 0 <= anchor['note_offset'] <= len(notes[anchor['note']]['text'])
                    for item in value.values():
                        check(item)

            check(chapter)
            base = Path(module['output_dir']) / module['abbreviation']
            standalone = json.loads((base / str(book['nr']) / f'{chapter["chapter"]}.json').read_text())
            book_file = json.loads((base / f'{book["nr"]}.json').read_text())
            book_chapter = next(item for item in book_file['chapters'] if item['chapter'] == chapter['chapter'])
            for field in ('editorial', 'reference'):
                assert standalone.get(field) == chapter.get(field) == book_chapter.get(field)


def test_real_divine_name_apostrophe_survives_native_projection_and_publication(converted_modules):
    """SWORD's bytewise divine-name uppercasing corrupted a real curly quote."""
    module = converted_modules['kjv']
    record = next(record for record in _entries(module)
                  if _text(record['scope']['osis_reference']) == 'Exod.9.29')
    source = _text(record.get('normalized_raw') or record['raw'])
    assert 'Lord’s' in source, 'The source apostrophe is an authoritative Unicode character'
    _, chapter = _chapter(module, record)
    verse = next(item for item in chapter['verses'] if item['verse'] == 29)
    assert '’' in verse['text']
    assert '\ufffd' not in verse['text']
    assert not any(0x80 <= ord(character) <= 0x9f for character in verse['text'])
    assert 'â' not in verse['text'] and 'Â' not in verse['text']
    normalized = record.get('normalized_stripped')
    if normalized is not None:
        # Native may explicitly omit a broken projection. Any projection it
        # does supply must actually be Unicode and retain the punctuation.
        assert '’' in _text(normalized)


def test_real_native_reference_list_enriches_source_note_without_duplicates(converted_modules):
    module = converted_modules['web']
    record = next(record for record in _entries(module)
                  if _text(record['scope']['osis_reference']) == 'Matt.4.6')
    root = _source_tree(record)
    source_note = next(element for element in root.iter()
                       if _tag(element) == 'note' and element.get('type') == 'crossReference')
    expected = _words(''.join(source_note.itertext()))
    _, chapter = _chapter(module, record)
    references = [item for item in chapter.get('reference', {}).get('items', [])
                  if item['anchor']['verse'] == 6 and _words(item['text']) == expected]
    assert len(references) == 1, 'Native refList must enrich its source note, not duplicate the citation'
    reference = references[0]
    assert reference['anchor'].get('alignment') != 'unresolved'
    assert 'offset' in reference['anchor']
    for group in record['official_attributes']:
        if _text(group['name']) != 'Footnote':
            continue
        for item in group['lists']:
            values = {_text(value['name']): _text(value['value']) for value in item['values']}
            if _words(values.get('body', '')) != expected or not values.get('refList'):
                continue
            assert values['refList'] == reference.get('attrs', {}).get('refList') or values['refList'] in {
                target['value'] for target in reference['targets']
            }
