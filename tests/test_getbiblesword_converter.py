import base64
import hashlib
import json

from converter import ConversionConfig
from getbiblesword_converter import GetBibleSwordConverter


def bv(text):
    data = text.encode()
    return {
        "base64": base64.b64encode(data).decode(),
        "encoding": "base64",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "utf8": text,
    }


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
        {"type": "config_entry", "ordinal": 0, "name": bv("Lang"), "value": bv("en")},
        {
            "type": "config_entry", "ordinal": 1,
            "name": bv("DistributionLicense"), "value": bv("Public Domain"),
        },
        entry(0, 0, 0, "book", "<title>Genesis</title>", "Genesis"),
        entry(1, 1, 0, "chapter", "<title>Creation</title>", "Creation"),
        entry(
            2, 1, 1, "verse",
            '<w lemma="strong:H07225">In the beginning</w>',
            "In the beginning",
        ),
    ]
    payload = b""
    counts = {}
    for sequence, record in enumerate(records):
        record = {"sequence": sequence, **record}
        payload += json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        counts[record["type"]] = counts.get(record["type"], 0) + 1
    footer = {
        "sequence": len(records), "type": "footer", "counts": counts,
        "entries": 3, "artifacts": 0, "artifact_bytes": 0,
        "success": True, "stream_sha256": hashlib.sha256(payload).hexdigest(),
    }
    path.write_bytes(payload + json.dumps(footer, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def test_native_converter_preserves_lossless_source_and_existing_api_shape(tmp_path):
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
    assert verse["source"]["raw"]["utf8"].startswith("<w")
    assert verse["source"]["annotation_segments"][0]["kind"] == "markup"
    assert document["books"][0]["introduction"][0]["text"] == "Genesis"
    assert document["books"][0]["chapters"][0]["introduction"][0]["text"] == "Creation"
    assert document["distribution_license"] == "Public Domain"
    assert document["source_contract"]["contract"] == "getbiblesword.ndjson/v1"
