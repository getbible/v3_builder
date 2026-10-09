# SPDX-License-Identifier: GPL-2.0-only
"""Convert validated getBibleSWORD contracts into the getBible v3 API shape."""

from __future__ import annotations

import os
import json
import logging
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Any

from book_identity import BookIdentityError, BookResolver
from converter import ConversionConfig, normalize_verse_text
from file_ops import write_json_minified
from getbiblesword_contract import (
    ContractSummary,
    byte_value_text,
    decode_byte_value,
    iter_contract,
    validate_contract,
)
from osis_parser import (
    osis_plain_text,
    parse_osis_semantics,
    parse_osis_verse,
)
from source_formats import decode_source, normalize_source
from study_annotations import extract_study


LOGGER = logging.getLogger(__name__)


def _content_children(element):
    """Represent decoded introduction markup without publishing source bytes."""
    content = [element.text] if element.text else []
    for child in element:
        node = {"tag": child.tag, "children": _content_children(child)}
        if child.attrib:
            node["attrs"] = dict(child.attrib)
        content.append(node)
        if child.tail:
            content.append(child.tail)
    return content


def _relabel_study(study, suffix):
    """Keep chapter-local links unique for independent source fragments."""
    ids = {
        item["id"]: item["id"] + suffix
        for item in study.get("footnotes", []) + study.get("references", [])
    }

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"id", "footnote_id", "reference_id", "note"} and isinstance(item, str):
                    value[key] = ids.get(item, item)
                else:
                    walk(item)

    walk(study)


def _merge_recovered_study(study, recovered, suffix):
    """Merge native-only annotations while retaining links to existing bodies."""
    def signature(item):
        return (" ".join(item["text"].split()), json.dumps(item.get("targets"), sort_keys=True))

    known = {signature(item): item for item in study["footnotes"] + study["references"]}
    _relabel_study(recovered, suffix)
    aliases = {
        item["id"]: known[signature(item)]["id"]
        for item in recovered["footnotes"] + recovered["references"]
        if signature(item) in known
    }

    def relink(value):
        if isinstance(value, list):
            for child in value:
                relink(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"footnote_id", "reference_id", "note"} and isinstance(child, str):
                    value[key] = aliases.get(child, child)
                else:
                    relink(child)

    relink(recovered)
    added = False
    for field in ("footnotes", "references"):
        recovered[field] = [item for item in recovered[field] if item["id"] not in aliases]
        for item in recovered[field]:
            item["anchor"].pop("offset", None)
            item["anchor"]["alignment"] = "unresolved"
        study[field].extend(recovered[field])
        added = added or bool(recovered[field])
    if added:
        study["diagnostics"].extend(recovered["diagnostics"])
    return added


def _supplement_study(study, record, markup, verse, text, *, book_resolver=None):
    """Recover native-only heading/note bodies without guessing their anchors."""
    attributes = _official_attributes(record)
    known_titles = {title["text"] for title in study.get("title_content", [])}
    for index, (identifier, body) in enumerate(_native_headings(attributes.get("Heading", {}))):
        normalized, diagnostics = normalize_source(body, markup)
        titles = parse_osis_semantics(normalized).get("titles", [])
        if not titles:
            heading_text = osis_plain_text(normalized)
            if not heading_text or not heading_text.strip():
                continue
            titles = [{"text": heading_text.strip()}]
            # SWORD may strip the original preverse wrapper while retaining
            # its inline notes/references. Give that content its title scope.
            normalized = "<title>" + normalized + "</title>"
        if all(title["text"] in known_titles for title in titles):
            continue
        recovered = extract_study(normalized, text, verse,
                                  context={"book_resolver": book_resolver})
        recovered["title_content"] = [title for title in recovered.get("title_content", [])
                                      if title["text"] not in known_titles]
        added = _merge_recovered_study(study, recovered, f"-sword-heading-{index + 1}")
        study.setdefault("title_content", []).extend(recovered["title_content"])
        study["diagnostics"].extend(diagnostics)
        known_titles.update(title["text"] for title in recovered["title_content"])
        if added:
            study["diagnostics"].append({
                "code": "official_heading_anchor_unresolved",
                "message": f"Recovered study annotations in SWORD heading {identifier}; no display offset was inferred.",
            })

    notes = attributes.get("Footnote", {})
    for index, (identifier, values) in enumerate(notes.items()):
        normalized_values = {key.lower(): value for key, value in values.items()}
        body = normalized_values.get("body", "")
        if not body:
            continue
        body, diagnostics = normalize_source(body, markup)
        try:
            wrapper = ET.fromstring("<note>" + body + "</note>")
        except ET.ParseError:
            study["diagnostics"].append({"code": "official_note_unparsed", "message": f"SWORD footnote {identifier} body is not parseable markup."})
            continue
        for key, value in values.items():
            if key.lower() != "body":
                wrapper.set(key, value)
        recovered = extract_study(ET.tostring(wrapper, encoding="unicode"), text, verse,
                                  context={"book_resolver": book_resolver})
        if _merge_recovered_study(study, recovered, f"-sword-{index + 1}"):
            study["diagnostics"].extend(diagnostics)
            study["diagnostics"].append({"code": "official_note_anchor_unresolved", "message": f"Recovered SWORD footnote {identifier}; source supplies no recoverable display offset."})


class ConversionError(ValueError):
    """Raised when a valid native contract cannot map to Scripture API v3."""


def _text(value: Any, location: str) -> str:
    """Decode verified module text, accepting mixed legacy single-byte content."""

    if value is None:
        return ""
    data = decode_byte_value(value, location=location)
    decoded = data.decode("utf-8", errors="surrogateescape")
    if not any(0xDC80 <= ord(character) <= 0xDCFF for character in decoded):
        return decoded

    result = []
    for character in decoded:
        codepoint = ord(character)
        if not 0xDC80 <= codepoint <= 0xDCFF:
            result.append(character)
            continue
        byte = bytes((codepoint - 0xDC00,))
        try:
            result.append(byte.decode("cp1252"))
        except UnicodeDecodeError:
            result.append(byte.decode("latin-1"))
    return "".join(result)


def _utf8_text(value: Any, location: str) -> str:
    """Decode text only when it is safe to treat as Unicode semantic markup."""

    if value is None:
        return ""
    return byte_value_text(value, location=location)


def _entry_text(record: dict[str, Any], markup: str, encoding: str = "", *, diagnostics=None) -> str:
    """Return display text without rejecting valid legacy module bytes.

    UTF-8 is preferred.  When an OSIS module has only a malformed stripped
    projection, valid raw or rendered OSIS remains the best source of visible
    text.  Other historic SWORD modules can contain ISO-8859-1 bytes even when
    their configuration claims UTF-8. Valid UTF-8 sequences are retained while
    only undecodable bytes use the SWORD-compatible Windows-1252/Latin-1
    fallback. This keeps the API text usable instead of aborting every other
    translation in the build.
    """

    if record.get("normalized_stripped") is not None:
        try:
            return _utf8_text(record["normalized_stripped"], "entry.normalized_stripped")
        except UnicodeError:
            if diagnostics is not None:
                diagnostics.append({
                    "code": "source.normalized_stripped_invalid",
                    "message": "Native stripped projection is not valid UTF-8; recovering display text from source.",
                })
            source, warnings = _source_projection(record, markup, encoding)
            if diagnostics is not None:
                diagnostics.extend(warnings)
            plain = osis_plain_text(source) if source is not None else None
            if plain is not None:
                return plain
    elif "normalized_stripped" in record and record.get("normalized_raw") is not None:
        source, warnings = _source_projection(record, markup, encoding)
        if diagnostics is not None:
            diagnostics.extend(warnings)
        plain = osis_plain_text(source) if source is not None else None
        if plain is not None:
            return plain
    label = "".join(character for character in encoding.lower() if character.isalnum())
    if label in {"utf16", "utf16le", "utf16be", "utf32", "utf32le", "utf32be", "scsu"}:
        source, _ = _source_projection(record, markup, encoding)
        plain = osis_plain_text(source) if source is not None else None
        if plain is not None:
            return plain
        try:
            return decode_source(decode_byte_value(record["stripped"], location="entry.stripped"), encoding)
        except (UnicodeError, ValueError) as exc:
            raise ConversionError(
                f"Cannot decode entry {record.get('ordinal')} with encoding {encoding}; "
                "a valid native UTF-8 projection is required"
            ) from exc
    if label not in {"", "utf8", "utf8sig"}:
        try:
            return decode_source(decode_byte_value(record["stripped"], location="entry.stripped"), encoding)
        except (UnicodeError, ValueError) as exc:
            raise ConversionError(f"Cannot decode entry {record.get('ordinal')} with encoding {encoding}: {exc}") from exc
    try:
        return _utf8_text(record.get("stripped"), "entry.stripped")
    except UnicodeDecodeError:
        if markup.lower() == "osis":
            for projection in ("raw", "rendered_default"):
                try:
                    projected_text = _utf8_text(
                        record.get(projection), f"entry.{projection}"
                    )
                except UnicodeDecodeError:
                    continue
                plain_text = osis_plain_text(projected_text)
                if plain_text is not None:
                    return plain_text
        data = decode_byte_value(record.get("stripped"), location="entry.stripped")
        return decode_source(data, encoding)


def _source_projection(record: dict[str, Any], markup: str, encoding: str):
    """Normalize semantic markup without changing the authoritative envelope."""
    diagnostics = []
    for key in ("normalized_raw", "raw"):
        value = record.get(key)
        if value is None:
            continue
        try:
            data = decode_byte_value(value, location=f"entry.{key}")
            source = data.decode("utf-8") if key == "normalized_raw" else decode_source(data, encoding)
            normalized, warnings = normalize_source(source, markup)
            diagnostics.extend(warnings)
            return normalized, diagnostics
        except (UnicodeError, ValueError) as exc:
            diagnostics.append({"code": "source.encoding", "message": str(exc)})
    return None, diagnostics


def _official_attributes(record: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    """Read the ordered native attribute map; byte envelopes never escape."""
    attributes = {}
    for group in record.get("official_attributes", []):
        name = _text(group.get("name"), "attribute.name")
        lists = attributes.setdefault(name, {})
        for item in group.get("lists", []):
            key = _text(item.get("name"), "attribute.list.name")
            values = lists.setdefault(key, {})
            for value in item.get("values", []):
                values[_text(value.get("name"), "attribute.value.name")] = _text(
                    value.get("value"), "attribute.value"
                )
    return attributes


def _native_headings(attributes):
    """Read both heading groups populated by SWORD's source filters."""
    for position in ("Preverse", "Interverse"):
        yield from sorted(attributes.get(position, {}).items(), key=lambda item: (
            not item[0].isdigit(), int(item[0]) if item[0].isdigit() else item[0]
        ))


def _source_semantics(source, record, markup):
    """Prefer source titles, supplementing missing SWORD preverse headings."""
    semantics = parse_osis_semantics(source) if source else {}
    titles = semantics.setdefault("titles", [])
    existing = {title["text"]: title for title in titles}
    heading_attributes = _official_attributes(record).get("Heading", {})
    for identifier, value in _native_headings(heading_attributes):
        normalized, _ = normalize_source(value, markup)
        recovered = parse_osis_semantics(normalized).get("titles", [])
        if not recovered:
            # Native preverse attributes can omit their outer title/div tag.
            # Restore the title scope so inline tokens and study links survive.
            recovered = parse_osis_semantics("<title>" + normalized + "</title>").get("titles", [])
        for title in recovered:
            attributes = heading_attributes.get(identifier, {})
            canonical = attributes.get("canonical", "").lower()
            if "canonical" not in title and canonical in {"true", "1", "yes", "false", "0", "no"}:
                title["canonical"] = canonical in {"true", "1", "yes"}
            for original, target in (("type", "type"), ("subType", "subtype")):
                if original in attributes:
                    title.setdefault(target, attributes[original])
            if attributes:
                for key, attribute in attributes.items():
                    title.setdefault("attrs", {}).setdefault(key, attribute)
            if title["text"] in existing:
                original = existing[title["text"]]
                for key, value in title.items():
                    if key == "attrs":
                        for name, attribute in value.items():
                            original.setdefault("attrs", {}).setdefault(name, attribute)
                    else:
                        original.setdefault(key, value)
            else:
                titles.append(title)
                existing[title["text"]] = title
    if not titles:
        semantics.pop("titles", None)
    return semantics


def _reconcile_title_content(semantics, study):
    """Heading trees link to the same chapter note/reference records."""
    available = list(study.get("title_content", []))
    for title in semantics.get("titles", []):
        index = next((i for i, item in enumerate(available)
                      if item["text"] == title["text"]
                      and all(title.get("attrs", {}).get(key) == value
                              for key, value in item.get("attrs", {}).items())), None)
        if index is not None:
            item = available.pop(index)
            if "content" in title:
                title["content"] = item["content"]


def _build_chapter_editorial(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ordered chapter-level reading-layout contract.

    Transient chapter/verse ``titles`` and public verse-level ``paragraph``
    markers are the projection inputs. ``editorial`` is the public chapter
    heading contract: headings are anchored before a verse, and paragraph
    starts are closed into inclusive verse ranges that cannot cross the current
    chapter.
    """

    verses = [
        verse
        for verse in chapter.get("verses", [])
        if isinstance(verse, dict)
        and isinstance(verse.get("verse"), int)
        and verse["verse"] > 0
    ]
    if not verses:
        return [
            {"order": index, **entry}
            for index, entry in enumerate(chapter.get("_study_editorial", []))
        ]

    positioned: list[tuple[int, int | tuple[int, int], int, dict[str, Any]]] = []
    seen_headings: set[tuple[int, str, str, bool, str]] = set()
    sequence = 0

    def add_heading(title: Any, anchor_verse: int) -> None:
        nonlocal sequence
        if not isinstance(title, dict):
            return
        text = title.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        heading_type = title.get("type")
        if not isinstance(heading_type, str) or not heading_type.strip():
            heading_type = "unspecified"
        canonical = title.get("canonical") is True
        identity = (anchor_verse, text, heading_type, canonical, json.dumps(
            {key: title[key] for key in ("attrs", "content", "subtype", "tokens", "spans") if key in title},
            sort_keys=True, ensure_ascii=False,
        ))
        if identity in seen_headings:
            return
        seen_headings.add(identity)
        positioned.append(
            (
                anchor_verse,
                0,
                sequence,
                {
                    "type": "heading",
                    "anchor": {
                        "verse": anchor_verse,
                        "edge": "before",
                    },
                    "text": text,
                    "heading_type": heading_type,
                    "canonical": canonical,
                    **{key: title[key] for key in ("tokens", "spans", "content", "attrs", "subtype") if key in title},
                },
            )
        )
        sequence += 1

    first_verse = verses[0]["verse"]
    for title in chapter.get("titles", []):
        add_heading(title, first_verse)
    for source_verse, title in chapter.get("_pending_titles", []):
        following = next((verse["verse"] for verse in verses if verse["verse"] >= source_verse), None)
        if following is not None:
            add_heading(title, following)
        else:
            chapter.setdefault("_study_editorial", []).append({
                "type": "structure",
                "anchor": {"verse": source_verse, "scope": "source", "alignment": "unresolved"},
                "content": [{"tag": "title", "attrs": title.get("attrs", {}),
                             "children": title.get("content", [title["text"]])}],
            })
    for verse in verses:
        for title in verse.get("titles", []):
            add_heading(title, verse["verse"])

    paragraph_starts = [
        position
        for position, verse in enumerate(verses)
        if verse.get("paragraph") is True
    ]
    if paragraph_starts:
        if paragraph_starts[0] != 0:
            paragraph_starts.insert(0, 0)
        for index, start_position in enumerate(paragraph_starts):
            next_position = (
                paragraph_starts[index + 1]
                if index + 1 < len(paragraph_starts)
                else len(verses)
            )
            positioned.append(
                (
                    verses[start_position]["verse"],
                    1,
                    sequence,
                    {
                        "type": "paragraph",
                        "start": verses[start_position]["verse"],
                        "end": verses[next_position - 1]["verse"],
                    },
                )
            )
            sequence += 1

    for entry in chapter.get("_study_editorial", []):
        anchor = entry["anchor"]
        verse_number = anchor["verse"]
        position = verse_number
        phase = (anchor.get("introduction", 0), 2 + anchor.get("offset", 0))
        positioned.append((position, phase, sequence, entry))
        sequence += 1

    positioned.sort(key=lambda item: (item[0], item[1] if isinstance(item[1], tuple) else (0, item[1]), item[2]))
    return [
        {"order": order, **entry}
        for order, (_, _, _, entry) in enumerate(positioned)
    ]


def _finalize_chapter(chapter: dict[str, Any]) -> None:
    """Finalize editorial and remove its transient duplicate title fields."""

    editorial = _build_chapter_editorial(chapter)
    chapter.pop("titles", None)
    verses = chapter.pop("verses")
    for verse in verses:
        verse.pop("titles", None)
    chapter.pop("_pending_titles", None)
    chapter.pop("_study_editorial", None)
    if editorial:
        chapter["editorial"] = editorial
    # Keep the usually large verses array last in every public representation.
    chapter["verses"] = verses


class GetBibleSwordConverter:
    """Build Bible JSON from a validated native contract.

    Lossless records and chapter/verse title collections are transient build
    inputs: byte envelopes, source/config records, annotation segments, and
    duplicate title arrays are not copied into the static API. Book titles,
    compact chapter editorial, verse paragraph markers, normalized verse text,
    and complete derived token/span data are retained instead.
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
        self._book_resolver = BookResolver(config.book_numbers, config.book_names)
        self._semantic_state: dict[str, Any] = {}
        self._semantic_book = None
        self._encoding = ""
        self._diagnostic_counts: dict[str, int] = {}

    def convert(
        self,
        contract_path: str,
        *,
        module_name: str | None = None,
        summary: ContractSummary | None = None,
    ) -> str:
        if summary is None:
            summary = validate_contract(
                contract_path,
                expected_module=module_name,
                expected_classification="bible",
            )
        else:
            if Path(summary.path).resolve() != Path(contract_path).resolve():
                raise ConversionError("contract summary belongs to a different file")
            if module_name is not None and summary.module_name != module_name:
                raise ConversionError(
                    f"contract contains module {summary.module_name!r}, "
                    f"expected {module_name!r}"
                )
            if summary.classification != "bible":
                raise ConversionError(
                    f"module classification is {summary.classification!r}, "
                    "expected 'bible'"
                )
        if summary.unknown_record_types:
            unsupported = ", ".join(summary.unknown_record_types)
            raise ConversionError(
                "validated contract contains unmapped record types: "
                f"{unsupported}"
            )

        module: dict[str, Any] | None = None
        config_map: dict[str, str] = {}
        shared_meta: dict[str, Any] | None = None
        bible: dict[str, Any] | None = None
        abbreviation = ""
        markup = ""
        books: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._semantic_state = {}
        self._semantic_book = None
        self._diagnostic_counts = {}

        # Validation is a bounded first pass over the untrusted contract.  The
        # conversion pass never retains complete entry records: each entry is
        # projected and discarded immediately.  This prevents a 500 MiB
        # contract from expanding into multiple GiB of Python objects.
        for record in iter_contract(contract_path):
            record_type = record["type"]
            if record_type == "module":
                module = record
            elif record_type == "config_entry":
                self._add_configuration_entry(config_map, record)
            elif record_type == "entry":
                if module is None:
                    raise ConversionError(
                        "validated contract entry precedes its module record"
                    )
                if bible is None:
                    abbreviation, markup, shared_meta, bible = (
                        self._initialize_documents(
                            module,
                            summary.module_name,
                            config_map,
                        )
                    )
                self._consume_entry(
                    bible,
                    books,
                    record,
                    markup,
                    abbreviation,
                )

        if module is None:
            raise ConversionError("validated contract is missing its module record")
        if bible is None:
            abbreviation, markup, shared_meta, bible = self._initialize_documents(
                module,
                summary.module_name,
                config_map,
            )
        if shared_meta is None:
            raise ConversionError("failed to initialize API metadata")

        output_root = Path(self._output_path)
        output_root.mkdir(parents=True, exist_ok=True)
        for book_number, book in books.items():
            book.pop("_sword_name", None)
            book.pop("_source_identity", None)
            book.pop("_source_position", None)
            chapters = [
                chapter for chapter in book.pop("_chapters").values()
                if chapter["verses"] or chapter.get("titles") or chapter.get("introduction")
                or chapter.get("_study_editorial") or chapter.get("reference") or chapter.get("_pending_titles")
            ]
            if not chapters and not book.get("titles") and not book.get("introduction"):
                continue
            for chapter in chapters:
                if not chapter["verses"]:
                    for _, title in chapter.get("_pending_titles", []):
                        if title not in chapter.setdefault("titles", []):
                            chapter["titles"].append(title)
                    extra = _build_chapter_editorial(chapter)
                    if extra:
                        chapter["editorial"] = extra
                    chapter.pop("_study_editorial", None)
                    chapter.pop("_pending_titles", None)
                    continue
                _finalize_chapter(chapter)
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
            if "titles" in book:
                book_data["titles"] = book["titles"]
            if "introduction" in book:
                book_data["introduction"] = book["introduction"]
            write_json_minified(
                book_data,
                str(output_root / abbreviation / f"{book_number}.json"),
            )

        bible.update(self._distribution_metadata(config_map, abbreviation))
        version_path = output_root / f"{abbreviation}.json"
        write_json_minified(bible, str(version_path))
        for code, count in sorted(self._diagnostic_counts.items()):
            LOGGER.warning("%s: %s (%d source occurrences)", summary.module_name, code, count)
        return str(version_path)

    def _initialize_documents(
        self,
        module: dict[str, Any],
        module_name: str,
        config_map: dict[str, str],
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        """Create lean API metadata after all ordered config records are read."""

        abbreviation = self._config.translation_names.get(
            module_name, module_name.lower()
        )
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
        self._encoding = encoding
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
        }
        bible: dict[str, Any] = {
            **shared_meta,
            "description": description,
            "books": [],
        }
        return abbreviation, markup, shared_meta, bible

    @staticmethod
    def _add_configuration_entry(
        values: dict[str, str],
        entry: dict[str, Any],
    ) -> None:
        """Decode only the config value needed by the API, then drop its record."""

        name = _text(entry["name"], "config_entry.name").lower()
        value = _text(entry["value"], "config_entry.value")
        values[name] = value

    def _consume_entry(
        self,
        bible: dict[str, Any],
        books: OrderedDict[int, dict[str, Any]],
        record: dict[str, Any],
        markup: str,
        abbreviation: str,
    ) -> None:
        """Project one validated entry and immediately release its envelopes."""

        scope = record.get("scope")
        if not isinstance(scope, dict) or scope.get("type") != "verse_key":
            # Domain-incompatible entries have no stable Scripture API address.
            # The validated NDJSON remains the build input for this run, but its
            # raw record is intentionally not copied into public JSON.
            return
        if scope.get("intro_scope") != "verse":
            self._attach_introduction(bible, books, record, markup)
            return

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
        if self._semantic_book != book["nr"]:
            self._semantic_state = {}
            self._semantic_book = book["nr"]
        chapter = book["_chapters"].setdefault(
            chapter_number,
            {
                "chapter": chapter_number,
                "name": f"{book['name']} {chapter_number}",
                "verses": [],
            },
        )
        diagnostics = []
        verse = self._verse(
            record,
            book["name"],
            chapter_number,
            verse_number,
            markup,
            encoding=self._encoding,
            state=self._semantic_state,
            diagnostics=diagnostics,
            book_resolver=self._book_resolver,
        )
        if verse is not None:
            study = verse.pop("_study", {})
            self._attach_study(chapter, study)
            chapter["verses"].append(verse)
        else:
            source, warnings = _source_projection(record, markup, self._encoding)
            diagnostics.extend(warnings)
            semantics = _source_semantics(source, record, markup)
            preserve_unparsed = any(item["code"] in {"unsupported_source_format", "unparsed_source_fragment"} for item in warnings)
            study = extract_study(source or "", "", verse_number, context={
                "book_resolver": self._book_resolver, "retain_content": preserve_unparsed,
            })
            _supplement_study(study, record, markup, verse_number, "", book_resolver=self._book_resolver)
            _reconcile_title_content(semantics, study)
            for title in semantics.get("titles", []):
                chapter.setdefault("_pending_titles", []).append((verse_number, title))
            if study:
                study["verse"] = verse_number
                study["anchor"] = {"verse": verse_number, "scope": "source", "alignment": "unresolved"}
                for item in study.get("footnotes", []) + study.get("references", []):
                    item["anchor"].pop("offset", None)
                    item["anchor"].update(scope="source", alignment="unresolved")
                self._attach_study(chapter, study)
                diagnostics.extend(study.get("diagnostics", []))
        self._record_diagnostics(record, diagnostics)

    def _record_diagnostics(self, record, diagnostics):
        for diagnostic in diagnostics:
            code = diagnostic.get("code", "source.unsupported")
            self._diagnostic_counts[code] = self._diagnostic_counts.get(code, 0) + 1
            if self._diagnostic_counts[code] <= 3:
                LOGGER.warning("Entry %s: %s: %s", record.get("ordinal"), code, diagnostic.get("message", ""))

    @staticmethod
    def _attach_study(chapter, study, *, introduction=None):
        footnotes = study.get("footnotes", [])
        references = study.get("references", [])
        if introduction is not None:
            for item in footnotes + references:
                item["anchor"].update(verse=0, scope="introduction", introduction=introduction)
        chapter.setdefault("_study_editorial", []).extend(footnotes)
        if study.get("content"):
            if introduction is None:
                anchor = study.get("anchor", {"verse": study.get("verse", 1), "offset": 0})
            else:
                anchor = {"verse": 0, "scope": "introduction", "introduction": introduction, "offset": 0}
            chapter["_study_editorial"].append({"type": "structure", "anchor": anchor, "content": study["content"]})
        if references:
            chapter.setdefault("reference", {"items": []})["items"].extend(references)

    def _book_for_scope(
        self,
        books: OrderedDict[int, dict[str, Any]],
        scope: dict[str, Any],
        abbreviation: str,
    ) -> dict[str, Any]:
        sword_name = _text(scope.get("book_name"), "entry.scope.book_name")
        osis_reference = _text(
            scope.get("osis_reference"), "entry.scope.osis_reference"
        )
        abbreviation_text = _text(
            scope.get("book_abbreviation"), "entry.scope.book_abbreviation"
        )
        try:
            identity = self._book_resolver.resolve(
                sword_name, osis_reference=osis_reference,
                abbreviation=abbreviation_text,
            )
        except BookIdentityError as exc:
            raise ConversionError(str(exc)) from exc
        book_number = identity.number
        # Native book indices are local positions, never public book numbers.
        # They only detect two distinct source books claiming one identity.
        position = (scope.get("testament"), scope.get("book"))
        claimed = books.get(book_number)
        if claimed is not None and (
            claimed["_source_identity"] != identity.key
            or claimed["_source_position"] != position
        ):
            raise ConversionError(
                f"SWORD books {claimed['_sword_name']!r} and {sword_name!r} both "
                f"map to book number {book_number}; their source identities "
                "or positions disagree"
            )
        if book_number not in books:
            display_name = self._resolve_book_name(
                book_number,
                identity.name,
                abbreviation,
                self._conf_dir,
                self._config,
            )
            books[book_number] = {
                "nr": book_number,
                "name": display_name,
                "_sword_name": sword_name,
                "_source_identity": identity.key,
                "_source_position": position,
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
        intro_scope = scope.get("intro_scope")
        if intro_scope in {"module", "testament"}:
            target = bible
        elif intro_scope in {"book", "chapter"}:
            book = self._book_for_scope(
                books, scope, bible["abbreviation"]
            )
            target = book
        else:
            return

        if intro_scope == "chapter":
            chapter_number = scope.get("chapter")
            if not isinstance(chapter_number, int) or chapter_number <= 0:
                raise ConversionError(
                    f"invalid chapter introduction at entry {record.get('ordinal')}"
                )
            chapter = book["_chapters"].setdefault(
                chapter_number,
                {
                    "chapter": chapter_number,
                    "name": f"{book['name']} {chapter_number}",
                    "verses": [],
                },
            )
            target = chapter

        osis, diagnostics = _source_projection(record, markup, self._encoding)
        semantics = _source_semantics(osis, record, markup)
        self._merge_semantics(target, semantics)

        text = _entry_text(record, markup, self._encoding, diagnostics=diagnostics)
        visible_text = text.strip()
        title_texts = {
            title["text"]
            for title in semantics.get("titles", [])
            if isinstance(title.get("text"), str)
        }
        # Structural book/chapter entries commonly strip to whitespace while
        # their useful title remains in raw OSIS.  Store actual prose as an
        # introduction, but do not duplicate a promoted title string.
        study = None
        if intro_scope == "chapter":
            study = extract_study(osis or "", text, 0, context={"book_resolver": self._book_resolver, "retain_content": True})
            _supplement_study(study, record, markup, 0, text, book_resolver=self._book_resolver)
            _relabel_study(study, f"-intro-{len(target.get('introduction', []))}")
            _reconcile_title_content(semantics, study)
        has_study = study and (study.get("footnotes") or study.get("references"))
        if (visible_text and visible_text not in title_texts) or has_study:
            introduction = {"text": text}
            if osis:
                try:
                    root = ET.fromstring("<r>" + osis + "</r>")
                except ET.ParseError:
                    root = None
                if study and study.get("content"):
                    introduction["content"] = study["content"]
                elif root is not None and list(root):
                    # Introductions have their own text space; preserve nested
                    # markup without reinterpreting it as verse content.
                    introduction["content"] = _content_children(root)
            if not text and not introduction.get("content") and has_study:
                # Native-only annotations still need a concrete introduction
                # document for their introduction-scoped links. Preserve the
                # recovered heading tree, or body links when only notes exist.
                introduction["content"] = [
                    {"tag": "title", "children": title["content"],
                     **({"attrs": title["attrs"]} if title.get("attrs") else {})}
                    for title in study.get("title_content", [])
                ] or [
                    {"tag": tag, "attrs": {link: item["id"]}, "children": []}
                    for field, tag, link in (("footnotes", "note", "footnote_id"),
                                             ("references", "reference", "reference_id"))
                    for item in study[field] if "note" not in item["anchor"]
                ]
            index = len(target.setdefault("introduction", []))
            target["introduction"].append(introduction)
            if study:
                study["content"] = []  # Already retained in this introduction.
                self._attach_study(target, study, introduction=index)
                diagnostics.extend(study.get("diagnostics", []))
        self._record_diagnostics(record, diagnostics)

    @staticmethod
    def _merge_semantics(
        target: dict[str, Any],
        semantics: dict[str, Any],
    ) -> None:
        """Merge ordered structural semantics without duplicating titles."""

        for title in semantics.get("titles", []):
            titles = target.setdefault("titles", [])
            if title not in titles:
                titles.append(title)

    @staticmethod
    def _verse(
        record: dict[str, Any],
        book_name: str,
        chapter: int,
        verse_number: int,
        markup: str,
        *,
        encoding: str = "",
        state: dict[str, Any] | None = None,
        diagnostics: list | None = None,
        book_resolver: BookResolver | None = None,
    ) -> dict[str, Any] | None:
        diagnostics = diagnostics if diagnostics is not None else []
        text = normalize_verse_text(_entry_text(record, markup, encoding, diagnostics=diagnostics))
        osis, warnings = _source_projection(record, markup, encoding)
        diagnostics.extend(warnings)
        if not text.replace("[]", "").strip():
            if osis:
                parse_osis_verse(osis, text, state=state, diagnostics=diagnostics)
            return None
        verse: dict[str, Any] = {
            "chapter": chapter,
            "verse": verse_number,
            "name": f"{book_name} {chapter}:{verse_number}",
            "text": text,
        }
        if osis is not None:
            semantics = _source_semantics(osis, record, markup)
            if semantics.get("paragraph"):
                verse["paragraph"] = True
            if semantics.get("titles"):
                verse["titles"] = semantics["titles"]
            word_data = parse_osis_verse(osis, text, state=state, diagnostics=diagnostics)
            if word_data:
                verse["tokens"] = word_data["tokens"]
                verse["spans"] = word_data["spans"]
            preserve_unparsed = any(item["code"] in {"unsupported_source_format", "unparsed_source_fragment"} for item in warnings)
            study = extract_study(osis, text, verse_number, context={"retain_content": preserve_unparsed, "book_resolver": book_resolver})
            _supplement_study(study, record, markup, verse_number, text, book_resolver=book_resolver)
            _reconcile_title_content(semantics, study)
            study["verse"] = verse_number
            diagnostics.extend(study.get("diagnostics", []))
            if study.get("footnotes") or study.get("references") or study.get("content"):
                verse["_study"] = study
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
