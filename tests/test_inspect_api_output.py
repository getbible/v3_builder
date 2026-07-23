import json
from pathlib import Path

import pytest

from scripts.inspect_api_output import (
    InspectionError,
    TARGET_BOOKS,
    TARGET_CHAPTERS,
    inspect_api,
    main,
    scan_output_sizes,
)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


@pytest.fixture
def generated_kjv(tmp_path):
    scripture = tmp_path / "api_scripture"
    hashes = tmp_path / "api"
    hashes.mkdir()
    (hashes / "checksum").write_text("1\tkjv\tdeadbeef\n", encoding="utf-8")

    books = []
    for target in TARGET_BOOKS:
        chapters = []
        for chapter_number in TARGET_CHAPTERS:
            chapter_path = (
                scripture / "kjv" / str(target.number) / f"{chapter_number}.json"
            )
            chapter = {
                "translation": "King James Version",
                "abbreviation": "kjv",
                "book_nr": target.number,
                "book_name": target.name,
                "chapter": chapter_number,
                "name": f"{target.name} {chapter_number}",
                "editorial": [
                    {
                        "order": 0,
                        "type": "heading",
                        "anchor": {"verse": 1, "edge": "before"},
                        "text": f"Section {chapter_number}",
                        "heading_type": "section",
                        "canonical": False,
                    },
                    {
                        "order": 1,
                        "type": "paragraph",
                        "start": 1,
                        "end": 2,
                    },
                ],
                "verses": [
                    {
                        "chapter": chapter_number,
                        "verse": 1,
                        "name": f"{target.name} {chapter_number}:1",
                        "text": "Representative text",
                        "paragraph": True,
                        "titles": [
                            {
                                "type": "section",
                                "text": f"Section {chapter_number}",
                            }
                        ],
                        "tokens": [
                            {
                                "token": "Representative text",
                                "word_start": 0,
                                "word_end": 1,
                            }
                        ],
                        "spans": [
                            {
                                "tag": "title",
                                "token_start": 0,
                                "token_end": 0,
                                "attrs": {"type": "section"},
                            }
                        ],
                    },
                    {
                        "chapter": chapter_number,
                        "verse": 2,
                        "name": f"{target.name} {chapter_number}:2",
                        "text": "More text",
                        "tokens": [
                            {"token": "More text", "word_start": 0, "word_end": 1}
                        ],
                        "spans": [],
                    },
                ],
            }
            _write_json(chapter_path, chapter)
            chapters.append({"chapter": chapter_number, "name": chapter["name"]})
        books.append({"nr": target.number, "name": target.name, "chapters": chapters})

    _write_json(
        scripture / "kjv.json",
        {
            "translation": "King James Version",
            "abbreviation": "kjv",
            "language": "English",
            "books": books,
        },
    )
    return scripture, hashes


def test_inspection_reports_all_requested_chapters_and_semantics(generated_kjv):
    scripture, hashes = generated_kjv

    result = inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)

    assert [book["book"] for book in result["books"]] == [
        "Psalms",
        "John",
        "Revelation",
    ]
    assert all(len(book["chapters"]) == 5 for book in result["books"])
    first = result["books"][0]["chapters"][0]
    assert first["verse_count"] == 2
    assert first["tokens"]["token_count"] == 2
    assert first["spans"]["tags"] == {"title": 1}
    assert first["editorial"]["heading_count"] == 1
    assert first["editorial"]["paragraph_count"] == 1
    assert first["editorial"]["entries"][1] == {
        "order": 1,
        "type": "paragraph",
        "start": 1,
        "end": 2,
    }
    assert first["paragraph_boundaries"]
    assert first["headings_or_titles"]
    assert result["size_report"]["file_count"] == 17


def test_cli_prints_bounded_inspection_to_stdout(generated_kjv, capsys):
    scripture, hashes = generated_kjv

    return_code = main(
        [
            "--scripture-root",
            str(scripture),
            "--output-root",
            str(scripture),
            "--output-root",
            str(hashes),
            "--size-limit-mib",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 0
    assert "PSALMS (book 19) — CHAPTERS 1–5" in captured.out
    assert "JOHN (book 43) — CHAPTERS 1–5" in captured.out
    assert "REVELATION (book 66) — CHAPTERS 1–5" in captured.out
    assert "Representative API verse records" in captured.out
    assert "INSPECTION PASSED" in captured.out
    assert captured.err == ""


def test_inspection_fails_when_a_requested_chapter_is_missing(generated_kjv):
    scripture, hashes = generated_kjv
    (scripture / "kjv" / "43" / "5.json").unlink()

    with pytest.raises(InspectionError, match="required API file is missing"):
        inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)


@pytest.mark.parametrize("forbidden_field", ["source", "source_contract"])
def test_inspection_fails_when_translation_retains_source_envelope(
    generated_kjv, forbidden_field
):
    scripture, hashes = generated_kjv
    translation_path = scripture / "kjv.json"
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    translation[forbidden_field] = {"transient": True}
    _write_json(translation_path, translation)

    with pytest.raises(InspectionError, match="forbidden transient field"):
        inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)


def test_inspection_fails_when_verse_retains_nested_source_envelope(generated_kjv):
    scripture, hashes = generated_kjv
    chapter_path = scripture / "kjv" / "19" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter["verses"][0]["source"] = {"raw": "must not ship"}
    _write_json(chapter_path, chapter)

    with pytest.raises(InspectionError, match="forbidden transient field"):
        inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)


def test_inspection_fails_when_editorial_paragraphs_do_not_cover_chapter(
    generated_kjv,
):
    scripture, hashes = generated_kjv
    chapter_path = scripture / "kjv" / "19" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter["editorial"][1]["end"] = 1
    _write_json(chapter_path, chapter)

    with pytest.raises(InspectionError, match="final emitted verse"):
        inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)


def test_inspection_fails_when_verse_text_starts_with_line_ending(generated_kjv):
    scripture, hashes = generated_kjv
    chapter_path = scripture / "kjv" / "19" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter["verses"][0]["text"] = "\n" + chapter["verses"][0]["text"]
    _write_json(chapter_path, chapter)

    with pytest.raises(InspectionError, match="text begins with a line ending"):
        inspect_api(scripture, [scripture, hashes], size_limit_bytes=1024 * 1024)


def test_size_gate_fails_at_or_above_the_limit(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "too-large.json").write_bytes(b"x" * 100)

    with pytest.raises(InspectionError, match="size gate failed"):
        scan_output_sizes([output], size_limit_bytes=100)


def test_size_report_is_bounded_to_largest_files(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    for index in range(5):
        (output / f"{index}.json").write_bytes(b"x" * (index + 1))

    report = scan_output_sizes([output], size_limit_bytes=100, largest_file_count=2)

    assert report["file_count"] == 5
    assert [item["bytes"] for item in report["largest_files"]] == [5, 4]
