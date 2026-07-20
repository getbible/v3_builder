# SPDX-License-Identifier: GPL-2.0-only
"""Convert validated getBibleSWORD contracts into the getBible v3 API shape."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from converter import ConversionConfig
from file_ops import write_json_minified
from getbiblesword_contract import (
    CONTRACT_ID,
    byte_value_text,
    iter_contract,
    validate_contract,
)
from osis_parser import osis_plain_text, parse_osis_verse


class ConversionError(ValueError):
    """Raised when a valid native contract cannot map to Scripture API v3."""


def _text(value: Any, location: str) -> str:
    if value is None:
        return ""
    return byte_value_text(value, location=location)


def _entry_text(record: dict[str, Any], markup: str) -> str:
    """Decode SWORD's stripped view, with a lossless OSIS fallback.

    Some official SWORD filters can return a malformed UTF-8 stripped
    projection even when a UTF-8 module's authoritative raw OSIS is intact.
    The malformed bytes remain preserved in ``source.stripped``. For the
    backward-compatible display field, derive visible text from valid raw OSIS
    (or the valid rendered OSIS projection) instead of replacing bytes.
    """

    try:
        return _text(record.get("stripped"), "entry.stripped")
    except UnicodeDecodeError as stripped_error:
        if markup.lower() == "osis":
            for projection in ("raw", "rendered_default"):
                try:
                    projected_text = _text(
                        record.get(projection), f"entry.{projection}"
                    )
                except UnicodeDecodeError:
                    continue
                plain_text = osis_plain_text(projected_text)
                if plain_text is not None:
                    return plain_text
        raise ConversionError(
            "entry stripped projection is not valid UTF-8 and no safe "
            f"{markup or 'unknown'} fallback is available at ordinal "
            f"{record.get('ordinal')}"
        ) from stripped_error


def _osis_for_tokens(record: dict[str, Any], markup: str) -> str | None:
    """Return a valid OSIS projection for optional token enrichment.

    Raw contract bytes are authoritative and remain preserved verbatim in the
    source envelope, but legacy SWORD modules can contain isolated malformed or
    truncated UTF-8 sequences. Token extraction is additive, so an unusable raw
    projection must not make an otherwise valid verse or complete build fail.
    """

    if markup.lower() != "osis":
        return None
    for projection in ("raw", "rendered_default"):
        try:
            value = _text(record.get(projection), f"entry.{projection}")
        except UnicodeDecodeError:
            continue
        if value:
            return value
    return None


def _without_framing(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"sequence", "type"}
    }


def _entry_source(record: dict[str, Any]) -> dict[str, Any]:
    """Keep every exporter projection and its exact authoritative bytes."""

    return {
        "contract": CONTRACT_ID,
        "entry_ordinal": record["ordinal"],
        "key": record["key"],
        "scope": record["scope"],
        "raw": record["raw"],
        "rendered_default": record.get("rendered_default"),
        "stripped": record.get("stripped"),
        "projections_available": record.get("projections_available", False),
        "official_attributes": record.get("official_attributes", []),
        "annotation_segments": record.get("annotation_segments", []),
    }


class GetBibleSwordConverter:
    """Build backward-compatible Bible JSON from a lossless native contract.

    Existing fields remain unchanged. The additive ``source`` envelope retains
    the exact raw entry, SWORD projections, attributes, and lexical annotations.
    Introduction entries are attached at translation, book, or chapter level.
    """

    def __init__(
        self,
        config: ConversionConfig,
        output_path: str,
        *,
        conf_dir: str | None = None,
    ):
        self._config = config
        self._output_path = output_path
        self._conf_dir = conf_dir

    def convert(
        self,
        contract_path: str,
        *,
        module_name: str | None = None,
    ) -> str:
        summary = validate_contract(
            contract_path,
            expected_module=module_name,
            expected_classification="bible",
        )

        header: dict[str, Any] | None = None
        module: dict[str, Any] | None = None
        config_sources: list[dict[str, Any]] = []
        config_entries: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        unknown_records: list[dict[str, Any]] = []
        entries: list[dict[str, Any]] = []
        known = {
            "header",
            "module",
            "config_source",
            "config_entry",
            "entry",
            "artifact_begin",
            "artifact_chunk",
            "artifact_end",
            "diagnostic",
            "footer",
        }
        for record in iter_contract(contract_path):
            record_type = record["type"]
            if record_type == "header":
                header = record
            elif record_type == "module":
                module = record
            elif record_type == "config_source":
                config_sources.append(_without_framing(record))
            elif record_type == "config_entry":
                config_entries.append(_without_framing(record))
            elif record_type == "entry":
                entries.append(record)
            elif record_type == "diagnostic":
                diagnostics.append(_without_framing(record))
            elif record_type not in known:
                unknown_records.append(record)
        if header is None or module is None:
            raise ConversionError("validated contract is missing required records")

        module_name = summary.module_name
        abbreviation = self._config.translation_names.get(
            module_name, module_name.lower()
        )
        config_map = self._configuration_map(config_entries)
        language_code = config_map.get(
            "lang", _text(module.get("language"), "module.language")
        )
        description = config_map.get(
            "description", _text(module.get("description"), "module.description")
        )
        translation = self._config.v1_translations.get(
            abbreviation, description or module_name
        )
        direction = module.get("direction", {}).get("name", "ltr").upper()
        encoding = config_map.get(
            "encoding", module.get("encoding", {}).get("name", "")
        )
        markup = module.get("markup", {}).get("name", "")

        shared_meta = {
            "translation": translation,
            "abbreviation": abbreviation,
            "lang": self._config.lang_correction.get(
                language_code, language_code
            ),
            "language": self._config.language_names.get(language_code, ""),
            "direction": self._config.text_direction.get(
                language_code, direction
            ),
            "encoding": encoding,
            "source_contract": {
                "contract": CONTRACT_ID,
                "producer": header.get("producer"),
                "producer_version": summary.producer_version,
                "sword_version": summary.sword_version,
                "stream_sha256": summary.stream_sha256,
                "entries": summary.entries,
                "artifacts": summary.artifacts,
                "artifact_bytes": summary.artifact_bytes,
            },
        }
        bible: dict[str, Any] = {
            **shared_meta,
            "description": description,
            "books": [],
            "source": {
                "module": _without_framing(module),
                "config_sources": config_sources,
                "config_entries": config_entries,
                "diagnostics": diagnostics,
                "unknown_records": unknown_records,
            },
        }
        books: OrderedDict[int, dict[str, Any]] = OrderedDict()

        for record in entries:
            scope = record.get("scope")
            if not isinstance(scope, dict) or scope.get("type") != "verse_key":
                bible.setdefault("unscoped_entries", []).append(
                    _entry_source(record)
                )
                continue
            intro_scope = scope.get("intro_scope")
            if intro_scope != "verse":
                self._attach_introduction(bible, books, record, markup)
                continue
            chapter_number = scope.get("chapter")
            verse_number = scope.get("verse")
            if not isinstance(chapter_number, int) or chapter_number <= 0:
                raise ConversionError(
                    f"invalid chapter scope in entry {record.get('ordinal')}"
                )
            if not isinstance(verse_number, int) or verse_number <= 0:
                raise ConversionError(
                    f"invalid verse scope in entry {record.get('ordinal')}"
                )
            book = self._book_for_scope(books, scope, abbreviation)
            chapter = book["_chapters"].setdefault(
                chapter_number,
                {
                    "chapter": chapter_number,
                    "name": f"{book['name']} {chapter_number}",
                    "verses": [],
                },
            )
            verse = self._verse(
                record,
                book["name"],
                chapter_number,
                verse_number,
                markup,
            )
            if verse is not None:
                chapter["verses"].append(verse)

        output_root = Path(self._output_path)
        output_root.mkdir(parents=True, exist_ok=True)
        for book_number, book in books.items():
            chapters = list(book.pop("_chapters").values())
            if not any(chapter["verses"] for chapter in chapters):
                continue
            book["chapters"] = chapters
            bible["books"].append(book)
            book_directory = output_root / abbreviation / str(book_number)
            book_directory.mkdir(parents=True, exist_ok=True)
            for chapter in chapters:
                if not chapter["verses"]:
                    continue
                chapter_data = {
                    **shared_meta,
                    "book_nr": book_number,
                    "book_name": book["name"],
                    **chapter,
                }
                write_json_minified(
                    chapter_data,
                    str(book_directory / f"{chapter['chapter']}.json"),
                )
            book_data = {
                **shared_meta,
                "nr": book_number,
                "name": book["name"],
                "chapters": chapters,
            }
            if "introduction" in book:
                book_data["introduction"] = book["introduction"]
            write_json_minified(
                book_data,
                str(output_root / abbreviation / f"{book_number}.json"),
            )

        bible.update(self._distribution_metadata(config_map, abbreviation))
        version_path = output_root / f"{abbreviation}.json"
        write_json_minified(bible, str(version_path))
        return str(version_path)

    @staticmethod
    def _configuration_map(
        entries: list[dict[str, Any]],
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for entry in entries:
            try:
                name = byte_value_text(
                    entry["name"], location="config_entry.name"
                ).lower()
                value = byte_value_text(
                    entry["value"], location="config_entry.value"
                )
            except UnicodeDecodeError:
                continue
            values[name] = value
        return values

    def _book_for_scope(
        self,
        books: OrderedDict[int, dict[str, Any]],
        scope: dict[str, Any],
        abbreviation: str,
    ) -> dict[str, Any]:
        sword_name = _text(scope.get("book_name"), "entry.scope.book_name")
        book_number = self._config.book_numbers.get(sword_name)
        if book_number is None:
            testament = scope.get("testament")
            testament_book = scope.get("book")
            if testament == 1 and isinstance(testament_book, int):
                book_number = testament_book
            elif testament == 2 and isinstance(testament_book, int):
                book_number = 39 + testament_book
            else:
                raise ConversionError(
                    f"unknown SWORD book name {sword_name!r}"
                )
        if book_number not in books:
            default_name = self._config.book_names.get(sword_name, sword_name)
            display_name = self._resolve_book_name(
                book_number,
                default_name,
                abbreviation,
                self._conf_dir,
                self._config,
            )
            books[book_number] = {
                "nr": book_number,
                "name": display_name,
                "_chapters": OrderedDict(),
            }
        return books[book_number]

    @staticmethod
    def _resolve_book_name(
        book_nr,
        default_name,
        abbreviation,
        conf_dir,
        config,
    ):
        if conf_dir:
            local_path = os.path.join(
                conf_dir, f"books_{abbreviation}.json"
            )
            if os.path.isfile(local_path):
                import json

                with open(local_path, "r", encoding="utf-8") as stream:
                    return json.load(stream).get(str(book_nr), default_name)
        return default_name

    def _attach_introduction(
        self,
        bible: dict[str, Any],
        books: OrderedDict[int, dict[str, Any]],
        record: dict[str, Any],
        markup: str,
    ) -> None:
        scope = record["scope"]
        introduction = {
            "scope": scope,
            "text": _entry_text(record, markup),
            "source": _entry_source(record),
        }
        intro_scope = scope.get("intro_scope")
        if intro_scope in {"module", "testament"}:
            bible.setdefault("introductions", []).append(introduction)
            return
        book = self._book_for_scope(
            books, scope, bible["abbreviation"]
        )
        if intro_scope == "book":
            book.setdefault("introduction", []).append(introduction)
            return
        if intro_scope == "chapter":
            chapter_number = scope.get("chapter")
            chapter = book["_chapters"].setdefault(
                chapter_number,
                {
                    "chapter": chapter_number,
                    "name": f"{book['name']} {chapter_number}",
                    "verses": [],
                },
            )
            chapter.setdefault("introduction", []).append(introduction)
            return
        bible.setdefault("unscoped_entries", []).append(
            _entry_source(record)
        )

    @staticmethod
    def _verse(
        record: dict[str, Any],
        book_name: str,
        chapter: int,
        verse_number: int,
        markup: str,
    ) -> dict[str, Any] | None:
        text = _entry_text(record, markup)
        if not text.replace("[]", "").strip():
            return None
        verse: dict[str, Any] = {
            "chapter": chapter,
            "verse": verse_number,
            "name": f"{book_name} {chapter}:{verse_number}",
            "text": text,
            "source": _entry_source(record),
        }
        osis = _osis_for_tokens(record, markup)
        if osis is not None:
            word_data = parse_osis_verse(osis, text)
            if word_data:
                verse["tokens"] = word_data["tokens"]
                verse["spans"] = word_data["spans"]
        return verse

    @staticmethod
    def _distribution_metadata(
        config: dict[str, str],
        abbreviation: str,
    ) -> dict[str, Any]:
        return {
            "distribution_lcsh": config.get("lcsh", ""),
            "distribution_version": config.get("version", ""),
            "distribution_version_date": config.get("swordversiondate", ""),
            "distribution_abbreviation": config.get(
                "abbreviation", abbreviation
            ),
            "distribution_about": config.get("about", ""),
            "distribution_license": config.get("distributionlicense", ""),
            "distribution_sourcetype": config.get("sourcetype", ""),
            "distribution_source": config.get("textsource", ""),
            "distribution_versification": config.get("versification", ""),
            "distribution_history": {
                key: value
                for key, value in config.items()
                if "history" in key
            },
        }
