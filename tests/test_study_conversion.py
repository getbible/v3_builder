# SPDX-License-Identifier: GPL-2.0-only
"""Contract-level checks for additive study projection, beyond parser fixtures."""

import json
from pathlib import Path

import pytest

from getbiblesword_converter import GetBibleSwordConverter
from openapi import openapi_document
from test_getbiblesword_converter import (
    _config, _convert, _module_records, bv, bv_bytes, entry, write_records,
)
from test_openapi import _validate


def assert_schemas(document):
    description = openapi_document(["lxx"], mount="/v3", schema_dir=str(Path(__file__).resolve().parents[1] / "schema"))
    _validate(description, "translation", document)


def attributes(**groups):
    return [
        {"name": bv(group), "lists": [
            {"name": bv(name), "values": [
                {"name": bv(key), "value": bv(value)}
                for key, value in values.items()
            ]} for name, values in lists.items()
        ]} for group, lists in groups.items()
    ]


def test_study_is_identical_at_all_document_levels_and_roundtrips(tmp_path):
    source = (
        '<title type="section">Creation</title><p><w lemma="strong:H1">Word</w>'
        '<note n="a"><title>Explanation</title><p>Body <hi type="italic">text</hi>'
        '<reference osisRef="John.3.16">John 3:16</reference></p></note>'
        '<note type="crossReference"><reference osisRef="Gen.1.2-Gen.1.3">Next</reference></note>'
        '<lg><l>poetry</l></lg></p>'
    )
    records = [entry(0, 1, 1, "verse", source, "Wordpoetry")]
    document, output = _convert(tmp_path, _config(Genesis=1), records)
    chapter = document["books"][0]["chapters"][0]
    book = json.loads((output / "lxx/1.json").read_text())
    standalone = json.loads((output / "lxx/1/1.json").read_text())
    for field in ("editorial", "reference"):
        assert chapter[field] == book["chapters"][0][field] == standalone[field]
    assert set(chapter["verses"][0]) <= {"chapter", "verse", "name", "text", "paragraph", "tokens", "spans"}
    assert [item["type"] for item in chapter["editorial"]].count("footnote") == 1
    note = next(item for item in chapter["editorial"] if item["type"] == "footnote")
    assert note["text"] == "Explanation\nBody textJohn 3:16"
    assert note["content"][0]["tag"] == "title"
    assert not any(item.get("text") == "Explanation" for item in chapter["editorial"] if item["type"] == "heading")
    references = chapter["reference"]["items"]
    assert references[0]["anchor"]["note"] == note["id"]
    assert references[0]["targets"][0]["book"] == 43
    assert references[1]["targets"][0]["end"] == {"book": 1, "chapter": 1, "verse": 3}
    assert_schemas(document)
    first = (output / "lxx.json").read_bytes()
    _convert(tmp_path, _config(Genesis=1), records)
    assert (output / "lxx.json").read_bytes() == first


def test_official_headings_supplement_without_duplicating_raw_titles(tmp_path):
    record = entry(0, 1, 1, "verse", '<title type="section">Existing</title>Word', "Word")
    record["official_attributes"] = attributes(Heading={"Preverse": {
        "0": '<title type="section">Existing</title>',
        "1": '<title type="section" canonical="true">Recovered</title>',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    editorial = document["books"][0]["chapters"][0]["editorial"]
    assert [item["text"] for item in editorial] == ["Existing", "Recovered"]
    assert editorial[1]["canonical"] is True
    assert_schemas(document)


def test_official_note_fallback_is_retained_without_inventing_position(tmp_path):
    record = entry(0, 1, 1, "verse", 'Word', 'Word')
    record["official_attributes"] = attributes(Footnote={"1": {"body": "Recovered note", "type": "study"}})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    note = document["books"][0]["chapters"][0]["editorial"][0]
    assert note["text"] == "Recovered note"
    assert note["anchor"] == {"verse": 1, "alignment": "unresolved"}
    assert_schemas(document)


def test_native_interverse_heading_and_reference_list_are_preserved(tmp_path):
    record = entry(0, 1, 1, "verse", "Word", "Word")
    record["official_attributes"] = attributes(
        Heading={"Interverse": {"0": "Recovered heading"}, "0": {"type": "section"}},
        Footnote={"1": {"body": "See the prophet", "type": "crossReference", "refList": "Isaiah 7:14"}},
    )
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    chapter = document["books"][0]["chapters"][0]
    assert chapter["editorial"][0]["text"] == "Recovered heading"
    reference = chapter["reference"]["items"][0]
    assert reference["text"] == "See the prophet"
    assert reference["targets"] == [{"value": "Isaiah 7:14", "scheme": "unresolved"}]
    assert reference["attrs"]["refList"] == "Isaiah 7:14"
    assert_schemas(document)


@pytest.mark.parametrize("markup,source", [
    ("gbf", '<TS>Heading<Ts>Word<RF>note<Rf>'),
    ("thml", '<h2>Heading</h2>Word<note>note</note>'),
    ("tei", '<head>Heading</head>Word<note>note</note>'),
])
def test_native_source_formats_share_the_public_contract(tmp_path, markup, source):
    records = _module_records([entry(0, 1, 1, "verse", source, "Word")])
    records[1]["markup"]["name"] = markup
    contract = tmp_path / "input.ndjson"
    write_records(contract, records)
    path = GetBibleSwordConverter(_config(Genesis=1), str(tmp_path / "out")).convert(str(contract))
    document = json.loads(open(path).read())
    chapter = document["books"][0]["chapters"][0]
    assert [(item["type"], item.get("text")) for item in chapter["editorial"]] == [
        ("heading", "Heading"), ("footnote", "note")
    ]
    assert_schemas(document)


def test_native_utf8_projection_preserves_non_utf8_raw_envelope(tmp_path):
    record = entry(0, 1, 1, "verse", "unused", "unused")
    record["raw"] = bv_bytes('<title>Héading</title>Wörd<note>Nöte</note>'.encode("utf-16"))
    record["normalized_raw"] = bv('<title>Héading</title>Wörd<note>Nöte</note>')
    record["normalized_stripped"] = bv("Wörd")
    records = _module_records([record])
    records[1]["encoding"]["name"] = "utf16"
    contract = tmp_path / "input.ndjson"
    write_records(contract, records)
    path = GetBibleSwordConverter(_config(Genesis=1), str(tmp_path / "out")).convert(str(contract))
    document = json.loads(open(path).read())
    chapter = document["books"][0]["chapters"][0]
    assert chapter["verses"][0]["text"] == "Wörd"
    assert [item["text"] for item in chapter["editorial"]] == ["Héading", "Nöte"]
    assert_schemas(document)


def test_source_only_note_and_title_keep_source_locations(tmp_path):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, "verse", "Word", "Word"),
        entry(1, 1, 2, "verse", '<note>Note without verse</note>', ""),
        entry(2, 1, 3, "verse", '<title>Final heading</title>', ""),
    ])
    chapter = document["books"][0]["chapters"][0]
    assert [verse["verse"] for verse in chapter["verses"]] == [1]
    assert [item["anchor"]["verse"] for item in chapter["editorial"]] == [2, 3]
    assert all(item["anchor"]["scope"] == "source" for item in chapter["editorial"])
    assert_schemas(document)


def test_note_only_chapter_introduction_keeps_content_and_links(tmp_path):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 0, "chapter", '<note>Intro note <reference osisRef="Gen.1.1">beginning</reference></note>', ""),
    ])
    chapter = document["books"][0]["chapters"][0]
    assert chapter["verses"] == []
    note = chapter["editorial"][0]
    assert note["anchor"]["scope"] == "introduction"
    assert note["anchor"]["introduction"] == 0
    assert chapter["introduction"][0]["content"][0]["attrs"]["footnote_id"] == note["id"]
    assert chapter["reference"]["items"][0]["anchor"]["note"] == note["id"]
    assert_schemas(document)


def test_native_humanized_reference_list_enriches_unique_source_citation(tmp_path):
    record = entry(0, 1, 1, 'verse',
                   'Word<note type="crossReference">Psalm 91:11-12 </note>', 'Word')
    record['official_attributes'] = attributes(Footnote={'1': {
        'body': 'Psalm 91:11-12 ', 'type': 'crossReference',
        'refList': 'Psalms 91:11-Psalms 91:12',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    references = document['books'][0]['chapters'][0]['reference']['items']
    assert len(references) == 1
    reference = references[0]
    assert reference['id'] == 'ref-1-1'
    assert reference['anchor'] == {'verse': 1, 'offset': 4}
    assert reference['text'] == 'Psalm 91:11-12'
    assert reference['attrs']['refList'] == 'Psalms 91:11-Psalms 91:12'
    assert reference['targets'] == [{'value': 'Psalm 91:11-12', 'scheme': 'unresolved'}]
    assert_schemas(document)


def test_native_explicit_target_enriches_unique_source_fallback_citation(tmp_path):
    record = entry(0, 1, 1, 'verse',
                   'Word<note type="crossReference">Psalm 91:11-12</note>', 'Word')
    record['official_attributes'] = attributes(Footnote={'1': {
        'body': 'Psalm 91:11-12', 'type': 'crossReference',
        'osisRef': 'Ps.91.11-Ps.91.12',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    references = document['books'][0]['chapters'][0]['reference']['items']
    assert len(references) == 1
    assert references[0]['anchor'] == {'verse': 1, 'offset': 4}
    assert references[0]['text'] == 'Psalm 91:11-12'
    assert references[0]['targets'] == [{
        'value': 'Ps.91.11-Ps.91.12', 'scheme': 'osis', 'book': 19,
        'chapter': 91, 'verse': 11, 'end': {'book': 19, 'chapter': 91, 'verse': 12},
    }]
    assert_schemas(document)


def test_same_reference_label_with_distinct_explicit_targets_stays_distinct(tmp_path):
    record = entry(0, 1, 1, 'verse',
                   'Word<note type="crossReference"><reference osisRef="Gen.1.1">See</reference></note>', 'Word')
    record['official_attributes'] = attributes(Footnote={'1': {
        'body': 'See', 'type': 'crossReference', 'osisRef': 'Gen.2.1',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    references = document['books'][0]['chapters'][0]['reference']['items']
    assert len(references) == 2
    assert [item['targets'][0]['value'] for item in references] == ['Gen.1.1', 'Gen.2.1']
    assert references[0]['anchor'] == {'verse': 1, 'offset': 4}
    assert references[1]['anchor'] == {'verse': 1, 'alignment': 'unresolved'}
    assert_schemas(document)


def test_native_reference_does_not_guess_between_equal_source_labels(tmp_path):
    record = entry(0, 1, 1, 'verse',
                   'Word<note type="crossReference">See</note>more'
                   '<note type="crossReference">See</note>', 'Wordmore')
    record['official_attributes'] = attributes(Footnote={'1': {
        'body': 'See', 'type': 'crossReference', 'refList': 'Isaiah 7:14',
    }})
    document, _ = _convert(tmp_path, _config(Genesis=1), [record])
    references = document['books'][0]['chapters'][0]['reference']['items']
    assert len(references) == 3
    assert [item['anchor'] for item in references] == [
        {'verse': 1, 'offset': 4}, {'verse': 1, 'offset': 8},
        {'verse': 1, 'alignment': 'unresolved'},
    ]
    assert_schemas(document)
