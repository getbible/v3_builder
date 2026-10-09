# SPDX-License-Identifier: GPL-2.0-only
"""Source-format regressions from SWORD/BibleTime's documented tag conventions."""

import xml.etree.ElementTree as ET

import pytest

from source_formats import decode_source, normalize_source


def fragment(source, format):
    normalized, diagnostics = normalize_source(source, format)
    return ET.fromstring(f'<root>{normalized}</root>'), diagnostics


def test_osis_is_unchanged_even_when_fragment_is_entry_local():
    source = '<q eID="Q1"/><w lemma="strong:G0001">ἀρχή</w>'
    assert normalize_source(source, 'OSIS') == (source, [])


def test_osis_unclosed_outer_container_recovers_headings_tokens_and_notes():
    from osis_parser import parse_osis_semantics, parse_osis_verse
    from study_annotations import extract_study

    source = ('<div type="section"><title>Heading</title>'
              '<w lemma="strong:G3056">Word</w>'
              '<note n="a"><title>Note title</title><p>Note body</p></note>')
    normalized, diagnostics = normalize_source(source, 'OSIS')
    assert {item['code'] for item in diagnostics} == {
        'recovered_source_markup', 'unpaired_source_container',
    }
    semantics = parse_osis_semantics(normalized)
    assert [title['text'] for title in semantics['titles']] == ['Heading']
    parsed = parse_osis_verse(normalized, 'Word')
    assert parsed['tokens'][0]['lemma'] == {'strong': ['G3056']}
    study = extract_study(normalized, 'Word', 1)
    assert study['footnotes'][0]['text'] == 'Note title\nNote body'
    root = ET.fromstring(f'<root>{normalized}</root>')
    assert root.find('div/milestone').attrib == {'type': 'x-source-open', 'n': 'div'}


def test_osis_unmatched_closing_boundary_preserves_case_and_following_annotations():
    from osis_parser import parse_osis_verse
    from study_annotations import extract_study

    normalized, diagnostics = normalize_source(
        '</divineName></p><w>Word</w><note>Note</note>', 'OSIS')
    root = ET.fromstring(f'<root>{normalized}</root>')
    assert [element.attrib for element in root.findall('milestone')] == [
        {'type': 'x-source-end', 'n': 'divineName'},
        {'type': 'x-source-end', 'n': 'p'},
    ]
    assert parse_osis_verse(normalized, 'Word')['tokens'][0]['token'] == 'Word'
    assert extract_study(normalized, 'Word', 2)['footnotes'][0]['text'] == 'Note'
    assert 'unmatched_source_boundary' in {item['code'] for item in diagnostics}


def test_osis_recovery_does_not_double_decode_literals_or_remap_element_names():
    root, diagnostics = fragment(
        '<div xmlns:x="urn:source"><divineName x:code="a&amp;lt;b">LORD</divineName>'
        '<transChange type="added">&lt;note&gt; literal &amp;lt;title&amp;gt;</transChange>'
        '<w><![CDATA[&nbsp; <literal>]]></w>', 'OSIS')
    assert root.find('div/divineName').get('{urn:source}code') == 'a&lt;b'
    assert root.find('div/transChange').text == '<note> literal &lt;title&gt;'
    assert root.find('div/w').text == '&nbsp; <literal>'
    assert any(item['code'] == 'recovered_source_markup' for item in diagnostics)


def test_osis_open_quotation_is_local_and_reports_missing_cross_entry_pairing():
    from osis_parser import parse_osis_verse

    normalized, diagnostics = normalize_source('<q who="Jesus"><w>Come</w>', 'OSIS')
    parsed = parse_osis_verse(normalized, 'Come')
    assert parsed['spans'][0]['attrs']['who'] == 'Jesus'
    root = ET.fromstring(f'<root>{normalized}</root>')
    assert root.find('q/milestone').get('n') == 'q'
    assert any(item['code'] == 'unpaired_source_container' for item in diagnostics)


def test_osis_recovery_keeps_milestones_empty_and_reports_mismatched_containers():
    from osis_parser import parse_osis_verse

    normalized, diagnostics = normalize_source(
        '<div><milestone type="x-p"><hi type="italic"><w>Word</w></div>', 'OSIS')
    root = ET.fromstring(f'<root>{normalized}</root>')
    assert len(root.find('div/milestone')) == 0
    assert root.find('div/hi/milestone').attrib == {'type': 'x-source-open', 'n': 'hi'}
    assert parse_osis_verse(normalized, 'Word')['tokens'][0]['token'] == 'Word'
    assert any(item['code'] == 'unpaired_source_container' for item in diagnostics)


def test_osis_deep_fragment_and_entity_declarations_have_bounded_recovery():
    for source in (
        '<div>' * 150 + 'content' + '</div>' * 150,
        '<!DOCTYPE p [<!ENTITY x "replacement">]><p>&x;</p>',
    ):
        root, diagnostics = fragment(source, 'OSIS')
        assert root.text == source
        assert any(item['code'] == 'unparsed_source_fragment' for item in diagnostics)


@pytest.mark.parametrize('format', ['Plain', 'Plaintext', 'Text', ''])
def test_plain_source_does_not_interpret_literal_markup(format):
    source = 'Text <title>not a heading</title> & more'
    root, diagnostics = fragment(source, format)
    assert list(root) == []
    assert root.text == source
    assert diagnostics == []


def test_gbf_postfix_groups_follow_bibletime_convention():
    # Example appearing in BibleTime's gbftohtml.cpp. A lexical marker can
    # annotate a phrase and multiple markers can belong to the same phrase.
    root, diagnostics = fragment(
        'Am Anfang<WH07225> schuf<WH01254><WTH8804> Gott<WH0430> '
        'Himmel<WH08064> und<WT> Erde<WH0776>.', 'GBF')
    words = root.findall('w')
    assert [''.join(word.itertext()) for word in words] == [
        'Am Anfang', 'schuf', 'Gott', 'Himmel', 'und', 'Erde',
    ]
    assert words[0].get('lemma') == 'strong:H07225'
    assert words[1].attrib == {'lemma': 'strong:H01254', 'morph': 'strongMorph:TH8804'}
    assert words[4].attrib == {}
    assert ''.join(root.itertext()) == 'Am Anfang schuf Gott Himmel und Erde.'
    assert diagnostics == []


def test_gbf_lexical_markers_preserve_inline_format_and_multiple_values():
    root, diagnostics = fragment('<FI>the beginning<Fi><WG746><WG1722><WTN-NSF>', 'GBF')
    word = root.find('w')
    assert word.get('lemma') == 'strong:G746 strong:G1722'
    assert word.get('morph') == 'robinson:N-NSF'
    assert word.find('hi').get('type') == 'italic'
    assert diagnostics == []


def test_postfix_punctuation_between_markers_does_not_create_phantom_tokens():
    root, diagnostics = fragment('word<WG1>,<WG2> next<WG3>', 'GBF')
    assert len(root.findall('w')) == 2
    assert root.find('w').get('lemma') == 'strong:G1 strong:G2'
    assert ''.join(root.itertext()) == 'word, next'
    assert diagnostics == []


def test_gbf_heading_and_note_boundaries_do_not_capture_scripture():
    root, diagnostics = fragment(
        '<TS>Creation<Ts><CM>In the beginning<WH07225>'
        '<RF n="a"><TS>Note title<Ts><CM>Note body<CL>Second line<Rf>'
        ' God<WH0430>.', 'GBF')
    assert root.find('title').text == 'Creation'
    assert root.find('milestone').get('type') == 'x-p'
    note = root.find('note')
    assert note.get('n') == 'a'
    assert note.find('title').text == 'Note title'
    assert note.find('milestone').tail == 'Note body'
    assert note.find('lb').tail == 'Second line'
    assert [word.text for word in root.findall('w')] == ['In the beginning', 'God']
    assert diagnostics == []


def test_gbf_embedded_footnote_catchword_remains_in_main_text():
    root, diagnostics = fragment('A <RB>word<RF>A note<Rf> after.', 'GBF')
    assert root.find('seg').text == 'word'
    assert root.find('note').text == 'A note'
    assert root.find('note').tail == ' after.'
    assert diagnostics == []


def test_gbf_references_quotes_poetry_and_unknown_markers_are_retained():
    root, diagnostics = fragment(
        '<FR>Come<Fr> <FO>Scripture<Fo> <RX John 3:16>John 3:16<Rx>'
        '<PP>First<CL>Second<Pp><ZZ new="data">text<CA65><CT><CG>', 'GBF')
    assert root.find('q').get('who') == 'Jesus'
    assert root.find('reference').get('target') == 'John 3:16'
    assert root.find('lg/lb').tail == 'Second'
    retained = root.findall('milestone')
    assert [node.get('n') for node in retained] == ['ZZ new="data"', 'CA65']
    assert retained[-1].tail == '<>'
    assert {item['code'] for item in diagnostics} == {'unsupported_gbf_tag'}


def test_orphan_gbf_lexical_marker_is_reported_and_retained():
    root, diagnostics = fragment('<WH0430> God', 'GBF')
    assert root.find('milestone').attrib == {'type': 'x-lexical', 'lemma': 'strong:H0430'}
    assert diagnostics[0]['code'] == 'orphan_lexical_marker'
    assert root.find('w') is None


def test_thml_footnote_heading_and_scriprefs_retain_explicit_destinations():
    root, diagnostics = fragment(
        '<div class="sechead">Creation</div><p>The beginning'
        '<note n="a"><h3>Alternative reading</h3><p>Note body '
        '<scripRef passage="John 1:1">See John</scripRef></p></note></p>'
        '<scripRef>Genesis 1:1</scripRef>', 'ThML')
    assert root.find('title').get('type') == 'section'
    note = root.find('p/note')
    assert note.find('title').text == 'Alternative reading'
    assert note.find('p/reference').get('target') == 'John 1:1'
    assert note.find('p/reference').get('passage') == 'John 1:1'
    assert root.find('reference').get('target') == 'Genesis 1:1'
    assert diagnostics == []


def test_thml_sync_markers_preserve_lexical_schemes_without_threshold_guessing():
    root, diagnostics = fragment(
        'beginning<sync type="Strongs" value="G746|G1722"/>'
        '<sync type="morph" class="robinson" value="N-DSF"/> '
        'earth<sync type="Strongs" value="H7760"/>', 'ThML')
    words = root.findall('w')
    assert words[0].attrib == {'lemma': 'strong:G746 strong:G1722', 'morph': 'robinson:N-DSF'}
    assert words[1].get('lemma') == 'strong:H7760'
    assert diagnostics == []


def test_extra_thml_sync_attributes_survive_in_the_lexical_content_tree():
    root, diagnostics = fragment('word<sync type="Strongs" value="G3056" n="7"/>', 'ThML')
    assert root.find('w').get('x-source-sync-n') == '7'
    assert diagnostics == []


def test_legacy_thml_html_syntax_entities_and_missing_closures_are_recovered():
    root, diagnostics = fragment(
        '<div class=sechead>A &amp; B</div><p>Word&nbsp;'
        '<sync type=Strongs value=G3056><br>next<p>last', 'ThML')
    assert root.find('title').text == 'A & B'
    assert root.find('p/w').get('lemma') == 'strong:G3056'
    assert root.find('p/lb').tail == 'next'
    assert root.findall('p')[-1].text == 'last'
    assert any(item['code'] == 'recovered_source_markup' for item in diagnostics)


def test_thml_tables_figures_and_variants_map_without_losing_attributes():
    root, diagnostics = fragment(
        '<table class="comparison"><tr><th colspan="2">Header</th>'
        '<td><img src="image.png" alt="Map"/></td></tr></table>'
        '<div type="variant" class="a">Alternate</div>', 'ThML')
    assert root.find('table').get('class') == 'comparison'
    assert root.find('table/row/cell').attrib == {'colspan': '2', 'role': 'label'}
    assert root.find('table/row/cell/figure').attrib == {'src': 'image.png', 'alt': 'Map'}
    assert root.find('seg').attrib == {'type': 'x-variant', 'class': 'a', 'subType': 'x-class:a'}
    assert diagnostics == []


def test_tei_references_highlights_headings_and_unknown_analysis_survive():
    root, diagnostics = fragment(
        '<head type="chapter">Introduction</head><p><hi rend="small-caps">LORD</hi>'
        '<note place="foot"><head>Note title</head><p>Body</p></note>'
        '<ref osisRef="Gen.1.1">Genesis</ref><ref target="Lexicon:entry">Entry</ref>'
        '<w lemma="λόγος" ana="#analysis1">λόγος</w></p>', 'TEI')
    assert root.find('title').get('type') == 'chapter'
    assert root.find('p/hi').attrib == {'rend': 'small-caps', 'type': 'small-caps'}
    assert root.find('p/note/title').text == 'Note title'
    references = root.findall('p/reference')
    assert references[0].get('osisRef') == 'Gen.1.1'
    assert references[1].get('target') == 'Lexicon:entry'
    assert root.find('p/w').get('ana') == '#analysis1'
    assert root.find('p/w').get('morph') is None
    assert diagnostics[0]['code'] == 'unresolved_tei_analysis'


def test_unknown_xml_tree_attributes_and_text_are_preserved():
    root, diagnostics = fragment('<extension x="y">before<hi rend="bold">inside</hi>after</extension>', 'TEI')
    node = root.find('extension')
    assert node.get('x') == 'y'
    assert node.text == 'before'
    assert node.find('hi').tail == 'after'
    assert diagnostics[0]['code'] == 'unsupported_source_element'


def test_namespaced_tei_remains_parseable_and_keeps_language():
    root, diagnostics = fragment(
        '<p xmlns="http://www.tei-c.org/ns/1.0" xml:lang="el">'
        '<hi rend="italic">λόγος</hi></p>', 'TEI')
    paragraph = root[0]
    assert paragraph.tag == '{http://www.tei-c.org/ns/1.0}p'
    assert paragraph.get('{http://www.w3.org/XML/1998/namespace}lang') == 'el'
    assert paragraph[0].get('type') == 'italic'
    assert diagnostics == []


def test_entity_declarations_are_never_expanded():
    source = '<!DOCTYPE p [<!ENTITY x "replacement">]><p>&x;</p>'
    root, diagnostics = fragment(source, 'TEI')
    assert root.text == source
    assert diagnostics[0]['code'] == 'unparsed_source_fragment'


def test_deep_fragment_has_bounded_recovery():
    source = '<p>' * 150 + 'content' + '</p>' * 150
    root, diagnostics = fragment(source, 'ThML')
    assert root.text == source
    assert any(item['code'] == 'unparsed_source_fragment' for item in diagnostics)


def test_unsupported_format_retains_content_with_diagnostic():
    source = '<special>body</special>'
    root, diagnostics = fragment(source, 'OtherMarkup')
    assert root.text == source
    assert diagnostics[0]['code'] == 'unsupported_source_format'


@pytest.mark.parametrize(('source', 'encoding', 'expected'), [
    ('λόγος שלום'.encode(), 'UTF-8', 'λόγος שלום'),
    ('café'.encode('latin-1'), 'Latin-1', 'café'),
    (b'\x91word\x92\x81', 'Windows-1252', '‘word’\x81'),
    (b'\x91word\x92', 'ISO-8859-1', '\x91word\x92'),
    ('λόγος'.encode('utf-16'), 'UTF-8', 'λόγος'),
    ('שלום'.encode('utf-16-le'), 'UTF-16', 'שלום'),
    ('λόγος'.encode('utf-16-be'), 'UTF-16BE', 'λόγος'),
    ('café'.encode('utf-8-sig'), 'Latin-1', 'café'),
    (b'\xef\xbb\xbf' + 'λόγος '.encode() + b'\xe9', 'Latin-1', 'λόγος é'),
    ('λόγος '.encode() + b'\x93word\x94\xe9', 'UTF-8', 'λόγος “word”é'),
])
def test_encoding_is_explicit_and_mixed_utf8_recovery_is_local(source, encoding, expected):
    assert decode_source(source, encoding) == expected


@pytest.mark.parametrize(('data', 'encoding'), [
    (b'\xff\xfeA', 'UTF-8'),
    (b'A\x00B', 'UTF-16'),
    (b'\x00\xd8', 'UTF-16LE'),
    (b'\xd8\x00', 'UTF-16BE'),
    (b'\x0e\x04\x01', 'SCSU'),
])
def test_incomplete_or_unsupported_unicode_requires_explicit_diagnostic(data, encoding):
    with pytest.raises(UnicodeError):
        decode_source(data, encoding)


def test_unknown_encoding_is_never_silently_guessed():
    with pytest.raises(ValueError, match='Unsupported source encoding'):
        decode_source(b'\xff', 'UnknownEncoding')


def test_explicit_additional_standard_library_codec_is_honoured():
    assert decode_source('Привет'.encode('cp1251'), 'cp1251') == 'Привет'
