# SPDX-License-Identifier: GPL-2.0-only
"""Integration regressions for source-only and native-recovered study metadata."""

from test_getbiblesword_converter import _config, _convert, entry
from test_study_conversion import assert_schemas, attributes


def _chapter(document):
    return document['books'][0]['chapters'][0]


def test_trailing_source_title_retains_nested_content_and_source_attributes(tmp_path):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, 'verse', 'Word', 'Word'),
        entry(1, 1, 2, 'verse',
              '<title level="2" short="Abbr">Final <hi type="italic">heading</hi></title>', ''),
    ])
    structure = next(item for item in _chapter(document)['editorial'] if item['type'] == 'structure')
    title = structure['content'][0]
    assert title['tag'] == 'title'
    assert title['attrs']['level'] == '2'
    assert title['attrs']['short'] == 'Abbr'
    assert title['children'] == ['Final ', {'tag': 'hi', 'attrs': {'type': 'italic'}, 'children': ['heading']}]
    assert_schemas(document)


def test_source_only_heading_uses_same_note_link_as_chapter_editorial(tmp_path):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, 'verse', '<title>Heading<note n="a">Heading note</note></title>', ''),
        entry(1, 1, 2, 'verse', 'Word', 'Word'),
    ])
    editorial = _chapter(document)['editorial']
    heading = next(item for item in editorial if item['type'] == 'heading')
    note = next(item for item in editorial if item['type'] == 'footnote')
    link = heading['content'][1]
    assert link['attrs']['footnote_id'] == note['id']
    assert link['children'] == []
    assert note['text'] == 'Heading note'
    assert_schemas(document)


def test_empty_source_entry_keeps_native_only_note_with_source_anchor(tmp_path):
    record = entry(1, 1, 2, 'verse', '', '')
    record['official_attributes'] = attributes(Footnote={'1': {'body': 'Recovered source-only note', 'type': 'study'}})
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, 'verse', 'Word', 'Word'), record,
    ])
    notes = [item for item in _chapter(document).get('editorial', []) if item['type'] == 'footnote']
    assert len(notes) == 1
    assert notes[0]['text'] == 'Recovered source-only note'
    assert notes[0]['anchor'] == {'verse': 2, 'scope': 'source', 'alignment': 'unresolved'}
    assert_schemas(document)


def test_native_heading_metadata_enriches_existing_source_heading(tmp_path):
    record = entry(0, 1, 1, 'verse', '<title>Psalm title</title>Word', 'Word')
    record['official_attributes'] = attributes(Heading={
        'Preverse': {'0': 'Psalm title'},
        '0': {'canonical': 'true', 'type': 'psalm', 'level': '1'},
    })
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    headings = [item for item in _chapter(document)['editorial'] if item['type'] == 'heading']
    assert len(headings) == 1
    assert headings[0]['canonical'] is True
    assert headings[0]['heading_type'] == 'psalm'
    assert headings[0]['attrs']['level'] == '1'
    assert_schemas(document)


def test_native_note_reference_is_not_discarded_when_visible_body_matches(tmp_path):
    record = entry(0, 1, 1, 'verse', 'Word<note>John</note>', 'Word')
    record['official_attributes'] = attributes(Footnote={'1': {
        'body': '<reference osisRef="John.3.16">John</reference>', 'type': 'study',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    references = _chapter(document).get('reference', {}).get('items', [])
    assert len(references) == 1
    assert references[0]['targets'][0]['value'] == 'John.3.16'
    assert_schemas(document)


def test_quote_state_continues_across_chapters_and_empty_entry_closes_it(tmp_path):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, 'verse', '<q sID="saying" who="Jesus"/><w>First</w>', 'First'),
        entry(1, 2, 1, 'verse', '<w>Second</w>', 'Second'),
        entry(2, 2, 2, 'verse', '<q eID="saying"/>', ''),
        entry(3, 2, 3, 'verse', '<w>Third</w>', 'Third'),
    ])
    chapters = document['books'][0]['chapters']
    assert chapters[0]['verses'][0]['spans'][0]['attrs']['who'] == 'Jesus'
    assert chapters[1]['verses'][0]['spans'][0]['attrs']['who'] == 'Jesus'
    assert chapters[1]['verses'][1]['spans'] == []
    assert_schemas(document)


def test_quote_state_is_reset_when_source_moves_to_another_book(tmp_path):
    from test_getbiblesword_converter import bv
    second = entry(1, 1, 1, 'verse', '<w>New book</w>', 'New book')
    second['scope'].update({
        'book_name': bv('Exodus'), 'book_abbreviation': bv('Exod'),
        'osis_reference': bv('Exod.1.1'), 'book': 2,
    })
    document, _ = _convert(tmp_path, _config(Genesis=1, Exodus=2), [
        entry(0, 1, 1, 'verse', '<q sID="saying" who="Jesus"/><w>First</w>', 'First'),
        second,
    ])
    assert document['books'][0]['chapters'][0]['verses'][0]['spans']
    assert document['books'][1]['chapters'][0]['verses'][0]['spans'] == []
    assert_schemas(document)


def test_declared_legacy_encoding_is_not_reinterpreted_as_accidental_utf8(tmp_path):
    import json
    from getbiblesword_converter import GetBibleSwordConverter
    from test_getbiblesword_converter import _module_records, bv_bytes, write_records
    record = entry(0, 1, 1, 'verse', '', '')
    # These two bytes happen to be valid UTF-8, but the module explicitly uses
    # Latin-1 and supplies the two source characters capital-A-tilde/copyright.
    record['raw'] = bv_bytes(b'<w>\xc3\xa9</w>')
    record['stripped'] = bv_bytes(b'\xc3\xa9')
    records = _module_records([record])
    records[1]['encoding']['name'] = 'Latin-1'
    contract = tmp_path / 'source.ndjson'
    write_records(contract, records)
    output = GetBibleSwordConverter(_config(Genesis=1), str(tmp_path / 'output')).convert(str(contract))
    document = json.loads(open(output, encoding='utf-8').read())
    verse = _chapter(document)['verses'][0]
    assert verse['text'] == '\u00c3\u00a9'
    assert verse['tokens'][0]['token'] == verse['text']
    assert_schemas(document)


def test_source_only_unknown_format_keeps_decoded_content_with_source_anchor(tmp_path):
    import json
    from getbiblesword_converter import GetBibleSwordConverter
    from test_getbiblesword_converter import _module_records, write_records
    raw = '<special>body</special>'
    records = _module_records([entry(0, 1, 1, 'verse', raw, '')])
    records[1]['markup']['name'] = 'OtherMarkup'
    contract = tmp_path / 'source.ndjson'
    write_records(contract, records)
    path = GetBibleSwordConverter(_config(Genesis=1), str(tmp_path / 'output')).convert(str(contract))
    document = json.loads(open(path, encoding='utf-8').read())
    chapter = _chapter(document)
    assert chapter['verses'] == []
    assert chapter['editorial'][0]['content'] == [raw]
    assert chapter['editorial'][0]['anchor'] == {'verse': 1, 'scope': 'source', 'alignment': 'unresolved'}
    assert_schemas(document)


def test_same_text_heading_reconciliation_respects_distinct_source_metadata(tmp_path):
    source = (
        '<title level="1">Heading<note>First</note></title>'
        '<title level="1">Heading<note>First</note></title>'
        '<title level="2">Heading<note>Second</note></title>Word'
    )
    document, _ = _convert(tmp_path, _config(Genesis=1), [entry(0, 1, 1, 'verse', source, 'Word')])
    editorial = _chapter(document)['editorial']
    heading = next(item for item in editorial if item['type'] == 'heading' and item['attrs']['level'] == '2')
    note_id = heading['content'][1]['attrs']['footnote_id']
    linked_note = next(item for item in editorial if item.get('id') == note_id)
    assert linked_note['text'] == 'Second'
    assert_schemas(document)


def test_native_only_heading_notes_and_references_use_separate_chapter_records(tmp_path):
    record = entry(0, 1, 1, 'verse', 'Word', 'Word')
    record['official_attributes'] = attributes(Heading={'Preverse': {
        '0': '<title>Heading<note n="a">See <reference osisRef="John.3.16">John</reference></note></title>',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    chapter = _chapter(document)
    heading = next(item for item in chapter['editorial'] if item['type'] == 'heading')
    note = next(item for item in chapter['editorial'] if item['type'] == 'footnote')
    reference = chapter['reference']['items'][0]
    assert heading['content'][1]['attrs']['footnote_id'] == note['id']
    assert heading['content'][1]['children'] == []
    assert note['text'] == 'See John'
    assert note['anchor'] == {'verse': 1, 'alignment': 'unresolved'}
    assert note['content'][1]['attrs']['reference_id'] == reference['id']
    assert reference['anchor']['note'] == note['id']
    assert reference['targets'][0]['value'] == 'John.3.16'
    assert_schemas(document)


def test_native_heading_already_in_source_does_not_duplicate_study(tmp_path):
    title = '<title>Heading<note>Heading note</note></title>'
    record = entry(0, 1, 1, 'verse', title + 'Word', 'Word')
    record['official_attributes'] = attributes(
        Heading={'Preverse': {'0': title}},
        Footnote={'1': {'body': 'Heading note'}},
    )
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    editorial = _chapter(document)['editorial']
    assert len([item for item in editorial if item['type'] == 'heading']) == 1
    assert len([item for item in editorial if item['type'] == 'footnote']) == 1
    assert_schemas(document)


def test_native_heading_and_footnote_groups_share_one_recovered_body(tmp_path):
    record = entry(0, 1, 1, 'verse', 'Word', 'Word')
    record['official_attributes'] = attributes(
        Heading={'Preverse': {'0': '<title>Heading<note>Heading note</note></title>'}},
        Footnote={'1': {'body': 'Heading note'}},
    )
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    editorial = _chapter(document)['editorial']
    notes = [item for item in editorial if item['type'] == 'footnote']
    assert len(notes) == 1
    heading = next(item for item in editorial if item['type'] == 'heading')
    assert heading['content'][1]['attrs']['footnote_id'] == notes[0]['id']
    assert_schemas(document)


def test_native_heading_notes_survive_an_empty_chapter_introduction(tmp_path):
    record = entry(0, 1, 0, 'chapter', '', '')
    record['official_attributes'] = attributes(Heading={'Preverse': {
        '0': '<title>Heading<note>Heading note</note></title>',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    chapter = _chapter(document)
    assert chapter['verses'] == []
    note = chapter['editorial'][0]
    assert note['anchor']['scope'] == 'introduction'
    assert chapter['titles'][0]['content'][1]['attrs']['footnote_id'] == note['id']
    assert_schemas(document)


def test_native_note_only_empty_introduction_has_valid_linked_content(tmp_path):
    record = entry(0, 1, 0, 'chapter', '', '')
    record['official_attributes'] = attributes(Footnote={'1': {'body': 'Native introduction note'}})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    chapter = _chapter(document)
    note = chapter['editorial'][0]
    assert chapter['introduction'][0]['content'][0]['attrs']['footnote_id'] == note['id']
    assert_schemas(document)


def test_native_preverse_with_stripped_wrapper_keeps_title_lexical_data_and_note_link(tmp_path):
    record = entry(0, 1, 1, 'verse', 'Word', 'Word')
    record['official_attributes'] = attributes(Heading={'Preverse': {
        '0': '<w lemma="strong:H1">Heading</w><note>Heading note</note>',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    editorial = _chapter(document)['editorial']
    heading = next(item for item in editorial if item['type'] == 'heading')
    note = next(item for item in editorial if item['type'] == 'footnote')
    assert heading['tokens'][0]['lemma'] == {'strong': ['H1']}
    assert heading['content'][1]['attrs']['footnote_id'] == note['id']
    assert_schemas(document)
