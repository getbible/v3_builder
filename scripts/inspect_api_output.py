#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect a lean KJV build without flooding a GitHub Actions log.

The builder deliberately emits minified JSON.  This script validates the
requested inspection surface, reports every chapter's structural shape, and
prints one complete representative verse per chapter.  It checks that the
generated ``openapi.json`` describes the tree it sits in, and enforces a
pre-publication file-size ceiling across every supplied output root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


MIB = 1024 * 1024
DEFAULT_SIZE_LIMIT_MIB = 95.0
DEFAULT_LARGEST_FILE_COUNT = 20


class InspectionError(RuntimeError):
    """Raised when generated API output is incomplete or unsafe to publish."""


@dataclass(frozen=True)
class BookTarget:
    number: int
    name: str
    aliases: tuple[str, ...]


TARGET_BOOKS = (
    BookTarget(19, "Psalms", ("psalm", "psalms")),
    BookTarget(43, "John", ("john", "gospelofjohn")),
    BookTarget(
        66,
        "Revelation",
        ("revelation", "revelationofjohn", "therevelationofjohn"),
    ),
)
TARGET_CHAPTERS = tuple(range(1, 6))
FORBIDDEN_SOURCE_FIELDS = frozenset({"source", "source_contract", "normalized_raw"})
EDITORIAL_HEADING_FIELDS = frozenset(
    {"order", "type", "anchor", "text", "heading_type", "canonical"}
)
EDITORIAL_HEADING_OPTIONAL_FIELDS = frozenset({"tokens", "spans", "content", "attrs", "subtype"})
EDITORIAL_PARAGRAPH_FIELDS = frozenset({"order", "type", "start", "end"})
EDITORIAL_ANCHOR_FIELDS = frozenset({"verse", "edge"})
OPENAPI_VERSION = "3.1.0"
OPENAPI_DOCUMENT = "openapi.json"
OPENAPI_CHECKSUM = "openapi.sha"
# Every document type the description must embed a schema for.
OPENAPI_SCHEMAS = frozenset(
    {
        "translation", "book", "chapter", "translations-index", "books-index",
        "chapters-index", "checksum-index", "checksum", "verse", "token", "span",
        "editorial", "title", "introduction", "reference", "anchor", "content",
    }
)
_VERSION_MOUNT = re.compile(r"^/v[0-9]+$")


def _json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError as exc:
        raise InspectionError(f"required API file is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectionError(
            f"cannot read valid UTF-8 JSON from {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise InspectionError(f"expected a JSON object in {path}")
    return value


def _normalized_name(value: Any) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _human_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(size)
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024.0
    return f"{size} B"


def _root_label(root: Path, path: Path) -> str:
    return f"{root.name}/{path.relative_to(root).as_posix()}"


def scan_output_sizes(
    output_roots: Sequence[Path],
    *,
    size_limit_bytes: int,
    largest_file_count: int = DEFAULT_LARGEST_FILE_COUNT,
) -> dict[str, Any]:
    """Return a bounded size report and fail when any output reaches the limit."""

    if size_limit_bytes <= 0:
        raise InspectionError("the output size limit must be greater than zero")
    if largest_file_count <= 0:
        raise InspectionError("largest_file_count must be greater than zero")

    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in output_roots:
        root = candidate.resolve()
        if root in seen:
            continue
        if not root.is_dir():
            raise InspectionError(f"generated API output directory is missing: {root}")
        roots.append(root)
        seen.add(root)

    files: list[tuple[int, str]] = []
    root_summaries: list[dict[str, Any]] = []
    violations: list[tuple[int, str]] = []
    for root in roots:
        root_files: list[tuple[int, str]] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise InspectionError(
                    f"generated API output contains a symlink: {path}"
                )
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise InspectionError(
                    f"cannot stat generated API file {path}: {exc}"
                ) from exc
            labelled = _root_label(root, path)
            item = (size, labelled)
            root_files.append(item)
            files.append(item)
            if size >= size_limit_bytes:
                violations.append(item)
        if not root_files:
            raise InspectionError(
                f"generated API output directory contains no files: {root}"
            )
        largest = max(root_files)
        root_summaries.append(
            {
                "root": root.name,
                "file_count": len(root_files),
                "total_bytes": sum(size for size, _ in root_files),
                "total_human": _human_bytes(sum(size for size, _ in root_files)),
                "largest_file": largest[1],
                "largest_bytes": largest[0],
                "largest_human": _human_bytes(largest[0]),
            }
        )

    files.sort(reverse=True)
    report = {
        "limit_bytes": size_limit_bytes,
        "limit_human": _human_bytes(size_limit_bytes),
        "file_count": len(files),
        "total_bytes": sum(size for size, _ in files),
        "total_human": _human_bytes(sum(size for size, _ in files)),
        "roots": root_summaries,
        "largest_files": [
            {"path": path, "bytes": size, "human": _human_bytes(size)}
            for size, path in files[:largest_file_count]
        ],
    }
    if violations:
        detail = "; ".join(
            f"{path}={_human_bytes(size)}"
            for size, path in sorted(violations, reverse=True)
        )
        raise InspectionError(
            f"generated API file size gate failed (limit {_human_bytes(size_limit_bytes)}): "
            f"{detail}"
        )
    return report


def _field_profiles(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = Counter(tuple(sorted(record)) for record in records)
    return [
        {"fields": list(fields), "record_count": count}
        for fields, count in sorted(profiles.items(), key=lambda item: item[0])
    ]


def _require_no_source_envelopes(value: Any, location: str) -> None:
    """Fail if a generated API document retained a transient source envelope."""

    pending: list[tuple[Any, str]] = [(value, location)]
    while pending:
        current, current_path = pending.pop()
        if isinstance(current, dict):
            forbidden = sorted(FORBIDDEN_SOURCE_FIELDS.intersection(current))
            if forbidden:
                raise InspectionError(
                    f"{current_path} retains forbidden transient field(s): "
                    + ", ".join(forbidden)
                )
            pending.extend(
                (child, f"{current_path}.{key}") for key, child in current.items()
                if not (key == "attrs" and isinstance(child, dict)
                        and all(isinstance(value, str) for value in child.values()))
            )
        elif isinstance(current, list):
            pending.extend(
                (child, f"{current_path}[{index}]")
                for index, child in enumerate(current)
            )


def _is_semantic_key(key: str, kind: str) -> bool:
    normalized = _normalized_name(key)
    if kind == "paragraph":
        return "paragraph" in normalized or normalized in {"para", "pilcrow"}
    return any(part in normalized for part in ("heading", "title", "sectionhead"))


def _semantic_fields(
    value: Any,
    *,
    kind: str,
    path: str = "",
) -> list[dict[str, Any]]:
    """Find explicit paragraph/title fields, including nested semantic objects."""

    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if _is_semantic_key(key, kind):
                found.append({"field": child_path, "value": child})
            elif key not in {"tokens", "spans", "source", "source_contract"}:
                found.extend(_semantic_fields(child, kind=kind, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_semantic_fields(child, kind=kind, path=f"{path}[{index}]"))
    return found


def _span_kind(span: dict[str, Any]) -> str | None:
    tag = _normalized_name(span.get("tag", ""))
    attrs = span.get("attrs") if isinstance(span.get("attrs"), dict) else {}
    attr_type = _normalized_name(attrs.get("type", ""))
    combined = f"{tag}{attr_type}"
    if "paragraph" in combined or tag in {"p", "para", "pilcrow"}:
        return "paragraph"
    if any(part in combined for part in ("heading", "title", "sectionhead")):
        return "heading"
    return None


def _semantic_summary(
    chapter: dict[str, Any], verses: Sequence[dict[str, Any]], kind: str
) -> list[dict[str, Any]]:
    chapter_meta = {key: value for key, value in chapter.items() if key != "verses"}
    result = [
        {"level": "chapter", **item}
        for item in _semantic_fields(chapter_meta, kind=kind)
    ]
    for verse in verses:
        verse_number = verse.get("verse")
        verse_meta = {
            key: value
            for key, value in verse.items()
            if key not in {"text", "tokens", "spans", "source", "source_contract"}
        }
        result.extend(
            {"level": "verse", "verse": verse_number, **item}
            for item in _semantic_fields(verse_meta, kind=kind)
        )
        for span in verse.get("spans", []):
            if isinstance(span, dict) and _span_kind(span) == kind:
                result.append(
                    {
                        "level": "span",
                        "verse": verse_number,
                        "tag": span.get("tag"),
                        "token_start": span.get("token_start"),
                        "token_end": span.get("token_end"),
                        "attrs": span.get("attrs", {}),
                    }
                )
    return result


def _string_attributes(value: Any, location: str) -> None:
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
        raise InspectionError(f"{location} must contain string-valued attributes")


def _validate_content(value: Any, location: str, notes: set[str], references: set[str]) -> None:
    """Validate recursive source trees and their chapter-local links."""
    if not isinstance(value, list):
        raise InspectionError(f"{location} content must be an array")
    pending = [(item, f"{location}[{index}]") for index, item in enumerate(value)]
    while pending:
        node, node_location = pending.pop()
        if isinstance(node, str):
            continue
        if not isinstance(node, dict) or not {"tag", "children"}.issubset(node) or set(node) - {"tag", "children", "attrs"}:
            raise InspectionError(f"{node_location} must be text or a content element")
        if not isinstance(node["tag"], str) or not node["tag"]:
            raise InspectionError(f"{node_location} tag must be a non-empty string")
        if not isinstance(node["children"], list):
            raise InspectionError(f"{node_location} children must be an array")
        attrs = node.get("attrs", {})
        _string_attributes(attrs, node_location)
        for key, available in (("reference_id", references), ("footnote_id", notes)):
            if key in attrs and attrs[key] not in available:
                raise InspectionError(f"{node_location} has a dangling {key}")
        if "footnote_id" in attrs and node["children"]:
            raise InspectionError(f"{node_location} duplicates a footnote body")
        pending.extend((child, f"{node_location}.children[{index}]") for index, child in enumerate(node["children"]))


def _study_ids(items: Sequence[Any], location: str) -> set[str]:
    ids: set[str] = set()
    for item in items:
        identifier = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise InspectionError(f"{location} IDs must be non-empty and unique")
        ids.add(identifier)
    return ids


def _validate_study_anchor(anchor: Any, chapter: dict[str, Any], notes: dict[str, Any], location: str) -> None:
    if not isinstance(anchor, dict) or "verse" not in anchor or set(anchor) - {"verse", "offset", "alignment", "note", "note_offset", "scope", "introduction"}:
        raise InspectionError(f"{location} has invalid study anchor fields")
    verses = {verse["verse"]: verse["text"] for verse in chapter["verses"]}
    verse = anchor["verse"]
    scope = anchor.get("scope")
    if scope == "introduction":
        index = anchor.get("introduction")
        introductions = chapter.get("introduction", [])
        if (
            type(verse) is not int or verse != 0 or type(index) is not int
            or not isinstance(introductions, list) or not 0 <= index < len(introductions)
            or not isinstance(introductions[index], dict)
            or not isinstance(introductions[index].get("text"), str)
        ):
            raise InspectionError(f"{location} must locate a chapter introduction")
        anchor_text = introductions[index]["text"]
    elif scope == "source":
        if type(verse) is not int or verse < 1 or verse in verses or "introduction" in anchor or "offset" in anchor or anchor.get("alignment") != "unresolved":
            raise InspectionError(f"{location} source anchor must identify an unpublished verse with unresolved alignment")
        anchor_text = ""
    elif scope is None:
        if type(verse) is not int or verse not in verses or "introduction" in anchor:
            raise InspectionError(f"{location} anchor verse must reference an emitted verse")
        anchor_text = verses[verse]
    else:
        raise InspectionError(f"{location} study anchor scope is invalid")
    if "offset" in anchor:
        offset = anchor["offset"]
        if "alignment" in anchor or type(offset) is not int or not 0 <= offset <= len(anchor_text):
            raise InspectionError(f"{location} offset must be a valid Unicode code-point position")
    elif anchor.get("alignment") != "unresolved":
        raise InspectionError(f"{location} must supply offset or unresolved alignment")
    if ("note" in anchor) != ("note_offset" in anchor):
        raise InspectionError(f"{location} note and note_offset must occur together")
    if "note" in anchor:
        note = notes.get(anchor["note"]) if isinstance(anchor["note"], str) else None
        offset = anchor["note_offset"]
        if (
            note is None or not isinstance(note.get("text"), str)
            or not isinstance(note.get("anchor"), dict)
            or type(offset) is not int or not 0 <= offset <= len(note["text"])
        ):
            raise InspectionError(f"{location} note_offset must locate an existing footnote")
        if any(note["anchor"].get(key) != anchor.get(key) for key in ("verse", "scope", "introduction")):
            raise InspectionError(f"{location} note must belong to the anchor source location")


def _validate_references(path: Path, chapter: dict[str, Any]) -> dict[str, Any]:
    reference = chapter.get("reference")
    if reference is None:
        return {"entry_count": 0, "target_count": 0}
    if not isinstance(reference, dict) or set(reference) != {"items"} or not isinstance(reference["items"], list) or not reference["items"]:
        raise InspectionError(f"{path} reference must be an object with non-empty items")
    items = reference["items"]
    references = _study_ids(items, f"{path} reference")
    notes = {item["id"]: item for item in chapter.get("editorial", []) if item.get("type") == "footnote"}
    targets_count = 0
    for index, item in enumerate(items):
        location = f"{path} reference.items[{index}]"
        required = {"id", "anchor", "text", "targets"}
        if not required.issubset(item) or set(item) - required - {"content", "attrs"}:
            raise InspectionError(f"{location} has invalid reference fields")
        _validate_study_anchor(item["anchor"], chapter, notes, location)
        if not isinstance(item["text"], str) or not isinstance(item["targets"], list):
            raise InspectionError(f"{location} requires text and a targets array")
        if "content" in item:
            _validate_content(item["content"], location, set(notes), references)
        if "attrs" in item:
            _string_attributes(item["attrs"], location)
        for target in item["targets"]:
            if not isinstance(target, dict) or not {"value", "scheme"}.issubset(target) or set(target) - {"value", "scheme", "book", "chapter", "verse", "end"}:
                raise InspectionError(f"{location} has invalid target fields")
            if (
                not isinstance(target["value"], str) or not target["value"]
                or not isinstance(target["scheme"], str)
                or target["scheme"] not in {"osis", "uri", "local", "unresolved"}
            ):
                raise InspectionError(f"{location} target value or scheme is invalid")
            addresses = [target]
            if "end" in target:
                end = target["end"]
                if not isinstance(end, dict) or "book" not in end or set(end) - {"book", "chapter", "verse"} or "book" not in target:
                    raise InspectionError(f"{location} target end requires book addresses")
                addresses.append(end)
            for address in addresses:
                for key in ("book", "chapter", "verse"):
                    if key in address and (type(address[key]) is not int or address[key] < 1 or (key == "book" and address[key] > 281474977710655)):
                        raise InspectionError(f"{location} target {key} must be a positive address")
                if ("verse" in address and "chapter" not in address) or ("chapter" in address and "book" not in address):
                    raise InspectionError(f"{location} target address is incomplete")
            targets_count += 1
    return {"entry_count": len(items), "target_count": targets_count}


def _validate_editorial(
    path: Path,
    chapter: dict[str, Any],
    verse_numbers: Sequence[int],
) -> dict[str, Any]:
    """Validate and summarize the optional chapter-level editorial contract."""

    editorial = chapter.get("editorial")
    if editorial is None:
        return {
            "entry_count": 0,
            "heading_count": 0,
            "paragraph_count": 0,
            "footnote_count": 0,
            "structure_count": 0,
            "entries": [],
        }
    if not isinstance(editorial, list) or not editorial:
        raise InspectionError(f"{path} editorial must be a non-empty array")

    verse_positions = {
        verse_number: position
        for position, verse_number in enumerate(verse_numbers)
    }
    notes_list = [item for item in editorial if isinstance(item, dict) and item.get("type") == "footnote"]
    note_ids = _study_ids(notes_list, f"{path} editorial footnotes")
    notes = {item["id"]: item for item in notes_list}
    reference = chapter.get("reference", {})
    reference_items = reference.get("items", []) if isinstance(reference, dict) else []
    reference_ids = _study_ids(reference_items, f"{path} reference") if isinstance(reference_items, list) else set()
    heading_count = 0
    structure_count = 0
    paragraph_ranges: list[tuple[int, int]] = []
    reading_positions: list[tuple[int, int, int, int]] = []
    for expected_order, entry in enumerate(editorial):
        location = f"{path} editorial[{expected_order}]"
        if not isinstance(entry, dict):
            raise InspectionError(f"{location} must be an object")
        if type(entry.get("order")) is not int or entry["order"] != expected_order:
            raise InspectionError(
                f"{location} order must be the contiguous integer {expected_order}"
            )

        entry_type = entry.get("type")
        if entry_type == "heading":
            if not EDITORIAL_HEADING_FIELDS.issubset(entry) or set(entry) - EDITORIAL_HEADING_FIELDS - EDITORIAL_HEADING_OPTIONAL_FIELDS:
                raise InspectionError(
                    f"{location} heading fields must include "
                    f"{sorted(EDITORIAL_HEADING_FIELDS)}"
                )
            anchor = entry["anchor"]
            if not isinstance(anchor, dict) or set(anchor) != EDITORIAL_ANCHOR_FIELDS:
                raise InspectionError(
                    f"{location} anchor fields must be exactly "
                    f"{sorted(EDITORIAL_ANCHOR_FIELDS)}"
                )
            anchor_verse = anchor["verse"]
            if type(anchor_verse) is not int or anchor_verse not in verse_positions:
                raise InspectionError(
                    f"{location} anchor verse must reference an emitted verse"
                )
            if anchor["edge"] != "before":
                raise InspectionError(f"{location} anchor edge must be 'before'")
            if not isinstance(entry["text"], str) or not entry["text"].strip():
                raise InspectionError(f"{location} text must be non-empty")
            if (
                not isinstance(entry["heading_type"], str)
                or not entry["heading_type"].strip()
            ):
                raise InspectionError(
                    f"{location} heading_type must be a non-empty string"
                )
            if type(entry["canonical"]) is not bool:
                raise InspectionError(f"{location} canonical must be boolean")
            if "content" in entry:
                _validate_content(entry["content"], location, note_ids, reference_ids)
            if "attrs" in entry:
                _string_attributes(entry["attrs"], location)
            if "subtype" in entry and not isinstance(entry["subtype"], str):
                raise InspectionError(f"{location} subtype must be a string")
            if ("tokens" in entry) != ("spans" in entry) or any(not isinstance(entry[key], list) for key in ("tokens", "spans") if key in entry):
                raise InspectionError(f"{location} tokens and spans must be arrays supplied together")
            heading_count += 1
            reading_positions.append((anchor_verse, 0, 0, 0))
            continue

        if entry_type == "paragraph":
            if set(entry) != EDITORIAL_PARAGRAPH_FIELDS:
                raise InspectionError(
                    f"{location} paragraph fields must be exactly "
                    f"{sorted(EDITORIAL_PARAGRAPH_FIELDS)}"
                )
            start = entry["start"]
            end = entry["end"]
            if type(start) is not int or start not in verse_positions:
                raise InspectionError(
                    f"{location} start must reference an emitted verse"
                )
            if type(end) is not int or end not in verse_positions:
                raise InspectionError(
                    f"{location} end must reference an emitted verse"
                )
            if verse_positions[end] < verse_positions[start]:
                raise InspectionError(f"{location} end precedes start")
            paragraph_ranges.append((start, end))
            reading_positions.append((start, 0, 1, 0))
            continue

        if entry_type in {"footnote", "structure"}:
            required = {"order", "type", "anchor", "content"}
            optional: set[str] = set()
            if entry_type == "footnote":
                required |= {"id", "text"}
                optional.add("attrs")
                if not isinstance(entry.get("text"), str):
                    raise InspectionError(f"{location} footnote text must be a string")
            else:
                structure_count += 1
            if not required.issubset(entry) or set(entry) - required - optional:
                raise InspectionError(f"{location} has invalid {entry_type} fields")
            _validate_study_anchor(entry["anchor"], chapter, notes, location)
            _validate_content(entry["content"], location, note_ids, reference_ids)
            if "attrs" in entry:
                _string_attributes(entry["attrs"], location)
            anchor = entry["anchor"]
            reading_positions.append((anchor["verse"], anchor.get("introduction", 0), 2, anchor.get("offset", 0)))
            continue

        raise InspectionError(f"{location} has an unsupported editorial type")

    if reading_positions != sorted(reading_positions):
        raise InspectionError(
            f"{path} editorial entries are not in chapter reading order"
        )

    if paragraph_ranges:
        expected_start_position = 0
        for start, end in paragraph_ranges:
            start_position = verse_positions[start]
            end_position = verse_positions[end]
            if start_position != expected_start_position:
                raise InspectionError(
                    f"{path} editorial paragraph ranges do not completely "
                    "and contiguously cover the emitted verses"
                )
            expected_start_position = end_position + 1
        if expected_start_position != len(verse_numbers):
            raise InspectionError(
                f"{path} editorial paragraph ranges do not end at the "
                "chapter's final emitted verse"
            )

    return {
        "entry_count": len(editorial),
        "heading_count": heading_count,
        "paragraph_count": len(paragraph_ranges),
        "footnote_count": len(notes),
        "structure_count": structure_count,
        "entries": editorial,
    }


def _chapter_summary(
    path: Path,
    chapter_data: dict[str, Any],
    target: BookTarget,
    chapter_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_no_source_envelopes(chapter_data, str(path))
    if "titles" in chapter_data:
        raise InspectionError(
            f"{path} retains redundant chapter titles outside editorial"
        )
    if chapter_data.get("book_nr") != target.number:
        raise InspectionError(
            f"{path} has book_nr={chapter_data.get('book_nr')!r}; expected {target.number}"
        )
    if chapter_data.get("chapter") != chapter_number:
        raise InspectionError(
            f"{path} has chapter={chapter_data.get('chapter')!r}; expected {chapter_number}"
        )
    verses = chapter_data.get("verses")
    if not isinstance(verses, list) or not verses:
        raise InspectionError(f"{path} has no non-empty verses array")
    if not all(isinstance(verse, dict) for verse in verses):
        raise InspectionError(f"{path} contains a non-object verse")

    verse_numbers = [verse.get("verse") for verse in verses]
    if any(not isinstance(number, int) or number <= 0 for number in verse_numbers):
        raise InspectionError(f"{path} contains an invalid verse number")
    expected_numbers = list(range(1, len(verse_numbers) + 1))
    if verse_numbers != expected_numbers:
        raise InspectionError(
            f"{path} verse sequence is incomplete or unordered: {verse_numbers}"
        )

    token_records: list[dict[str, Any]] = []
    span_records: list[dict[str, Any]] = []
    verses_with_tokens = 0
    verses_with_spans_field = 0
    for verse in verses:
        if "reference" in verse:
            raise InspectionError(f"{path} reference data must belong to the chapter")
        if "titles" in verse:
            raise InspectionError(
                f"{path} verse {verse['verse']} retains redundant titles "
                "outside editorial"
            )
        text = verse.get("text")
        if not isinstance(text, str) or not text:
            raise InspectionError(
                f"{path} verse {verse['verse']} has no non-empty text"
            )
        if text.startswith(("\n", "\r")):
            raise InspectionError(
                f"{path} verse {verse['verse']} text begins with a line ending"
            )
        tokens = verse.get("tokens")
        spans = verse.get("spans")
        if tokens is not None:
            if not isinstance(tokens, list) or not all(
                isinstance(item, dict) for item in tokens
            ):
                raise InspectionError(
                    f"{path} verse {verse['verse']} has an invalid tokens field"
                )
            if tokens:
                verses_with_tokens += 1
                token_records.extend(tokens)
            if not isinstance(spans, list):
                raise InspectionError(
                    f"{path} verse {verse['verse']} has tokens but no valid spans array"
                )
        if spans is not None:
            if not isinstance(spans, list) or not all(
                isinstance(item, dict) for item in spans
            ):
                raise InspectionError(
                    f"{path} verse {verse['verse']} has an invalid spans field"
                )
            verses_with_spans_field += 1
            span_records.extend(spans)
    if verses_with_tokens == 0:
        raise InspectionError(f"{path} exposes no KJV token data")

    span_tags = Counter(str(span.get("tag", "<missing>")) for span in span_records)
    editorial_summary = _validate_editorial(path, chapter_data, verse_numbers)
    reference_summary = _validate_references(path, chapter_data)
    note_ids = {item["id"] for item in chapter_data.get("editorial", []) if item["type"] == "footnote"}
    reference_ids = {item["id"] for item in chapter_data.get("reference", {}).get("items", [])}
    for index, introduction in enumerate(chapter_data.get("introduction", [])):
        location = f"{path} introduction[{index}]"
        if not isinstance(introduction, dict) or not isinstance(introduction.get("text"), str):
            raise InspectionError(f"{location} must contain introduction text")
        if "content" in introduction:
            _validate_content(introduction["content"], location, note_ids, reference_ids)
        if not introduction["text"] and not introduction.get("content"):
            raise InspectionError(f"{location} must retain text or source content")
    paragraph_markers = _semantic_summary(chapter_data, verses, "paragraph")
    heading_markers = _semantic_summary(chapter_data, verses, "heading")
    summary = {
        "book": target.name,
        "book_nr": target.number,
        "chapter": chapter_number,
        "file": path.as_posix(),
        "file_bytes": path.stat().st_size,
        "file_human": _human_bytes(path.stat().st_size),
        "chapter_fields": sorted(chapter_data),
        "verse_count": len(verses),
        "verse_range": [verse_numbers[0], verse_numbers[-1]],
        "verse_field_profiles": _field_profiles(verses),
        "tokens": {
            "verses_with_tokens": verses_with_tokens,
            "token_count": len(token_records),
            "token_field_profiles": _field_profiles(token_records),
        },
        "spans": {
            "verses_with_spans_field": verses_with_spans_field,
            "span_count": len(span_records),
            "tags": dict(sorted(span_tags.items())),
            "span_field_profiles": _field_profiles(span_records),
        },
        "editorial": editorial_summary,
        "reference": reference_summary,
        "paragraph_boundaries": paragraph_markers,
        "headings_or_titles": heading_markers,
    }

    semantic_verses = {
        marker.get("verse")
        for marker in paragraph_markers + heading_markers
        if isinstance(marker.get("verse"), int)
    }
    representative = next(
        (verse for verse in verses if verse["verse"] in semantic_verses),
        next((verse for verse in verses if verse.get("spans")), verses[0]),
    )
    return summary, representative


def _require_checksum_siblings(root: Path) -> dict[str, Any]:
    """Every JSON document must have a .sha sibling holding its SHA-1.

    Readers detect a changed document through that small sibling, so an index
    or checksum document without one cannot be watched for changes.
    """

    documents = 0
    problems: list[str] = []
    for path in sorted(root.rglob("*.json")):
        if not path.is_file():
            continue
        documents += 1
        sibling = path.with_suffix(".sha")
        try:
            expected = sibling.read_text(encoding="utf-8")
        except OSError:
            problems.append(f"{path.relative_to(root).as_posix()}: no .sha sibling")
            continue
        if expected != hashlib.sha1(path.read_bytes()).hexdigest() + "\n":
            problems.append(f"{path.relative_to(root).as_posix()}: .sha does not match")
        if len(problems) >= 20:
            break
    if problems:
        raise InspectionError(
            "JSON documents without a matching .sha sibling: " + "; ".join(problems)
        )
    return {"json_documents": documents, "checksum_siblings": documents}


def _validate_openapi(root: Path, abbreviation: str) -> dict[str, Any]:
    """Check that the generated description is true of the tree it sits in.

    The description must be a host-free OpenAPI 3.1 document whose paths all
    start at one version segment, must list the inspected translation, must
    embed a schema for every document type, and must match its checksum.
    """

    path = root / OPENAPI_DOCUMENT
    document = _json(path)
    if document.get("openapi") != OPENAPI_VERSION:
        raise InspectionError(
            f"{path} is not an OpenAPI {OPENAPI_VERSION} description"
        )
    if "servers" in document or "://" in json.dumps(document):
        raise InspectionError(f"{path} names a server or host")
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise InspectionError(f"{path} describes no paths")
    # The tree's root documents are the shallowest paths; their directory is
    # the mount every other path must start at.
    shallowest = min(paths, key=lambda route: (route.count("/"), route))
    mount = shallowest.rsplit("/", 1)[0]
    if not _VERSION_MOUNT.match(mount) or any(
        not route.startswith(mount + "/") for route in paths
    ):
        raise InspectionError(
            f"{path} paths do not all start at one version segment: {sorted(paths)}"
        )
    components = document.get("components")
    if not isinstance(components, dict):
        raise InspectionError(f"{path} has no components")
    parameter = components.get("parameters", {}).get("translation", {})
    listed = parameter.get("schema", {}).get("enum")
    if not isinstance(listed, list) or abbreviation not in listed:
        raise InspectionError(f"{path} does not list the {abbreviation} translation")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict) or not OPENAPI_SCHEMAS.issubset(schemas):
        missing = sorted(OPENAPI_SCHEMAS - set(schemas or ()))
        raise InspectionError(f"{path} embeds no schema for: {', '.join(missing)}")
    checksum_path = root / OPENAPI_CHECKSUM
    try:
        expected = checksum_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InspectionError(f"required checksum is missing: {checksum_path}") from exc
    actual = hashlib.sha1(path.read_bytes()).hexdigest() + "\n"
    if expected != actual:
        raise InspectionError(f"{checksum_path} does not match {path}")
    return {
        "file": path.as_posix(),
        "file_bytes": path.stat().st_size,
        "title": document.get("info", {}).get("title"),
        "mount": mount,
        "path_count": len(paths),
        "paths": sorted(paths),
        "translations": listed,
        "schemas": sorted(schemas),
        "sha": actual.strip(),
    }


def _require_target_book(
    translation: dict[str, Any], target: BookTarget, translation_path: Path
) -> None:
    books = translation.get("books")
    if not isinstance(books, list):
        raise InspectionError(f"{translation_path} has no books array")
    match = next(
        (
            book
            for book in books
            if isinstance(book, dict) and book.get("nr") == target.number
        ),
        None,
    )
    if match is None:
        raise InspectionError(
            f"{translation_path} is missing {target.name} (book {target.number})"
        )
    actual_name = _normalized_name(match.get("name", ""))
    if actual_name not in target.aliases:
        raise InspectionError(
            f"{translation_path} book {target.number} is named {match.get('name')!r}; "
            f"expected {target.name}"
        )


def inspect_api(
    scripture_root: Path,
    output_roots: Sequence[Path],
    *,
    abbreviation: str = "kjv",
    size_limit_bytes: int = int(DEFAULT_SIZE_LIMIT_MIB * MIB),
) -> dict[str, Any]:
    """Validate and return the bounded, log-friendly inspection document."""

    root = scripture_root.resolve()
    if not root.is_dir():
        raise InspectionError(f"Scripture API output directory is missing: {root}")
    size_report = scan_output_sizes(
        output_roots,
        size_limit_bytes=size_limit_bytes,
    )
    openapi_summary = _validate_openapi(root, abbreviation)
    checksum_summary = _require_checksum_siblings(root)
    translation_path = root / f"{abbreviation}.json"
    translation = _json(translation_path)
    _require_no_source_envelopes(translation, str(translation_path))
    if translation.get("abbreviation") != abbreviation:
        raise InspectionError(
            f"{translation_path} abbreviation is {translation.get('abbreviation')!r}; "
            f"expected {abbreviation!r}"
        )

    books_output: list[dict[str, Any]] = []
    for target in TARGET_BOOKS:
        _require_target_book(translation, target, translation_path)
        chapter_summaries: list[dict[str, Any]] = []
        representative_records: list[dict[str, Any]] = []
        for chapter_number in TARGET_CHAPTERS:
            chapter_path = (
                root / abbreviation / str(target.number) / f"{chapter_number}.json"
            )
            chapter_data = _json(chapter_path)
            summary, representative = _chapter_summary(
                chapter_path, chapter_data, target, chapter_number
            )
            chapter_summaries.append(summary)
            representative_records.append(
                {
                    "book": target.name,
                    "book_nr": target.number,
                    "chapter": chapter_number,
                    "representative_verse": representative,
                }
            )
        books_output.append(
            {
                "book": target.name,
                "book_nr": target.number,
                "chapters": chapter_summaries,
                "representative_records": representative_records,
            }
        )

    return {
        "inspection": "getbible-kjv-api/v1",
        "abbreviation": abbreviation,
        "translation_fields": sorted(translation),
        "checksums": checksum_summary,
        "openapi": openapi_summary,
        "size_report": size_report,
        "books": books_output,
    }


def _print_inspection(result: dict[str, Any]) -> None:
    print("KJV API STRUCTURE INSPECTION")
    print("============================")
    print(f"Inspection contract: {result['inspection']}")
    print(f"Translation fields: {', '.join(result['translation_fields'])}")
    print("\nCHECKSUM SIBLINGS")
    print(json.dumps(result["checksums"], ensure_ascii=False, indent=2, sort_keys=True))
    print("\nGENERATED TREE DESCRIPTION")
    print(json.dumps(result["openapi"], ensure_ascii=False, indent=2, sort_keys=True))
    print("\nGENERATED API SIZE REPORT")
    print(
        json.dumps(result["size_report"], ensure_ascii=False, indent=2, sort_keys=True)
    )

    for book in result["books"]:
        print(f"\n{book['book'].upper()} (book {book['book_nr']}) — CHAPTERS 1–5")
        print("-" * 72)
        print("Complete per-chapter structural summaries:")
        print(
            json.dumps(book["chapters"], ensure_ascii=False, indent=2, sort_keys=True)
        )
        print("Representative API verse records (one complete record per chapter):")
        for record in book["representative_records"]:
            verse = record["representative_verse"]
            print(f"\n{book['book']} {record['chapter']}:{verse.get('verse', '?')}")
            print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))

    print("\nINSPECTION PASSED")
    print(
        "All requested books and chapters are present, no source envelopes remain, "
        "verse text has no leading line endings, KJV token/span and editorial "
        "fields are structurally valid, every JSON document has a matching .sha "
        "sibling, openapi.json describes the tree it sits in, and every generated "
        "API file is below the size ceiling."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and print a bounded structural inspection of a fresh KJV API build."
    )
    parser.add_argument(
        "--scripture-root",
        required=True,
        type=Path,
        help="generated Scripture JSON root containing kjv.json",
    )
    parser.add_argument(
        "--output-root",
        action="append",
        required=True,
        type=Path,
        help="generated API root to include in the size gate; repeat for each root",
    )
    parser.add_argument("--abbreviation", default="kjv")
    parser.add_argument(
        "--size-limit-mib",
        type=float,
        default=DEFAULT_SIZE_LIMIT_MIB,
        help=(
            "fail when any output is at least this many MiB "
            f"(default: {DEFAULT_SIZE_LIMIT_MIB:g})"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.size_limit_mib > 0:
        print("ERROR: --size-limit-mib must be greater than zero", file=sys.stderr)
        return 2
    try:
        result = inspect_api(
            args.scripture_root,
            args.output_root,
            abbreviation=args.abbreviation,
            size_limit_bytes=int(args.size_limit_mib * MIB),
        )
    except InspectionError as exc:
        print(f"INSPECTION FAILED: {exc}", file=sys.stderr)
        return 1
    _print_inspection(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
