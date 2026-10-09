"""Lexical variant wrappers use existing spans without duplicating every word."""

import pytest

from osis_parser import parse_osis_verse
from study_annotations import extract_study
from test_getbiblesword_converter import _config, _convert, entry
from test_study_conversion import assert_schemas


SOURCE = (
    '<seg type="x-variant" subType="x-1" custom="" source="A"> '
    '<w lemma="strong:G2532" morph="packard:C" xlit="betacode:KAI">καὶ</w> '
    '<w lemma="strong:G2532" morph="packard:C" xlit="betacode:KAI">καὶ</w>'
    '\n<w lemma="strong:G2316" morph="packard:N2" xlit="betacode:QEOS">θεός</w> '
    '</seg>'
)


@pytest.mark.parametrize("display", ["καὶ καὶ θεός", "unrelated display"])
def test_variant_wrapper_and_lexical_words_reconstruct_from_existing_fields(tmp_path, display):
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, "verse", SOURCE, display),
    ])
    chapter = document["books"][0]["chapters"][0]
    verse = chapter["verses"][0]
    span, = verse["spans"]
    assert span["tag"] == "seg"
    assert span["attrs"] == {"type": "x-variant", "subType": "x-1", "custom": "", "source": "A"}
    assert span["span"] == "καὶ καὶ\nθεός"
    assert (span["token_start"], span["token_end"]) == (0, 2)
    words = verse["tokens"][span["token_start"]:span["token_end"] + 1]
    assert [word["token"] for word in words] == ["καὶ", "καὶ", "θεός"]
    assert [word["lemma"] for word in words] == [{"strong": ["G2532"]}, {"strong": ["G2532"]}, {"strong": ["G2316"]}]
    assert [word["morph"] for word in words] == [{"packard": ["C"]}, {"packard": ["C"]}, {"packard": ["N2"]}]
    assert [word["xlit"] for word in words] == [{"betacode": ["KAI"]}, {"betacode": ["KAI"]}, {"betacode": ["QEOS"]}]
    assert not chapter.get("editorial")
    if display == "unrelated display":
        assert (span["word_start"], span["word_end"]) == (0, 0)
    assert_schemas(document)


def test_adjacent_identical_segments_keep_independent_boundaries():
    source = '<seg type="x-variant"><w>same</w></seg> <seg type="x-variant"><w>same</w></seg>'
    parsed = parse_osis_verse(source, "same same")
    assert [(span["token_start"], span["token_end"]) for span in parsed["spans"]] == [(0, 0), (1, 1)]
    assert not extract_study(source, "same same", 1, context={"spans": parsed["spans"]})["content"]


def test_standalone_study_extraction_keeps_structure_without_exported_spans():
    assert extract_study(SOURCE, "καὶ καὶ θεός", 1)["content"]
    assert extract_study(SOURCE, "καὶ καὶ θεός", 1, context={"spans": [{
        "tag": "seg", "span": "καὶ καὶ θεός", "attrs": {"type": "x-variant"},
    }]})["content"]


@pytest.mark.parametrize("source", [
    '<seg type="x-variant"/>',
    '<seg type="x-variant"><w/></seg>',
    '<seg type="x-variant">prefix<w>word</w></seg>',
    '<seg type="x-variant"><w>word</w>suffix</seg>',
    '<seg type="x-variant"><w>word</w><note>explanation</note></seg>',
    '<seg type="x-variant"><w><hi type="italic">word</hi></w></seg>',
    '<seg type="x-variant"><w>word</w><unknown>extra</unknown></seg>',
    '<seg type="x-variant"><seg><w>word</w></seg></seg>',
    '<w><seg type="x-variant"><w>word</w></seg></w>',
    '<seg type="x-variant"><w> word </w></seg>',
    '<seg xmlns="urn:custom" type="x-variant"><w>word</w></seg>',
    '<seg type="x-variant"><w xmlns:x="urn:custom" x:lemma="special">word</w></seg>',
    '<seg type="x-variant"><w lemma="">word</w></seg>',
    '<seg type="x-variant"><w word_start="original">word</w></seg>',
    '<seg type="x-variant" xml:space="preserve"> <w>word</w> </seg>',
    '<hi xml:space="preserve"><seg type="x-variant"> <w>word</w> </seg></hi>',
])
def test_unrepresented_source_details_keep_the_complete_structure(source):
    parsed = parse_osis_verse(source) or {"spans": []}
    assert extract_study(source, "word", 1, context={"spans": parsed["spans"]})["content"]


@pytest.mark.parametrize("display", ["λόγος θεός ", "λόγος θεός"])
def test_trailing_chapter_markers_keep_exact_unicode_endpoint_without_lexical_copies(tmp_path, display):
    source = (
        '<w lemma="strong:G3056">λόγος</w> <w lemma="strong:G2316">θεός</w> '
        '<seg n="empty"/><chapter eID="ch-1" osisID="Gen.1"/>'
    )
    document, output = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, "verse", source, display),
    ])
    chapter = document["books"][0]["chapters"][0]
    structure, = chapter["editorial"]
    assert structure["anchor"] == {"verse": 1, "offset": len(display)}
    assert structure["content"] == [
        {"tag": "seg", "attrs": {"n": "empty"}, "children": []},
        {"tag": "chapter", "attrs": {"eID": "ch-1", "osisID": "Gen.1"}, "children": []},
    ]
    assert [word["lemma"] for word in chapter["verses"][0]["tokens"]] == [
        {"strong": ["G3056"]}, {"strong": ["G2316"]},
    ]
    import json
    assert json.loads((output / "lxx/1/1.json").read_text())["editorial"] == chapter["editorial"]
    assert_schemas(document)


def test_trailing_marker_after_variant_keeps_both_independent_representations(tmp_path):
    source = SOURCE + '<chapter eID="ch-1" osisID="Gen.1"/>'
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, "verse", source, "καὶ καὶ θεός"),
    ])
    chapter = document["books"][0]["chapters"][0]
    assert len(chapter["verses"][0]["spans"]) == 1
    assert chapter["editorial"][0]["content"] == [
        {"tag": "chapter", "attrs": {"eID": "ch-1", "osisID": "Gen.1"}, "children": []},
    ]
    assert_schemas(document)


def test_terminal_book_and_chapter_markers_keep_both_source_identities(tmp_path):
    source = (
        '<w lemma="strong:G3056">λόγος</w> '
        '<chapter eID="ch-1" osisID="Gen.1"/>'
        '<div type="book" eID="book-1" osisID="Gen"/>'
    )
    document, _ = _convert(tmp_path, _config(Genesis=1), [
        entry(0, 1, 1, "verse", source, "λόγος"),
    ])
    chapter = document["books"][0]["chapters"][0]
    structure, = chapter["editorial"]
    assert structure["anchor"] == {"verse": 1, "offset": 5}
    assert structure["content"] == [
        {"tag": "chapter", "attrs": {"eID": "ch-1", "osisID": "Gen.1"}, "children": []},
        {"tag": "div", "attrs": {"type": "book", "eID": "book-1", "osisID": "Gen"}, "children": []},
    ]
    assert_schemas(document)


@pytest.mark.parametrize("source,display", [
    ('<w>word</w><chapter eID="ch-1"/>', 'unrelated'),
    ('<w>word</w><chapter eID="ch-1"/>', 'word extra'),
    ('<w>word</w><chapter eID="ch-1"/>suffix', 'wordsuffix'),
    ('<w>word</w><chapter eID="ch-1"/><w>more</w>', 'wordmore'),
    ('<w>word</w><chapter eID="ch-1">body</chapter>', 'wordbody'),
    ('<w>word</w><chapter eID="ch-1" xml:space="preserve"/>', 'word'),
    ('<w><hi>word</hi></w><chapter eID="ch-1"/>', 'word'),
    ('<w>word</w><unknown/><chapter eID="ch-1"/>', 'word'),
    ('<seg><w>word</w><chapter eID="ch-1"/></seg>', 'word'),
    ('<w>word</w><chapter xmlns="urn:custom" eID="ch-1"/>', 'word'),
])
def test_trailing_marker_compaction_keeps_full_tree_when_position_or_content_is_unrepresented(source, display):
    parsed = parse_osis_verse(source, display) or {"tokens": [], "spans": []}
    study = extract_study(source, display, 1, context=parsed)
    assert study["content"]
    assert "anchor" not in study


def test_trailing_markers_need_exported_tokens_and_preserve_requested_full_content():
    source = '<w>word</w><chapter eID="ch-1"/>'
    assert "anchor" not in extract_study(source, "word", 1)
    parsed = parse_osis_verse(source, "word")
    assert "anchor" not in extract_study(source, "word", 1, context={**parsed, "retain_content": True})
