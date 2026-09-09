"""LXX's extended canon must pass the actual extraction and output boundary."""

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


def test_lxx_preserves_extended_books_in_the_generated_tree(
    converted_modules, hashed_output,
):
    result = converted_modules["lxx"]
    books = result["version_data"]["books"]
    numbers = [book["nr"] for book in books]

    # Protect the extended canon without freezing the upstream book count or
    # preventing CrossWire from adding genuine Enoch content in a future module.
    assert any(number > 66 for number in numbers)
    assert len(numbers) == len(set(numbers))

    root = Path(result["output_dir"]) / "lxx"
    index = json.loads((root / "books.json").read_text(encoding="utf-8"))
    assert set(index) == {str(number) for number in numbers}
    for book in books:
        path = root / f'{book["nr"]}.json'
        standalone = json.loads(path.read_text(encoding="utf-8"))
        assert standalone["nr"] == book["nr"]
        assert standalone["name"] == book["name"]
        assert standalone["chapters"] == book["chapters"]
        assert path.with_suffix(".sha").is_file()
