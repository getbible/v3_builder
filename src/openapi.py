# SPDX-License-Identifier: GPL-2.0-only
"""Describe the generated static tree as an OpenAPI document.

The builder writes static files and knows nothing about where they are served.
``openapi.json`` at the root of the generated tree tells whoever serves the
tree, and whoever consumes it, what every path holds: the documents, their
parameters, the JSON Schema of each, and the rules for reading them.  Paths
start at the version segment the tree is built for and no server is named, so
the document is true wherever the tree is mounted at that segment; a
deployment under another prefix adds a ``servers`` entry of its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from file_ops import write_json_minified, write_text_atomic

OPENAPI_VERSION = "3.1.0"
DOCUMENT_NAME = "openapi.json"
CHECKSUM_NAME = "openapi.sha"

# The schemas the tree is described with, in the order the description
# introduces them.  Every one is embedded under ``components`` so that the
# document stands alone.
SCHEMA_NAMES = (
    "translation",
    "book",
    "chapter",
    "translations-index",
    "books-index",
    "chapters-index",
    "checksum-index",
    "checksum",
    "verse",
    "token",
    "span",
    "editorial",
    "title",
    "introduction",
)

_TRANSLATION_PATTERN = "^[a-z0-9][a-z0-9_-]{0,29}$"
# The tree is mounted at exactly one version segment, as the downstream
# static endpoint serves it: /v3, never /prefix/v3.
_VERSION_MOUNT = re.compile(r"^/v[0-9]+$")

_DESCRIPTION = """\
Static JSON documents of Bible translations, converted from CrossWire SWORD
modules by the GetBible builder. Every path below is a file in a static tree:
there is no query string, no content negotiation and no request-time logic.

**Where the paths start.** Paths are given from the server root with `{label}`
as the first segment, the version the tree is built for. No host is assumed. A
tree mounted under another prefix is described by adding a `servers` entry with
that prefix.

**Finding a translation.** `translations.json` lists every translation in the
tree with its language, direction, distribution metadata, the URL its document
was built for and the SHA-1 of that document. The abbreviation is the first
path segment of all of that translation's documents. `{{translation}}/books.json`
lists the books the translation has and
`{{translation}}/{{book}}/chapters.json` the chapters of one book, each entry
carrying the same `url` and `sha` members, so nothing need be guessed.

**Addressing.** `book` is the GetBible book number: Genesis is 1, Matthew 40,
Revelation 66, and the deuterocanonical books continue to 83. `chapter` and
`verse` are the numbers of the translation's own versification. Only books,
chapters and verses that have text are published as documents. A chapter for
which the source supplies an introduction but no verse text stays nested in
its book and translation documents with an empty `verses` array and its
`titles`, and has no document of its own.

**Three self-similar levels.** A chapter document holds the verses of one
chapter with the translation's metadata; a book document holds every chapter of
one book; the translation document holds every book. A chapter nested in a book
or translation document carries the same members as the standalone chapter
document after the shared metadata, so one reader serves all three levels.

**Integrity.** Every JSON document, the index and checksum documents and this
description included, has a `.sha` sibling holding the SHA-1 of its bytes as
forty hexadecimal digits and a line feed, so a reader can detect a change to
any document by fetching its small sibling. Each index document also repeats
the digest of every document it lists as `sha` beside that document's `url`,
and `checksum.json` at each level maps every translation, book or chapter
document of that level to its digest, so a change anywhere is visible with
one request per level. The extension-less
tab-separated listings written beside the JSON indexes (`translations`,
`books`, `chapters` and `checksum`) are text companions of the same data and
are not described here.

**Reading a verse.** `text` is the display text; it never begins with a line
ending, while line endings inside the verse are preserved. `paragraph: true`
marks a verse that begins a paragraph. `tokens` and `spans` are present
together when the source carries OSIS word markup: tokens are the words in
reading order with their lexical attributes (`lemma`, `morph` and `xlit`
grouped by scheme, `src` as positions), spans are annotations such as
`divineName`, `transChange` and `q` over a token range (`token_start` and
`token_end`, 0-based indexes into `tokens`) and over the whitespace-separated
words of `text` (`word_start` and `word_end`, 1-based and inclusive; 0 when
the text could not be located). A reader that only highlights uses the word
range; a reader that needs the original-language data uses the token range.

**Editorial.** A chapter's optional `editorial` array is its reading layout in
order: headings anchored before a verse, with the source's title type and
canonical flag, and paragraphs as inclusive verse ranges. When paragraph
entries are present they cover every verse of the chapter contiguously. The
same array appears in the standalone chapter document and in the chapter
nested in the book and translation documents. A chapter with verses carries no
`titles`, and verses never do; headings have that one representation.

**Titles and introductions.** A translation or a book may carry `titles`, the
title metadata belonging to it, and `introduction`, prose attached at that
level; a chapter may carry `introduction`. Both are absent when the source
supplies nothing.
"""

_TAGS = [
    {"name": "tree", "description": "Documents that describe the whole tree."},
    {"name": "translation", "description": "The documents of one translation."},
    {"name": "book", "description": "The documents of one book of a translation."},
    {"name": "chapter", "description": "The documents of one chapter of a book."},
]

_NOT_FOUND = {
    "description": (
        "No such document. The tree is static: a document the index documents "
        "do not list does not exist."
    )
}


def normalize_base_url(base_url: str) -> str:
    """The public base URL the tree is built for, checked and normalized.

    The URL must name a scheme and host and its path must be exactly one
    version segment, such as ``https://example.test/v3``.  A trailing slash
    is dropped so the index ``url`` fields and the description agree.
    """

    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/")
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.query
        or parts.fragment
        or not _VERSION_MOUNT.match(path)
    ):
        raise ValueError(
            "the public base URL must name a host and end in exactly one "
            f"version segment, such as https://example.test/v3; got {base_url!r}"
        )
    return f"{parts.scheme}://{parts.netloc}{path}"


def mount_from_base_url(base_url: str) -> str:
    """The path at which the tree is mounted, taken from its public base URL.

    ``https://example.test/v3`` and ``https://example.test/v3/`` both yield
    ``/v3``.
    """

    return urlsplit(normalize_base_url(base_url)).path


def version_label(mount: str) -> str:
    """The version segment a mount consists of, such as ``v3``."""

    if not _VERSION_MOUNT.match(mount):
        raise ValueError(
            "the generated tree must be described under exactly one version "
            f"segment such as /v3; the mount is {mount!r}"
        )
    return mount[1:]


def openapi_document(
    abbreviations: list[str],
    *,
    mount: str,
    schema_dir: str,
) -> dict[str, Any]:
    """The OpenAPI description of one tree, from its build and the checked-in schemas."""

    label = version_label(mount)
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": f"GetBible Scripture {label}",
            "version": label[1:],
            "summary": (
                "Bible translations as static JSON: every path is a file, every "
                "document is plain text."
            ),
            "description": _DESCRIPTION.format(label=label),
        },
        "tags": list(_TAGS),
        "paths": {
            **_tree_paths(mount),
            **_translation_paths(mount),
            **_book_paths(mount),
            **_chapter_paths(mount),
        },
        "components": {
            "parameters": _parameters(abbreviations),
            "schemas": _component_schemas(schema_dir, SCHEMA_NAMES),
        },
    }


def describe_tree(scripture_root: str, *, mount: str, schema_dir: str) -> str:
    """Write ``openapi.json`` and its ``.sha`` at the root of a hashed tree.

    The translations the description lists are read from the ``translations``
    index the hasher wrote, so the description names exactly the translations
    of this build.  Returns the path of the written document.
    """

    index_path = os.path.join(scripture_root, "translations.json")
    with open(index_path, "r", encoding="utf-8") as stream:
        index = json.load(stream)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path} is not a translations index")
    document = openapi_document(
        sorted(index), mount=mount, schema_dir=schema_dir,
    )
    document_path = os.path.join(scripture_root, DOCUMENT_NAME)
    write_json_minified(document, document_path)
    digest = hashlib.sha1()
    with open(document_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(8192), b""):
            digest.update(chunk)
    write_text_atomic(
        digest.hexdigest() + "\n", os.path.join(scripture_root, CHECKSUM_NAME)
    )
    return document_path


def _operation(
    operation_id: str,
    tag: str,
    summary: str,
    description: str,
    schema: dict[str, Any],
    parameters: tuple[str, ...] = (),
    media_type: str = "application/json",
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "description": description,
        "operationId": operation_id,
    }
    if parameters:
        operation["parameters"] = [
            {"$ref": f"#/components/parameters/{name}"} for name in parameters
        ]
    operation["responses"] = {
        "200": {
            "description": summary,
            "content": {media_type: {"schema": schema}},
        },
        "404": _NOT_FOUND,
    }
    return {"get": operation}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _checksum_operation(operation_id: str, tag: str, subject: str, parameters=()):
    return _operation(
        operation_id,
        tag,
        f"The SHA-1 of {subject}",
        f"Forty hexadecimal digits and a line feed: the SHA-1 of the bytes of {subject}.",
        _ref("checksum"),
        parameters,
        media_type="text/plain",
    )


def _tree_paths(mount: str) -> dict[str, Any]:
    return {
        f"{mount}/translations.json": _operation(
            "getTranslations",
            "tree",
            "Every translation in the tree",
            "The translation index: every translation with its metadata, the URL "
            "its document was built for and the SHA-1 of that document.",
            _ref("translations-index"),
        ),
        f"{mount}/translations.sha": _checksum_operation(
            "getTranslationsChecksum", "tree", "the translation index",
        ),
        f"{mount}/checksum.json": _operation(
            "getTranslationChecksums",
            "tree",
            "The SHA-1 of every translation document",
            "Every translation's abbreviation mapped to the SHA-1 of its document.",
            _ref("checksum-index"),
        ),
        f"{mount}/checksum.sha": _checksum_operation(
            "getTranslationChecksumsChecksum", "tree", "the translation checksum index",
        ),
        f"{mount}/openapi.json": _operation(
            "getOpenApi",
            "tree",
            "This description",
            "The OpenAPI description of the tree, generated with it.",
            {"type": "object"},
        ),
        f"{mount}/openapi.sha": _checksum_operation(
            "getOpenApiChecksum", "tree", "this description",
        ),
    }


def _translation_paths(mount: str) -> dict[str, Any]:
    return {
        f"{mount}/{{translation}}.json": _operation(
            "getTranslation",
            "translation",
            "The whole translation",
            "Every book, chapter and verse of one translation with its metadata "
            "and the source module's distribution metadata. A bulk document: "
            "translations.json carries its digest.",
            _ref("translation"),
            ("translation",),
        ),
        f"{mount}/{{translation}}.sha": _checksum_operation(
            "getTranslationChecksum", "translation",
            "the translation document", ("translation",),
        ),
        f"{mount}/{{translation}}/books.json": _operation(
            "getBooks",
            "translation",
            "Every book of one translation",
            "The book index of a translation: every book with its number, name "
            "and title metadata, the URL its document was built for and the "
            "SHA-1 of that document.",
            _ref("books-index"),
            ("translation",),
        ),
        f"{mount}/{{translation}}/books.sha": _checksum_operation(
            "getBooksChecksum", "translation", "the book index", ("translation",),
        ),
        f"{mount}/{{translation}}/checksum.json": _operation(
            "getBookChecksums",
            "translation",
            "The SHA-1 of every book document of one translation",
            "Every book number mapped to the SHA-1 of its document.",
            _ref("checksum-index"),
            ("translation",),
        ),
        f"{mount}/{{translation}}/checksum.sha": _checksum_operation(
            "getBookChecksumsChecksum", "translation", "the book checksum index",
            ("translation",),
        ),
    }


def _book_paths(mount: str) -> dict[str, Any]:
    return {
        f"{mount}/{{translation}}/{{book}}.json": _operation(
            "getBook",
            "book",
            "One book",
            "Every chapter and verse of one book with the translation's metadata "
            "and the book's title metadata and introduction.",
            _ref("book"),
            ("translation", "book"),
        ),
        f"{mount}/{{translation}}/{{book}}.sha": _checksum_operation(
            "getBookChecksum", "book", "the book document", ("translation", "book"),
        ),
        f"{mount}/{{translation}}/{{book}}/chapters.json": _operation(
            "getChapters",
            "book",
            "Every chapter of one book",
            "The chapter index of a book: every chapter with its number, name "
            "and reading layout, the URL its document was built for and the "
            "SHA-1 of that document.",
            _ref("chapters-index"),
            ("translation", "book"),
        ),
        f"{mount}/{{translation}}/{{book}}/chapters.sha": _checksum_operation(
            "getChaptersChecksum", "book", "the chapter index", ("translation", "book"),
        ),
        f"{mount}/{{translation}}/{{book}}/checksum.json": _operation(
            "getChapterChecksums",
            "book",
            "The SHA-1 of every chapter document of one book",
            "Every chapter number mapped to the SHA-1 of its document.",
            _ref("checksum-index"),
            ("translation", "book"),
        ),
        f"{mount}/{{translation}}/{{book}}/checksum.sha": _checksum_operation(
            "getChapterChecksumsChecksum", "book", "the chapter checksum index",
            ("translation", "book"),
        ),
    }


def _chapter_paths(mount: str) -> dict[str, Any]:
    return {
        f"{mount}/{{translation}}/{{book}}/{{chapter}}.json": _operation(
            "getChapter",
            "chapter",
            "One chapter",
            "Every verse of one chapter with the translation's metadata, the "
            "book it belongs to, and its reading layout and introduction when "
            "the source supplies them.",
            _ref("chapter"),
            ("translation", "book", "chapter"),
        ),
        f"{mount}/{{translation}}/{{book}}/{{chapter}}.sha": _checksum_operation(
            "getChapterChecksum", "chapter", "the chapter document",
            ("translation", "book", "chapter"),
        ),
    }


def _parameters(abbreviations: list[str]) -> dict[str, Any]:
    translation: dict[str, Any] = {"type": "string", "pattern": _TRANSLATION_PATTERN}
    if abbreviations:
        translation["enum"] = sorted(abbreviations)
    return {
        "translation": {
            "name": "translation",
            "in": "path",
            "required": True,
            "description": "The translation's abbreviation, as translations.json lists it.",
            "schema": translation,
        },
        "book": {
            "name": "book",
            "in": "path",
            "required": True,
            "description": (
                "GetBible book number: Genesis is 1, Matthew 40, Revelation 66, "
                "and the deuterocanonical books continue to 83. books.json lists "
                "the numbers the translation has."
            ),
            "schema": {"type": "integer", "minimum": 1, "maximum": 83},
        },
        "chapter": {
            "name": "chapter",
            "in": "path",
            "required": True,
            "description": "Chapter number; chapters.json lists the chapters the book has.",
            "schema": {"type": "integer", "minimum": 1},
        },
    }


def _component_schemas(schema_dir: str, names: tuple[str, ...]) -> dict[str, Any]:
    """The checked-in schemas, embedded so every reference stays inside this document."""

    components: dict[str, Any] = {}
    for name in names:
        path = os.path.join(schema_dir, f"{name}.schema.json")
        with open(path, "r", encoding="utf-8") as stream:
            schema = json.load(stream)
        schema.pop("$schema", None)
        schema.pop("$id", None)
        components[name] = _internalise(schema, name, names)
    return components


def _internalise(node: Any, own: str, names: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                result[key] = _component_reference(value, own, names)
            else:
                result[key] = _internalise(value, own, names)
        return result
    if isinstance(node, list):
        return [_internalise(value, own, names) for value in node]
    return node


def _component_reference(reference: str, own: str, names: tuple[str, ...]) -> str:
    """Rewrite a schema-file reference into a pointer inside the description."""

    target, _, fragment = reference.partition("#")
    if target:
        target = target.rsplit("/", 1)[-1]
        if target.endswith(".schema.json"):
            target = target[: -len(".schema.json")]
        elif target.endswith(".json"):
            target = target[: -len(".json")]
        if target not in names:
            raise ValueError(
                f"schema {own} refers to {reference}, which the tree does not publish"
            )
    else:
        target = own
    if fragment and not fragment.startswith("/"):
        raise ValueError(f"schema {own} uses an unsupported reference {reference}")
    return f"#/components/schemas/{target}{fragment}"


__all__ = [
    "CHECKSUM_NAME",
    "DOCUMENT_NAME",
    "OPENAPI_VERSION",
    "SCHEMA_NAMES",
    "describe_tree",
    "mount_from_base_url",
    "normalize_base_url",
    "openapi_document",
    "version_label",
]
