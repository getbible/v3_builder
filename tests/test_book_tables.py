"""The checked-in book tables: one stable number for every book a module can carry."""

from book_identity import MAX_BOOK_NUMBER, OSIS_NUMBERS

import collections
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONF = REPOSITORY_ROOT / "conf"


def _load(name):
    return json.loads((CONF / name).read_text(encoding="utf-8"))


def test_book_numbers_cover_one_to_eighty_nine_without_gaps():
    numbers = _load("bookNumbers.json")
    by_number = collections.defaultdict(list)
    for name, number in numbers.items():
        assert isinstance(number, int) and number >= 1, name
        by_number[number].append(name)
    assert sorted(by_number) == list(range(1, 90))
    # Only explicitly supported source-name aliases share fixed numbers.
    shared = {number: names for number, names in by_number.items() if len(names) > 1}
    assert shared == {
        71: ["Additions to Esther", "Esther (Greek)"],
        87: ["1 Enoch", "I Enoch"],
    }


def test_the_additional_books_continue_after_four_maccabees():
    numbers = _load("bookNumbers.json")
    assert numbers["IV Maccabees"] == 83
    assert {name: numbers[name] for name in (
        "Epistle of Jeremiah", "Psalms of Solomon", "Odes", "1 Enoch",
        "Additions to Daniel", "Laodiceans",
    )} == {
        "Epistle of Jeremiah": 84, "Psalms of Solomon": 85, "Odes": 86,
        "1 Enoch": 87, "Additions to Daniel": 88, "Laodiceans": 89,
    }


def test_the_complete_list_names_every_number():
    numbers = _load("bookNumbers.json")
    complete = _load("complete_list_of_books.json")
    assert sorted(int(key) for key in complete) == list(range(1, 90))
    assert sorted(int(key) for key in complete) == sorted(set(numbers.values()))
    for name in (
        "Epistle of Jeremiah", "Psalms of Solomon", "Odes", "1 Enoch",
        "Additions to Daniel", "Laodiceans",
    ):
        assert complete[str(numbers[name])] == name


def test_the_description_and_schemas_reach_the_last_book_number():
    numbers = _load("bookNumbers.json")
    assert max(numbers.values()) == 89
    last = MAX_BOOK_NUMBER
    for name in ("chapter", "book", "books-index", "chapters-index"):
        schema = json.dumps(json.loads((REPOSITORY_ROOT / "schema" / f"{name}.schema.json").read_text(encoding="utf-8")))
        assert f'"maximum": {last}' in schema, name
        assert '"maximum": 83' not in schema, name


def test_native_enoch_name_and_osis_identity_keep_existing_number():
    numbers = _load("bookNumbers.json")
    names = _load("bookNames.json")
    assert numbers["I Enoch"] == numbers["1 Enoch"] == 87
    assert names["I Enoch"] == "1 Enoch"
    assert OSIS_NUMBERS["1En"] == 87
