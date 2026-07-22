import base64
import hashlib

from getbiblesword_converter import GetBibleSwordConverter


MALFORMED_RAW = b'<w lemma="strong:G0976">book</w>\xcf'


def byte_value(data: bytes) -> dict:
    value = {
        "base64": base64.b64encode(data).decode("ascii"),
        "encoding": "base64",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    try:
        value["utf8"] = data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return value


def verse_record(*, raw: bytes, rendered: bytes) -> dict:
    return {
        "ordinal": 1,
        "key": byte_value(b"Matthew 1:1"),
        "scope": {"type": "verse_key", "intro_scope": "verse"},
        "raw": byte_value(raw),
        "rendered_default": byte_value(rendered),
        "stripped": byte_value(b"book"),
        "projections_available": True,
        "official_attributes": [],
        "annotation_segments": [],
    }


def test_verse_uses_rendered_osis_when_raw_is_not_utf8():
    record = verse_record(
        raw=MALFORMED_RAW,
        rendered=b'<w lemma="strong:G0976">book</w>',
    )

    verse = GetBibleSwordConverter._verse(
        record,
        "Matthew",
        1,
        1,
        "osis",
    )

    assert verse is not None
    assert verse["text"] == "book"
    assert verse["tokens"][0]["lemma"] == {"strong": ["G0976"]}
    assert "source" not in verse
    assert "titles" not in verse


def test_verse_survives_when_no_osis_projection_is_utf8():
    record = verse_record(raw=b"raw\xcf", rendered=b"rendered\xcf")

    verse = GetBibleSwordConverter._verse(
        record,
        "Matthew",
        1,
        1,
        "osis",
    )

    assert verse is not None
    assert verse["text"] == "book"
    assert "tokens" not in verse
    assert "spans" not in verse
    assert "source" not in verse
    assert "titles" not in verse


def test_verse_decodes_legacy_single_byte_text_without_rejecting_module():
    record = verse_record(
        raw=b"Ya si Se\xf1ot guiaguan g\xfciya.<CL>",
        rendered=b"Ya si Se\xf1ot guiaguan g\xfciya.<CL>",
    )
    record["stripped"] = byte_value(b"Ya si Se\xf1ot guiaguan g\xfciya.\n")

    verse = GetBibleSwordConverter._verse(
        record,
        "Psalms",
        1,
        2,
        "gbf",
    )

    assert verse is not None
    assert verse["text"] == "Ya si Se\u00f1ot guiaguan g\u00fciya.\n"
    assert "tokens" not in verse
    assert "spans" not in verse


def test_verse_preserves_valid_utf8_around_legacy_single_bytes():
    record = verse_record(raw=b"unused", rendered=b"unused")
    record["stripped"] = byte_value(
        "\u1f10\u03bd \u1f00\u03c1\u03c7\u1fc7 ".encode("utf-8") + b"Se\xf1ot"
    )

    verse = GetBibleSwordConverter._verse(
        record,
        "John",
        1,
        1,
        "plain",
    )

    assert verse is not None
    assert verse["text"] == "\u1f10\u03bd \u1f00\u03c1\u03c7\u1fc7 Se\u00f1ot"


def test_verse_uses_cp1252_for_legacy_sword_punctuation():
    record = verse_record(raw=b"unused", rendered=b"unused")
    record["stripped"] = byte_value(b"\x93Se\xf1ot\x94 \x97 \x80")

    verse = GetBibleSwordConverter._verse(
        record,
        "Psalms",
        1,
        2,
        "gbf",
    )

    assert verse is not None
    assert verse["text"] == "\u201cSe\u00f1ot\u201d \u2014 \u20ac"
    assert "\ufffd" not in verse["text"]
    assert not any(0x80 <= ord(character) <= 0x9F for character in verse["text"])
