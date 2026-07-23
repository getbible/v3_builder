#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect a lean KJV build without flooding a GitHub Actions log.

The builder deliberately emits minified JSON.  This script validates the
requested inspection surface, reports every chapter's structural shape, and
prints one complete representative verse per chapter.  It also enforces a
pre-publication file-size ceiling across every supplied API output root.
"""

from __future__ import annotations

import argparse
import json
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
FORBIDDEN_SOURCE_FIELDS = frozenset({"source", "source_contract"})
EDITORIAL_HEADING_FIELDS = frozenset(
    {"order", "type", "anchor", "text", "heading_type", "canonical"}
)
EDITORIAL_PARAGRAPH_FIELDS = frozenset({"order", "type", "start", "end"})
EDITORIAL_ANCHOR_FIELDS = frozenset({"verse", "edge"})


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
            "entries": [],
        }
    if not isinstance(editorial, list) or not editorial:
        raise InspectionError(f"{path} editorial must be a non-empty array")

    verse_positions = {
        verse_number: position
        for position, verse_number in enumerate(verse_numbers)
    }
    heading_count = 0
    paragraph_ranges: list[tuple[int, int]] = []
    reading_positions: list[tuple[int, int]] = []
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
            if set(entry) != EDITORIAL_HEADING_FIELDS:
                raise InspectionError(
                    f"{location} heading fields must be exactly "
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
            heading_count += 1
            reading_positions.append((verse_positions[anchor_verse], 0))
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
            reading_positions.append((verse_positions[start], 1))
            continue

        raise InspectionError(
            f"{location} type must be 'heading' or 'paragraph'"
        )

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
        "entries": editorial,
    }


def _chapter_summary(
    path: Path,
    chapter_data: dict[str, Any],
    target: BookTarget,
    chapter_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_no_source_envelopes(chapter_data, str(path))
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
        "size_report": size_report,
        "books": books_output,
    }


def _print_inspection(result: dict[str, Any]) -> None:
    print("KJV API STRUCTURE INSPECTION")
    print("============================")
    print(f"Inspection contract: {result['inspection']}")
    print(f"Translation fields: {', '.join(result['translation_fields'])}")
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
        "fields are structurally valid, and every generated API file is below "
        "the size ceiling."
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
