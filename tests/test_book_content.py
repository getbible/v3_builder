"""Preserve books whose only source content is a title or introduction."""

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from hasher import hash_all
from openapi import openapi_document
from test_getbiblesword_converter import _config, _convert, _verse_in, bv, entry

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def _validate(description, schema_name, document):
    validator = jsonschema.Draft202012Validator({
        "$ref": f"#/components/schemas/{schema_name}",
        "components": description["components"],
    })
    validator.validate(document)


@pytest.mark.parametrize("scope", ["book", "chapter"])
@pytest.mark.parametrize(
    ("raw", "stripped", "field", "expected"),
    [
        ("<p>Source preface.</p>", "Source preface.", "introduction",
         [{"text": "Source preface."}]),
        ('<title type="main">Source title</title>', "Source title", "titles",
         [{"type": "main", "text": "Source title"}]),
    ],
)
def test_content_without_verses_survives_conversion_hashing_and_schemas(
    tmp_path, scope, raw, stripped, field, expected
):
    chapter = 1 if scope == "chapter" else 0
    records = [entry(0, chapter, 0, scope, raw, stripped)]
    # SWORD also enumerates empty canon positions. They must not become public
    # books or empty chapters beside the actual title/introduction content.
    records.append(entry(1, 2, 0, "chapter", "", ""))
    records.append(entry(2, 2, 1, "verse", "", ""))
    empty_book = entry(3, 0, 0, "book", "", "")
    empty_book["scope"].update({
        "book_name": bv("Exodus"),
        "book_abbreviation": bv("Exod"),
        "osis_reference": bv("Exod"),
        "book": 2,
    })
    records.append(empty_book)

    document, output = _convert(
        tmp_path, _config(Genesis=1, Exodus=2), records
    )

    assert [(book["nr"], book["name"]) for book in document["books"]] == [
        (1, "Genesis")
    ]
    book = document["books"][0]
    if scope == "book":
        assert book["chapters"] == []
        assert book[field] == expected
    else:
        assert len(book["chapters"]) == 1
        assert book["chapters"][0]["chapter"] == 1
        assert book["chapters"][0]["verses"] == []
        assert book["chapters"][0][field] == expected
    assert not (output / "lxx" / "2.json").exists()
    assert not (output / "lxx" / "1" / "1.json").exists()
    assert not (output / "lxx" / "1" / "2.json").exists()

    _, books, chapters = hash_all(str(output))

    assert list(books["lxx"]) == ["1"]
    assert chapters["lxx"] == {"1": {}}
    description = openapi_document(
        ["lxx"], mount="/v3", schema_dir=str(SCHEMA_DIR)
    )
    documents = {
        "lxx.json": "translation",
        "lxx/1.json": "book",
        "translations.json": "translations-index",
        "lxx/books.json": "books-index",
        "lxx/1/chapters.json": "chapters-index",
        "checksum.json": "checksum-index",
        "lxx/checksum.json": "checksum-index",
        "lxx/1/checksum.json": "checksum-index",
    }
    for relative, schema_name in documents.items():
        path = output / relative
        _validate(description, schema_name, json.loads(path.read_text()))
        assert path.with_suffix(".sha").read_text() == (
            hashlib.sha1(path.read_bytes()).hexdigest() + "\n"
        )
    listed_books = json.loads((output / "lxx" / "books.json").read_text())
    assert list(listed_books) == ["1"]


def test_empty_canon_chapters_are_not_added_to_a_populated_book(tmp_path):
    document, output = _convert(
        tmp_path,
        _config(Genesis=1),
        [
            _verse_in("Genesis", 0, 1, 1, "Content."),
            entry(1, 2, 0, "chapter", "", ""),
            entry(2, 2, 1, "verse", "", ""),
        ],
    )
    assert [chapter["chapter"] for chapter in document["books"][0]["chapters"]] == [1]
    hash_all(str(output))
    chapters = json.loads((output / "lxx" / "1" / "chapters.json").read_text())
    assert list(chapters) == ["1"]


def test_schema_rejects_books_without_any_content():
    description = openapi_document(
        [], mount="/v3", schema_dir=str(SCHEMA_DIR)
    )
    validator = jsonschema.Draft202012Validator({
        "$ref": "#/components/schemas/book/$defs/entry",
        "components": description["components"],
    })
    empty = {"nr": 1, "name": "Genesis", "chapters": []}
    assert not validator.is_valid(empty)
    assert validator.is_valid({**empty, "introduction": [{"text": "Preface."}]})
    assert validator.is_valid({**empty, "titles": [{"text": "Title"}]})


def test_all_book_number_schemas_accept_extension_ids_and_share_the_bound():
    description = openapi_document(
        [], mount="/v3", schema_dir=str(SCHEMA_DIR)
    )
    components = description["components"]["schemas"]
    numbered = [
        components["book"]["properties"]["nr"],
        components["book"]["$defs"]["entry"]["properties"]["nr"],
        components["books-index"]["additionalProperties"]["properties"]["nr"],
        components["chapter"]["properties"]["book_nr"],
        components["chapters-index"]["additionalProperties"]["properties"]["book_nr"],
        description["components"]["parameters"]["book"]["schema"],
    ]
    for schema in numbered:
        validator = jsonschema.Draft202012Validator(schema)
        assert validator.is_valid(1)
        assert validator.is_valid(89)
        assert validator.is_valid(1000000)
        assert validator.is_valid(281474977710655)
        assert not validator.is_valid(281474977710656)
        assert not validator.is_valid(0)
