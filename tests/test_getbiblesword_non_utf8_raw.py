import base64
import hashlib

from getbiblesword_converter import GetBibleSwordConverter


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
        "stripped": byte_value(b"The book of the generation"),
        "projections_available": True,
        "official_attributes": [],
        "annotation_segments": [],
    }


def test_verse_uses_rendered_osis_when_raw_is_not_utf8():
    record = verse_record(
        raw=b'<w lemma="strong:G0976">book</w>\xcf',
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
    assert verse["text"] == "The book of the generation"
    assert verse["tokens"][0]["lemma"] == {"strong": ["G0976"]}
    assert "utf8" not in verse["source"]["raw"]
    assert verse["source"]["raw"]["base64"] == base64.b64encode(
        record["raw"] and b'<w lemma="strong:G0976">book</w>\xcf'
    ).decode("ascii")


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
    assert verse["text"] == "The book of the generation"
    assert "tokens" not in verse
    assert "spans" not in verse
    assert "utf8" not in verse["source"]["raw"]
    assert "utf8" not in verse["source"]["rendered_default"]
