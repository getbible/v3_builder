"""Tests for openapi.py: the generated description of the static tree."""

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from converter import ConversionConfig
from getbiblesword_converter import GetBibleSwordConverter
from hasher import ContentHasher
from openapi import (
    CHECKSUM_NAME,
    DOCUMENT_NAME,
    SCHEMA_NAMES,
    describe_tree,
    mount_from_base_url,
    openapi_document,
    version_label,
)
from test_getbiblesword_converter import bv, entry, write_records

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPOSITORY_ROOT / "schema"

EXPECTED_PATHS = [
    "/v3/translations.json",
    "/v3/checksum.json",
    "/v3/openapi.json",
    "/v3/openapi.sha",
    "/v3/{translation}.json",
    "/v3/{translation}.sha",
    "/v3/{translation}/books.json",
    "/v3/{translation}/checksum.json",
    "/v3/{translation}/{book}.json",
    "/v3/{translation}/{book}.sha",
    "/v3/{translation}/{book}/chapters.json",
    "/v3/{translation}/{book}/checksum.json",
    "/v3/{translation}/{book}/{chapter}.json",
    "/v3/{translation}/{book}/{chapter}.sha",
]


def _references(node):
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        for value in node.values():
            found.extend(_references(value))
        return found
    if isinstance(node, list):
        return [ref for value in node for ref in _references(value)]
    return []


def _resolve(document, pointer):
    assert pointer.startswith("#/"), pointer
    node = document
    for token in pointer[2:].split("/"):
        node = node[token]
    return node


def _validator(document, name):
    """A validator for one embedded schema, resolving inside the description."""

    schema = {
        "$ref": f"#/components/schemas/{name}",
        "components": document["components"],
    }
    return jsonschema.Draft202012Validator(schema)


def _validate(document, name, instance):
    errors = sorted(
        _validator(document, name).iter_errors(instance), key=lambda error: error.path
    )
    assert not errors, "\n".join(
        f"{name} {'/'.join(str(part) for part in error.path)}: {error.message}"
        for error in errors
    )


# ── Description shape ────────────────────────────────────────────────────────


def test_the_description_names_no_host_and_starts_at_the_mount():
    document = openapi_document(["kjv", "aov"], mount="/v3", schema_dir=str(SCHEMA_DIR))

    assert document["openapi"] == "3.1.0"
    assert "servers" not in document
    assert document["info"]["title"] == "GetBible Scripture v3"
    assert document["info"]["version"] == "3"
    text = json.dumps(document)
    assert "http://" not in text and "https://" not in text
    assert all(path.startswith("/v3/") for path in document["paths"])
    for operation in document["paths"].values():
        assert set(operation) == {"get"}
        assert set(operation["get"]["responses"]) == {"200", "404"}
    assert [tag["name"] for tag in document["tags"]] == [
        "tree", "translation", "book", "chapter",
    ]


def test_every_document_of_the_tree_is_described():
    document = openapi_document(["kjv"], mount="/v3", schema_dir=str(SCHEMA_DIR))

    assert list(document["paths"]) == EXPECTED_PATHS
    chapter = document["paths"]["/v3/{translation}/{book}/{chapter}.json"]["get"]
    assert chapter["parameters"] == [
        {"$ref": "#/components/parameters/translation"},
        {"$ref": "#/components/parameters/book"},
        {"$ref": "#/components/parameters/chapter"},
    ]
    assert chapter["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/chapter"
    }
    checksum = document["paths"]["/v3/{translation}/{book}/{chapter}.sha"]["get"]
    assert list(checksum["responses"]["200"]["content"]) == ["text/plain"]
    assert checksum["responses"]["200"]["content"]["text/plain"]["schema"] == {
        "$ref": "#/components/schemas/checksum"
    }
    operation_ids = [
        operation["get"]["operationId"] for operation in document["paths"].values()
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_the_translation_parameter_lists_the_translations_of_the_build():
    document = openapi_document(["web", "kjv"], mount="/v3", schema_dir=str(SCHEMA_DIR))
    parameter = document["components"]["parameters"]["translation"]
    assert parameter["schema"]["enum"] == ["kjv", "web"]
    assert parameter["in"] == "path" and parameter["required"] is True

    empty = openapi_document([], mount="/v3", schema_dir=str(SCHEMA_DIR))
    assert "enum" not in empty["components"]["parameters"]["translation"]["schema"]
    book = empty["components"]["parameters"]["book"]["schema"]
    assert book == {"type": "integer", "minimum": 1, "maximum": 83}


def test_the_mount_follows_the_public_base_url():
    document = openapi_document(["kjv"], mount="/v1", schema_dir=str(SCHEMA_DIR))
    assert all(path.startswith("/v1/") for path in document["paths"])
    assert document["info"]["title"] == "GetBible Scripture v1"
    assert document["info"]["version"] == "1"
    assert "`v1`" in document["info"]["description"]


@pytest.mark.parametrize(
    ("base_url", "mount"),
    [
        ("https://api.example.test/v3", "/v3"),
        ("https://api.example.test/v3/", "/v3"),
        ("https://example.test/bible/v1", "/bible/v1"),
        ("https://example.test", ""),
        ("https://example.test/", ""),
    ],
)
def test_mount_from_base_url(base_url, mount):
    assert mount_from_base_url(base_url) == mount


@pytest.mark.parametrize("mount", ["", "/", "/api", "/v3/kjv", "/version3"])
def test_a_mount_without_a_version_segment_is_refused(mount):
    with pytest.raises(ValueError, match="version segment"):
        version_label(mount)
    with pytest.raises(ValueError, match="version segment"):
        openapi_document(["kjv"], mount=mount, schema_dir=str(SCHEMA_DIR))


# ── Embedded schemas ─────────────────────────────────────────────────────────


def test_the_checked_in_schemas_are_embedded_and_every_reference_resolves_inside():
    document = openapi_document([], mount="/v3", schema_dir=str(SCHEMA_DIR))
    components = document["components"]["schemas"]

    assert list(components) == list(SCHEMA_NAMES)
    assert sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json")) == sorted(
        f"{name}.schema.json" for name in SCHEMA_NAMES
    )
    for name, schema in components.items():
        assert "$id" not in schema and "$schema" not in schema
        published = json.loads(
            (SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        )
        assert published["$id"] == f"{name}.schema.json"
        assert schema["title"] == published["title"]
        jsonschema.Draft202012Validator.check_schema(schema)
    for reference in _references(document):
        _resolve(document, reference)
    # The translation document reaches a verse through two embedded levels.
    books = _resolve(document, "#/components/schemas/translation/properties/books/items")
    assert books == {"$ref": "#/components/schemas/book/$defs/entry"}
    chapters = _resolve(document, "#/components/schemas/book/$defs/entry/properties/chapters/items")
    assert chapters == {"$ref": "#/components/schemas/chapter/$defs/entry"}
    verses = _resolve(document, "#/components/schemas/chapter/$defs/entry/properties/verses/items")
    assert verses == {"$ref": "#/components/schemas/verse"}


def test_a_schema_that_refers_outside_the_tree_is_refused(tmp_path):
    for name in SCHEMA_NAMES:
        (tmp_path / f"{name}.schema.json").write_text(
            json.dumps({"title": name, "type": "object"}), encoding="utf-8"
        )
    (tmp_path / "verse.schema.json").write_text(
        json.dumps(
            {
                "title": "verse",
                "type": "object",
                "properties": {"notes": {"$ref": "note.schema.json"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not publish"):
        openapi_document(["kjv"], mount="/v3", schema_dir=str(tmp_path))


# ── Generated documents against the embedded schemas ─────────────────────────


def _write_rich_contract(path):
    """A contract exercising every optional member the converter can emit."""

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
        {"type": "config_entry", "ordinal": 2, "name": bv("Version"), "value": bv("2.9")},
        {
            "type": "config_entry", "ordinal": 3,
            "name": bv("History_2.9"), "value": bv("Updated markup"),
        },
        entry(
            0, 0, 0, "module",
            '<title type="main">The Holy Bible</title><p>Module preface.</p>',
            "Module preface.",
        ),
        entry(
            1, 0, 0, "book",
            '<title type="main">Genesis</title><p>Book preface.</p>',
            "Book preface.",
        ),
        entry(
            2, 1, 0, "chapter",
            '<title type="chapter">Creation</title>', "Creation",
        ),
        entry(
            3, 1, 1, "verse",
            '<title canonical="true" type="section" subType="x-preverse">The Creation</title>'
            '<milestone marker="¶" type="x-p"/>'
            '<w lemma="strong:H07225" morph="oshm:HNcfsa" src="1">In the beginning</w> '
            '<w lemma="strong:H0430 strong:H0430" src="2 3" n="1" type="x-split">God</w> '
            '<transChange type="added">created</transChange> '
            '<q who="God"><w lemma="strong:H0776"><divineName>Lord</divineName></w></q>',
            "\r\n\nIn the beginning God created Lord",
        ),
        entry(
            4, 1, 2, "verse",
            '<w lemma="strong:H0776"><seg type="x-variant" subType="x-1">And the earth</seg></w>',
            "And the earth",
        ),
        entry(5, 2, 1, "verse", "Thus the heavens", "Thus the heavens"),
        # A chapter introduction without verse text stays nested with no document.
        entry(
            6, 3, 0, "chapter",
            '<title type="chapter">Three</title>Chapter preface.',
            "Chapter preface.",
        ),
    ]
    write_records(path, records)


@pytest.fixture
def described_tree(tmp_path):
    contract = tmp_path / "KJV.ndjson"
    output = tmp_path / "output"
    _write_rich_contract(contract)
    config = ConversionConfig(
        translation_names={"KJV": "kjv"},
        v1_translations={"kjv": "King James Version"},
        book_numbers={"Genesis": 1},
        book_names={"Genesis": "Genesis"},
        lang_correction={"en": "en"},
        language_names={"en": "English"},
        text_direction={"en": "LTR"},
    )
    GetBibleSwordConverter(config, str(output)).convert(str(contract), module_name="KJV")
    ContentHasher(str(output)).hash_all()
    describe_tree(str(output), mount="/v3", schema_dir=str(SCHEMA_DIR))
    document = json.loads((output / DOCUMENT_NAME).read_text(encoding="utf-8"))
    return output, document


def test_generated_documents_validate_against_the_embedded_schemas(described_tree):
    output, document = described_tree

    translation = json.loads((output / "kjv.json").read_text(encoding="utf-8"))
    _validate(document, "translation", translation)
    assert translation["titles"] == [{"type": "main", "text": "The Holy Bible"}]
    assert translation["introduction"] == [{"text": "Module preface."}]
    assert translation["distribution_history"] == {"history_2.9": "Updated markup"}

    book = json.loads((output / "kjv" / "1.json").read_text(encoding="utf-8"))
    _validate(document, "book", book)
    assert book["titles"] == [{"type": "main", "text": "Genesis"}]
    assert book["introduction"] == [{"text": "Book preface."}]
    nested = book["chapters"]
    assert [chapter["chapter"] for chapter in nested] == [1, 2, 3]
    assert nested[2]["verses"] == [] and "titles" in nested[2]
    assert not (output / "kjv" / "1" / "3.json").exists()

    for chapter_number in (1, 2):
        chapter = json.loads(
            (output / "kjv" / "1" / f"{chapter_number}.json").read_text(encoding="utf-8")
        )
        _validate(document, "chapter", chapter)
    first = json.loads((output / "kjv" / "1" / "1.json").read_text(encoding="utf-8"))
    verse = first["verses"][0]
    assert verse["paragraph"] is True
    assert verse["text"] == "In the beginning God created Lord"
    assert {span["tag"] for span in verse["spans"]} >= {"transChange", "q", "divineName"}
    assert verse["tokens"][1]["src"] == [2, 3]
    assert first["verses"][1]["tokens"][0]["variant"] is True
    assert first["editorial"][0]["type"] == "heading"
    assert first["editorial"][-1]["type"] == "paragraph"

    _validate(
        document, "translations-index",
        json.loads((output / "translations.json").read_text(encoding="utf-8")),
    )
    _validate(
        document, "books-index",
        json.loads((output / "kjv" / "books.json").read_text(encoding="utf-8")),
    )
    _validate(
        document, "chapters-index",
        json.loads((output / "kjv" / "1" / "chapters.json").read_text(encoding="utf-8")),
    )
    for relative in ("checksum.json", "kjv/checksum.json", "kjv/1/checksum.json"):
        _validate(
            document, "checksum-index",
            json.loads((output / relative).read_text(encoding="utf-8")),
        )
    checksums = sorted(output.rglob("*.sha"))
    assert checksums
    for path in checksums:
        _validate(document, "checksum", path.read_text(encoding="utf-8"))


def test_the_embedded_schemas_reject_documents_outside_the_contract(described_tree):
    output, document = described_tree
    chapter = json.loads((output / "kjv" / "1" / "1.json").read_text(encoding="utf-8"))

    leaked = json.loads(json.dumps(chapter))
    leaked["source"] = {"raw": "must not ship"}
    assert not _validator(document, "chapter").is_valid(leaked)

    titled = json.loads(json.dumps(chapter))
    titled["titles"] = [{"text": "Duplicate heading"}]
    assert not _validator(document, "chapter").is_valid(titled)

    leading = json.loads(json.dumps(chapter))
    leading["verses"][0]["text"] = "\n" + leading["verses"][0]["text"]
    assert not _validator(document, "chapter").is_valid(leading)

    tokens_only = json.loads(json.dumps(chapter))
    del tokens_only["verses"][0]["spans"]
    assert not _validator(document, "chapter").is_valid(tokens_only)

    bad_order = json.loads(json.dumps(chapter))
    bad_order["editorial"][0]["order"] = "0"
    assert not _validator(document, "chapter").is_valid(bad_order)

    assert not _validator(document, "checksum").is_valid("deadbeef\n")
    assert not _validator(document, "checksum-index").is_valid({"kjv": "deadbeef"})


def test_describe_tree_writes_the_document_and_its_checksum(described_tree):
    output, document = described_tree

    content = (output / DOCUMENT_NAME).read_bytes()
    assert content.endswith(b"\n") and b"\n" not in content[:-1]
    assert content == (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha1(content).hexdigest()
    assert (output / CHECKSUM_NAME).read_text(encoding="utf-8") == digest + "\n"
    assert document["components"]["parameters"]["translation"]["schema"]["enum"] == ["kjv"]
    assert document["components"]["schemas"] == openapi_document(
        ["kjv"], mount="/v3", schema_dir=str(SCHEMA_DIR)
    )["components"]["schemas"]
    # The description and its checksum are not treated as translations.
    index = json.loads((output / "translations.json").read_text(encoding="utf-8"))
    assert list(index) == ["kjv"]


def test_describe_tree_lists_every_translation_of_the_index(tmp_path):
    (tmp_path / "translations.json").write_text(
        json.dumps({"web": {}, "aov": {}, "kjv": {}}), encoding="utf-8"
    )

    path = describe_tree(str(tmp_path), mount="/v3", schema_dir=str(SCHEMA_DIR))

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    assert Path(path) == tmp_path / DOCUMENT_NAME
    assert document["components"]["parameters"]["translation"]["schema"]["enum"] == [
        "aov", "kjv", "web",
    ]


def test_describe_tree_requires_the_translations_index(tmp_path):
    with pytest.raises(FileNotFoundError):
        describe_tree(str(tmp_path), mount="/v3", schema_dir=str(SCHEMA_DIR))
    (tmp_path / "translations.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="translations index"):
        describe_tree(str(tmp_path), mount="/v3", schema_dir=str(SCHEMA_DIR))
