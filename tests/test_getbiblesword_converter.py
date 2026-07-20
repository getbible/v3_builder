import base64
import gc
import hashlib
import json
import weakref
from types import SimpleNamespace

import pytest

from converter import ConversionConfig
from getbiblesword_converter import ConversionError, GetBibleSwordConverter


def bv_bytes(data):
    value = {
        "base64": base64.b64encode(data).decode(),
        "encoding": "base64",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    try:
        value["utf8"] = data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return value


def bv(text):
    return bv_bytes(text.encode())


def entry(ordinal, chapter, verse, intro_scope, raw, stripped):
    return {
        "type": "entry",
        "ordinal": ordinal,
        "key": bv(f"Genesis {chapter}:{verse}"),
        "scope": {
            "type": "verse_key",
            "testament": 1,
            "book": 1,
            "book_name": bv("Genesis"),
            "book_abbreviation": bv("Gen"),
            "chapter": chapter,
            "verse": verse,
            "intro_scope": intro_scope,
            "osis_reference": bv("Gen.1.1"),
            "index": ordinal,
            "suffix": 0,
            "versification": bv("KJV"),
        },
        "raw": bv(raw),
        "rendered_default": bv(stripped),
        "stripped": bv(stripped),
        "projections_available": True,
        "official_attributes": [],
        "annotation_segments": [{"kind": "markup", "raw": bv("<w>")}],
    }


def write_contract(path):
    records = [
        {
            "type": "header", "command": "extract",
            "contract": "getbiblesword.ndjson/v1", "contract_version": 1,
            "producer": "getBibleSword", "producer_version": "0.1.0",
            "sword_version": "1.9.0",
        },
        {
            "type": "module", "classification": "bible",
            "name": bv("KJV"), "description": bv("King James Version"),
            "language": bv("en"), "driver": bv("zText"),
            "sword_type": bv("Biblical Texts"),
            "direction": {"code": 0, "name": "ltr"},
            "encoding": {"code": 2, "name": "utf8"},
            "markup": {"code": 7, "name": "osis"},
        },
        {
            "type": "config_source", "ordinal": 0,
            "path": bv("mods.d/kjv.conf"), "raw": bv("[KJV]\nLang=en\n"),
        },
        {"type": "config_entry", "ordinal": 0, "name": bv("Lang"), "value": bv("en")},
        {
            "type": "config_entry", "ordinal": 1,
            "name": bv("DistributionLicense"), "value": bv("Public Domain"),
        },
        entry(
            0, 0, 0, "book",
            '<title type="main">Genesis</title>', "Genesis",
        ),
        entry(
            1, 1, 0, "chapter",
            '<title type="chapter">Creation</title>', "Creation",
        ),
        entry(
            2, 1, 1, "verse",
            '<milestone marker="¶" type="x-p"/>'
            '<w lemma="strong:H07225">In the beginning</w>',
            "In the beginning",
        ),
    ]
    write_records(path, records)


def write_records(path, records):
    payload = b""
    counts = {}
    for sequence, record in enumerate(records):
        record = {
            "sequence": sequence,
            **{key: value for key, value in record.items() if key != "sequence"},
        }
        payload += json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        counts[record["type"]] = counts.get(record["type"], 0) + 1
    footer = {
        "sequence": len(records), "type": "footer", "counts": counts,
        "entries": 3, "artifacts": 0, "artifact_bytes": 0,
        "success": True, "stream_sha256": hashlib.sha256(payload).hexdigest(),
    }
    path.write_bytes(payload + json.dumps(footer, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def assert_no_lossless_envelopes(value):
    """Assert recursively that build-time contract records did not leak."""

    if isinstance(value, list):
        for item in value:
            assert_no_lossless_envelopes(item)
        return
    if not isinstance(value, dict):
        return
    assert "source" not in value
    assert "source_contract" not in value
    assert not {"base64", "encoding", "sha256", "size"}.issubset(value)
    for item in value.values():
        assert_no_lossless_envelopes(item)


def test_native_converter_emits_lean_semantic_api_shape(tmp_path):
    contract = tmp_path / "KJV.ndjson"
    output = tmp_path / "output"
    write_contract(contract)
    config = ConversionConfig(
        translation_names={"KJV": "kjv"},
        v1_translations={"kjv": "King James Version"},
        book_numbers={"Genesis": 1},
        book_names={"Genesis": "Genesis"},
        lang_correction={"en": "en"},
        language_names={"en": "English"},
        text_direction={"en": "LTR"},
    )
    result = GetBibleSwordConverter(config, str(output)).convert(str(contract), module_name="KJV")
    document = json.loads(open(result, encoding="utf-8").read())
    verse = document["books"][0]["chapters"][0]["verses"][0]
    assert verse["text"] == "In the beginning"
    assert verse["tokens"][0]["lemma"] == {"strong": ["H07225"]}
    assert verse["paragraph"] is True
    assert document["books"][0]["titles"] == [
        {"type": "main", "text": "Genesis"}
    ]
    assert document["books"][0]["chapters"][0]["titles"] == [
        {"type": "chapter", "text": "Creation"}
    ]
    assert document["distribution_license"] == "Public Domain"
    assert_no_lossless_envelopes(document)

    chapter_document = json.loads(
        (output / "kjv" / "1" / "1.json").read_text(encoding="utf-8")
    )
    book_document = json.loads(
        (output / "kjv" / "1.json").read_text(encoding="utf-8")
    )
    assert chapter_document["titles"][0]["text"] == "Creation"
    assert book_document["titles"][0]["text"] == "Genesis"
    assert_no_lossless_envelopes(chapter_document)
    assert_no_lossless_envelopes(book_document)


def test_native_converter_recovers_display_text_from_valid_raw_osis(tmp_path):
    contract = tmp_path / "KJV.ndjson"
    output = tmp_path / "output"
    write_contract(contract)

    records = [json.loads(line) for line in contract.read_text().splitlines()]
    verse = next(record for record in records if record.get("ordinal") == 2)
    verse["raw"] = bv(
        '<w lemma="strong:H03068">the '
        '<divineName>Lord’s</divineName></w>.'
    )
    # Reproduce the malformed projection returned by SWORD 1.9 for some KJV
    # curly apostrophes. Raw OSIS still provides a safe display fallback.
    verse["stripped"] = bv_bytes(b"the Lord\xc2\x80\x99s.")

    body = records[:-1]
    payload = b""
    counts = {}
    for sequence, record in enumerate(body):
        record["sequence"] = sequence
        payload += json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        counts[record["type"]] = counts.get(record["type"], 0) + 1
    footer = records[-1]
    footer.update({
        "sequence": len(body),
        "counts": counts,
        "stream_sha256": hashlib.sha256(payload).hexdigest(),
    })
    contract.write_bytes(
        payload
        + json.dumps(footer, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )

    config = ConversionConfig(
        translation_names={"KJV": "kjv"},
        v1_translations={"kjv": "King James Version"},
        book_numbers={"Genesis": 1},
        book_names={"Genesis": "Genesis"},
        lang_correction={"en": "en"},
        language_names={"en": "English"},
        text_direction={"en": "LTR"},
    )
    result = GetBibleSwordConverter(config, str(output)).convert(
        str(contract), module_name="KJV"
    )
    document = json.loads(open(result, encoding="utf-8").read())
    converted = document["books"][0]["chapters"][0]["verses"][0]

    assert converted["text"] == "the Lord’s."
    assert converted["tokens"][1]["token"] == "Lord’s"
    assert converted["spans"][0]["tag"] == "divineName"
    assert_no_lossless_envelopes(document)


def test_native_converter_fails_on_unmapped_contract_record(tmp_path):
    contract = tmp_path / "KJV.ndjson"
    write_contract(contract)
    records = [json.loads(line) for line in contract.read_text().splitlines()][:-1]
    records.insert(
        4,
        {"type": "future_semantic_record", "value": bv("unmapped")},
    )
    write_records(contract, records)

    config = ConversionConfig(
        translation_names={"KJV": "kjv"},
        v1_translations={"kjv": "King James Version"},
        book_numbers={"Genesis": 1},
        book_names={"Genesis": "Genesis"},
        lang_correction={"en": "en"},
        language_names={"en": "English"},
        text_direction={"en": "LTR"},
    )
    with pytest.raises(ConversionError, match="future_semantic_record"):
        GetBibleSwordConverter(config, str(tmp_path / "output")).convert(
            str(contract), module_name="KJV"
        )


def test_native_converter_releases_each_contract_entry_while_streaming(
    tmp_path, monkeypatch
):
    class WeakRecord(dict):
        pass

    state = {}
    module = {
        "type": "module",
        "classification": "bible",
        "name": bv("KJV"),
        "description": bv("King James Version"),
        "language": bv("en"),
        "direction": {"code": 0, "name": "ltr"},
        "encoding": {"code": 2, "name": "utf8"},
        "markup": {"code": 7, "name": "osis"},
    }

    def records(_path):
        yield module
        first = WeakRecord(
            entry(0, 1, 1, "verse", '<w lemma="strong:H1">One</w>', "One")
        )
        state["first"] = weakref.ref(first)
        yield first
        del first
        yield WeakRecord(
            entry(1, 1, 2, "verse", '<w lemma="strong:H2">Two</w>', "Two")
        )
        gc.collect()
        state["released_before_stream_end"] = state["first"]() is None

    monkeypatch.setattr(
        "getbiblesword_converter.validate_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            module_name="KJV", unknown_record_types=()
        ),
    )
    monkeypatch.setattr("getbiblesword_converter.iter_contract", records)

    config = ConversionConfig(
        translation_names={"KJV": "kjv"},
        v1_translations={"kjv": "King James Version"},
        book_numbers={"Genesis": 1},
        book_names={"Genesis": "Genesis"},
        lang_correction={"en": "en"},
        language_names={"en": "English"},
        text_direction={"en": "LTR"},
    )
    result = GetBibleSwordConverter(config, str(tmp_path / "output")).convert(
        str(tmp_path / "unused.ndjson"), module_name="KJV"
    )
    document = json.loads(open(result, encoding="utf-8").read())

    assert state["released_before_stream_end"] is True
    assert [
        verse["text"]
        for verse in document["books"][0]["chapters"][0]["verses"]
    ] == ["One", "Two"]
