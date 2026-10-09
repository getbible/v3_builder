"""Regression cases for faithful chapter study data and source-based anchoring."""

import json

import pytest

from book_identity import BookResolver
from study_annotations import extract_study


def test_footnote_preserves_title_paragraphs_nesting_and_source_attributes():
    source = ('The <w>he</w><note type="explanation" n="a" osisID="John.1.1!a">'
              '<title>Reading</title><p>First <hi type="italic">note</hi>.</p>'
              '<p>Second <foreign xml:lang="el">λόγος</foreign>.</p></note> spoke.')
    output = extract_study(source, 'The he spoke.', 1)
    assert output['references'] == []
    assert output['content'] == []
    assert output['diagnostics'] == []
    note = output['footnotes'][0]
    assert note['id'] == 'fn-1-1'
    assert note['anchor'] == {'verse': 1, 'offset': 6}
    assert note['text'] == 'Reading\nFirst note.\nSecond λόγος.'
    assert note['attrs'] == {'type': 'explanation', 'n': 'a', 'osisID': 'John.1.1!a'}
    assert note['content'][0] == {'tag': 'title', 'children': ['Reading']}
    assert note['content'][1]['children'][1] == {
        'tag': 'hi', 'attrs': {'type': 'italic'}, 'children': ['note'],
    }
    assert note['content'][2]['children'][1]['attrs'] == {
        '{http://www.w3.org/XML/1998/namespace}lang': 'el',
    }


def test_crossreference_note_becomes_one_separate_reference_group():
    source = ('Start<note type="crossReference" n="x">See '
              '<reference osisRef="Gen.1.1 John.3.16-John.3.18">these passages</reference>'
              ' and <reference osisRef="Rev.1.1">Revelation</reference>.</note> here')
    output = extract_study(source, 'Start here', 9)
    assert output['footnotes'] == []
    assert len(output['references']) == 1
    reference = output['references'][0]
    assert reference['id'] == 'ref-9-1'
    assert reference['anchor'] == {'verse': 9, 'offset': 5}
    assert reference['text'] == 'See these passages and Revelation.'
    assert reference['targets'] == [
        {'value': 'Gen.1.1', 'scheme': 'osis', 'book': 1, 'chapter': 1, 'verse': 1},
        {'value': 'John.3.16-John.3.18', 'scheme': 'osis', 'book': 43, 'chapter': 3,
         'verse': 16, 'end': {'book': 43, 'chapter': 3, 'verse': 18}},
        {'value': 'Rev.1.1', 'scheme': 'osis', 'book': 66, 'chapter': 1, 'verse': 1},
    ]
    assert reference['content'][1]['attrs']['reference_id'] == 'ref-9-1'


def test_reference_inside_footnote_links_to_note_and_correct_local_text_offset():
    source = ('Word<note><title>Note</title><p>See '
              '<reference osisRef="John.3.16">John</reference>.</p></note> end')
    output = extract_study(source, 'Word end', 4)
    note, reference = output['footnotes'][0], output['references'][0]
    assert note['text'] == 'Note\nSee John.'
    assert reference['anchor'] == {'verse': 4, 'offset': 4,
                                   'note': 'fn-4-1', 'note_offset': 9}
    assert note['text'][reference['anchor']['note_offset']:].startswith('John')
    link = note['content'][1]['children'][1]
    assert link['attrs']['reference_id'] == reference['id']
    assert link['children'] == ['John']


def test_nested_notes_have_deterministic_separate_bodies_and_links():
    source = 'Text<note>Outer <hi>formatted<note n="b">Inner</note></hi> end.</note>'
    output = extract_study(source, 'Text', 7)
    assert [note['id'] for note in output['footnotes']] == ['fn-7-1', 'fn-7-2']
    assert [note['text'] for note in output['footnotes']] == ['Outer formatted end.', 'Inner']
    link = output['footnotes'][0]['content'][1]['children'][1]
    assert link == {'tag': 'note', 'attrs': {'n': 'b', 'footnote_id': 'fn-7-2'}, 'children': []}
    assert extract_study(source, 'Text', 7) == output


def test_lexical_notes_are_preserved_without_entering_verse_or_structure():
    output = extract_study('A<note type="strongsMarkup">Lexical payload</note> B', 'A B', 1)
    assert output['content'] == []
    assert output['footnotes'][0]['attrs']['type'] == 'strongsMarkup'
    assert output['footnotes'][0]['text'] == 'Lexical payload'


@pytest.mark.parametrize(('source', 'display', 'offset'), [
    ('the <w>he</w><note>a</note>', 'the he', 6),
    ('λόγος 😀<note>a</note> λόγος', 'λόγος 😀 λόγος', 7),
    ('\n  First\n   second<note>a</note> third', 'First second third', 12),
    ('<note>a</note>Start', 'Start', 0),
    ('End<note>a</note>', 'End', 3),
])
def test_note_offsets_count_display_unicode_codepoints(source, display, offset):
    assert extract_study(source, display, 1)['footnotes'][0]['anchor'] == {
        'verse': 1, 'offset': offset,
    }


def test_unalignable_text_keeps_note_without_invented_character_offset():
    output = extract_study('AAA<note>Important</note>', 'ZZZ', 1)
    assert output['footnotes'][0]['anchor'] == {'verse': 1, 'alignment': 'unresolved'}
    assert output['diagnostics'][0]['code'] == 'study_anchor_unresolved'


def test_structural_tree_preserves_poetry_tables_figures_and_unknown_elements():
    source = ('<lg><l level="1">First<note>A note body</note></l><l>Second</l></lg>'
              '<table><row><cell>A</cell><cell>B</cell></row></table>'
              '<figure src="illustration.png"><caption>Caption</caption></figure>'
              '<custom foo="bar">Kept</custom>')
    output = extract_study(source, 'FirstSecondABKept', 2)
    assert [node['tag'] for node in output['content']] == ['lg', 'table', 'figure', 'custom']
    assert output['content'][0]['children'][0]['attrs'] == {'level': '1'}
    assert output['content'][0]['children'][0]['children'][1]['attrs']['footnote_id'] == 'fn-2-1'
    assert 'A note body' not in json.dumps(output['content'])
    assert output['content'][2]['attrs'] == {'src': 'illustration.png'}
    assert output['content'][2]['children'][0]['children'] == ['Caption']
    assert output['content'][3] == {'tag': 'custom', 'attrs': {'foo': 'bar'}, 'children': ['Kept']}


def test_unknown_structure_in_note_does_not_duplicate_main_text():
    output = extract_study('Text<note><custom attr="yes">Note</custom></note>', 'Text', 1)
    assert output['content'] == []
    assert output['footnotes'][0]['content'][0]['attrs'] == {'attr': 'yes'}


@pytest.mark.parametrize(('attributes', 'expected'), [
    ('osisRef="Bible:John.3.16"', {'value': 'Bible:John.3.16', 'scheme': 'osis', 'book': 43, 'chapter': 3, 'verse': 16}),
    ('osisRef="Ps.119"', {'value': 'Ps.119', 'scheme': 'osis', 'book': 19, 'chapter': 119}),
    ('osisRef="Gen"', {'value': 'Gen', 'scheme': 'osis', 'book': 1}),
    ('osisRef="1En.1.1"', {'value': '1En.1.1', 'scheme': 'osis', 'book': 87, 'chapter': 1, 'verse': 1}),
    ('osisRef="John.0.1"', {'value': 'John.0.1', 'scheme': 'unresolved'}),
    ('osisRef="Josephus:Ant.1.1"', {'value': 'Josephus:Ant.1.1', 'scheme': 'unresolved'}),
    ('target="John 3:16"', {'value': 'John 3:16', 'scheme': 'unresolved'}),
    ('passage="Psalm 1"', {'value': 'Psalm 1', 'scheme': 'unresolved'}),
    ('target="https://example.org/passage"', {'value': 'https://example.org/passage', 'scheme': 'uri'}),
    ('target="#note-one"', {'value': '#note-one', 'scheme': 'local'}),
    ('target="https://["', {'value': 'https://[', 'scheme': 'unresolved'}),
])
def test_reference_target_resolution_preserves_unresolved_values(attributes, expected):
    output = extract_study(f'<reference {attributes}>See</reference>', 'See', 1)
    assert output['references'][0]['targets'] == [expected]
    assert bool(output['diagnostics']) == (expected['scheme'] == 'unresolved')


def test_unknown_explicit_osis_book_uses_same_stable_extension_as_source_books():
    resolver = BookResolver({}, {})
    output = extract_study('<reference osisRef="Herm.Mand.1.2">Mandates</reference>',
                           'Mandates', 1, context={'book_resolver': resolver})
    target = output['references'][0]['targets'][0]
    assert target['book'] == resolver.resolve('', 'Herm.Mand.1.2').number
    assert target['chapter'] == 1 and target['verse'] == 2


def test_source_without_target_preserves_visible_reference_label_as_unresolved():
    output = extract_study('<note type="crossReference">Compare John 3:16</note>Text', 'Text', 1)
    assert output['references'][0]['targets'] == [
        {'value': 'Compare John 3:16', 'scheme': 'unresolved'},
    ]


def test_malformed_xml_reports_unparsed_study_without_rejecting_display_text():
    output = extract_study('Text<note>broken', 'Text', 1)
    assert output['footnotes'] == []
    assert output['diagnostics'][0]['code'] == 'study_markup_unparsed'


def test_unknown_milestone_and_midverse_paragraph_are_retained_for_reconstruction():
    output = extract_study('First<milestone type="x-page" n="12"/><p>Second</p>',
                           'FirstSecond', 1)
    assert output['content'][1] == {'tag': 'milestone',
                                  'attrs': {'type': 'x-page', 'n': '12'}, 'children': []}
    assert output['content'][2]['tag'] == 'p'


@pytest.mark.parametrize(('source_format', 'source', 'display'), [
    ('ThML', '<div class="sechead">Creation</div><p>Word<note n="a">'
             '<h3>Note heading</h3><p>Body</p><scripRef passage="John 1:1">See John</scripRef>'
             '</note></p>', 'Word'),
    ('TEI', '<head>Creation</head><p>Word<note place="foot">'
            '<head>Note heading</head><p>Body</p><ref osisRef="John.1.1">See John</ref>'
            '</note></p>', 'Word'),
    ('GBF', '<TS>Creation<Ts>Word<RF><TS>Note heading<Ts><CM>Body'
            '<RX John 1:1>See John<Rx><Rf>', 'Word'),
])
def test_normalized_legacy_sources_keep_complete_notes_and_references(source_format, source, display):
    from source_formats import normalize_source

    normalized, diagnostics = normalize_source(source, source_format)
    assert not diagnostics
    result = extract_study(normalized, display, 1)
    assert result['footnotes'][0]['anchor'] == {'verse': 1, 'offset': 4}
    assert result['footnotes'][0]['text'].startswith('Note heading\nBody')
    assert result['references'][0]['anchor']['note'] == 'fn-1-1'
    assert result['references'][0]['text'] == 'See John'
    assert result['references'][0]['targets'][0]['value'] in {'John 1:1', 'John.1.1'}


def test_unrecognized_gbf_information_remains_in_structural_content():
    from source_formats import normalize_source

    normalized, diagnostics = normalize_source('<WH0430>Word<ZZ value="data">', 'GBF')
    assert diagnostics
    result = extract_study(normalized, 'Word', 1)
    assert result['content'][0]['attrs'] == {'type': 'x-lexical', 'lemma': 'strong:H0430'}
    assert result['content'][-1]['attrs'] == {'type': 'x-gbf', 'n': 'ZZ value="data"'}


def test_retain_content_keeps_intro_title_and_paragraphs_with_note_links():
    source = ('<title>Introduction</title><p>Some prose<note>Full note body</note> '
              '<reference osisRef="Gen.1.1">Genesis</reference></p>')
    output = extract_study(source, 'Some prose Genesis', 0, context={'retain_content': True})
    assert output['content'][0] == {'tag': 'title', 'children': ['Introduction']}
    paragraph = output['content'][1]
    assert paragraph['tag'] == 'p'
    assert paragraph['children'][1] == {
        'tag': 'note', 'attrs': {'footnote_id': 'fn-0-1'}, 'children': [],
    }
    assert paragraph['children'][3]['attrs']['reference_id'] == 'ref-0-1'
    assert 'Full note body' not in json.dumps(output['content'])
    assert output['footnotes'][0]['text'] == 'Full note body'


def test_retain_content_does_not_change_default_for_simple_markup():
    source = '<title>Heading</title><p>Prose</p>'
    assert extract_study(source, 'Prose', 1)['content'] == []
    assert extract_study(source, 'Prose', 1, context={'retain_content': True})['content'] == [
        {'tag': 'title', 'children': ['Heading']},
        {'tag': 'p', 'children': ['Prose']},
    ]


@pytest.mark.parametrize(('display', 'context'), [('', None), ('Text', {'retain_content': True})])
def test_unparsed_source_only_content_is_retained_as_inert_text(display, context):
    source = 'Text<note>unclosed <p>body'
    output = extract_study(source, display, 0, context=context)
    assert output['content'] == [source]
    assert output['footnotes'] == []
    assert output['references'] == []
    assert output['diagnostics'][0]['code'] == 'study_markup_unparsed'


@pytest.mark.parametrize(('segment_type', 'extra'), [
    ('x-caps', ''), ('x-nested', 'subType="inner"'),
    ('x-morph', 'n="prefix"'), ('x-variant', 'subType="reading-b"'),
])
def test_segments_retain_exact_boundaries_and_all_attributes(segment_type, extra):
    source = f'<w lemma="strong:H03068">The <seg type="{segment_type}" {extra}>LORD</seg></w>'
    result = extract_study(source, 'The LORD', 1)
    word = result['content'][0]
    assert word['attrs'] == {'lemma': 'strong:H03068'}
    assert word['children'][0] == 'The '
    assert word['children'][1]['tag'] == 'seg'
    assert word['children'][1]['attrs']['type'] == segment_type
    assert word['children'][1]['children'] == ['LORD']


@pytest.mark.parametrize('source', [
    '<div type="paragraph" sID="paragraph-a"/>Text<div type="paragraph" eID="paragraph-a"/>',
    '<milestone type="x-p" n="opening" sID="p1"/>Text<milestone type="x-p" eID="p1"/>',
    '<div type="section" osisID="section-a" xml:lang="he">Text</div>',
    '<verse osisID="Gen.1.1" x-label="custom">Text</verse>',
])
def test_container_boundary_attributes_survive_exact_tree_roundtrip(source):
    import xml.etree.ElementTree as ET

    output = extract_study(source, 'Text', 1)
    assert output['content']

    def restore(values, parent):
        for value in values:
            if isinstance(value, str):
                if len(parent):
                    parent[-1].tail = (parent[-1].tail or '') + value
                else:
                    parent.text = (parent.text or '') + value
                continue
            child = ET.SubElement(parent, value['tag'], value.get('attrs', {}))
            restore(value['children'], child)

    rebuilt = ET.Element('r')
    restore(output['content'], rebuilt)
    expected = ET.fromstring(f'<r>{source}</r>')
    assert ET.tostring(rebuilt) == ET.tostring(expected)


def test_fully_represented_lexical_and_span_data_does_not_duplicate_full_source_tree():
    source = ('<milestone type="x-p"/><w lemma="strong:H03068" src="1 2">'
              '<hi type="italic"><divineName>LORD</divineName></hi></w> '
              '<seg type="x-transChange">is</seg>')
    assert extract_study(source, 'LORD is', 1)['content'] == []


def test_nested_note_anchor_locates_inner_note_in_parent_body():
    source = 'Word<note><title>Title</title><p>Outer <note>Inner</note> tail</p></note>'
    result = extract_study(source, 'Word', 1)
    assert result['footnotes'][0]['text'] == 'Title\nOuter  tail'
    assert result['footnotes'][1]['anchor'] == {
        'verse': 1, 'offset': 4, 'note': 'fn-1-1', 'note_offset': 12,
    }


def test_main_title_content_reuses_study_links_without_duplicating_note_bodies():
    source = ('<title type="section" n="a">Heading<note>Heading note<title>Private title</title></note> '
              '<reference osisRef="Gen.1.1">Genesis</reference></title>Text'
              '<note><title>Not a chapter title</title>Body</note>')
    result = extract_study(source, 'Text', 1)
    assert len(result['title_content']) == 1
    heading = result['title_content'][0]
    assert heading['text'] == 'Heading Genesis'
    assert heading['attrs'] == {'type': 'section', 'n': 'a'}
    assert heading['content'][1] == {
        'tag': 'note', 'attrs': {'footnote_id': 'fn-1-1'}, 'children': [],
    }
    assert heading['content'][3]['attrs']['reference_id'] == 'ref-1-1'
    assert 'Heading note' not in json.dumps(heading)
    assert result['footnotes'][0]['text'] == 'Heading note\nPrivate title'
