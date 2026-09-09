# SPDX-License-Identifier: GPL-2.0-only
"""Book identity regressions independent of native extraction fixtures."""

import json
from pathlib import Path

import pytest

import book_identity
from book_identity import (
    BookIdentityError,
    BookResolver,
    EXTENSION_MIN,
    MAX_BOOK_NUMBER,
    OSIS_NUMBERS,
)


@pytest.mark.parametrize(
    ("osis", "number"),
    [
        ("Gen", 1), ("Matt", 40), ("Rev", 66), ("AddPs", 72),
        ("EpJer", 84), ("PssSol", 85), ("Odes", 86),
        ("1En", 87), ("AddDan", 88), ("EpLao", 89),
    ],
)
def test_standard_source_identity_keeps_existing_address_without_english_name(
    osis, number
):
    identity = BookResolver({}, {}).resolve(
        "Localized source title", osis_reference=f"{osis}.1.1"
    )
    assert identity.number == number
    assert identity.name == "Localized source title"


def test_all_historical_addresses_have_an_osis_identity():
    assert set(OSIS_NUMBERS.values()) == set(range(1, 90))
    assert OSIS_NUMBERS["1En"] == 87


def test_native_enoch_spelling_resolves_from_the_checked_in_configuration():
    root = Path(__file__).resolve().parents[1]
    numbers = json.loads((root / "conf" / "bookNumbers.json").read_text())
    names = json.loads((root / "conf" / "bookNames.json").read_text())
    resolver = BookResolver(numbers, names)

    assert resolver.resolve("I Enoch", "1En.1.1").number == 87
    assert resolver.resolve("1 Enoch").number == 87
    assert resolver.resolve("I Enoch").key == resolver.resolve("1 Enoch", "1En").key


@pytest.mark.parametrize("spelling", ["1 Samuel", "I Samuel", " i  samuel "])
def test_ordinal_aliases_preserve_known_number_and_configured_display(spelling):
    identity = BookResolver(
        {"I Samuel": 9}, {"I Samuel": "1 Samuel"}
    ).resolve(spelling)
    assert identity.number == 9
    assert identity.name == "1 Samuel"


def test_ambiguous_ordinal_alias_is_not_guessed():
    resolver = BookResolver({"I Example": 90, "1 Example": 91}, {})
    assert resolver.resolve("I Example").number == 90
    assert resolver.resolve("1 Example").number == 91
    assert resolver.resolve(" i example ").number >= EXTENSION_MIN


def test_unknown_osis_gets_the_same_address_across_source_names_and_build_order():
    first = BookResolver({}, {})
    forward = {
        osis: first.resolve(name, f"{osis}.1.1").number
        for osis, name in [("Jub", "Jubilees"), ("1Meq", "I Meqabyan")]
    }
    second = BookResolver({}, {})
    backward = {
        osis: second.resolve(name, f"{osis}.12.34").number
        for osis, name in [("1Meq", "መቃብያን"), ("Jub", "Book of Jubilees")]
    }

    assert forward == backward
    assert len(set(forward.values())) == 2
    assert all(EXTENSION_MIN <= number <= MAX_BOOK_NUMBER for number in forward.values())
    assert MAX_BOOK_NUMBER < 2 ** 53


def test_unknown_explicit_osis_is_not_merged_into_a_known_display_name():
    resolver = BookResolver({"Genesis": 1}, {})
    known = resolver.resolve("Genesis", "Gen.1.1")
    unknown = resolver.resolve("Genesis", "FutureGenesis.1.1")

    assert known.number == 1
    assert unknown.number >= EXTENSION_MIN
    assert unknown.key != known.key
    assert unknown.name == "Genesis"


def test_conflicting_known_name_and_osis_are_rejected():
    with pytest.raises(BookIdentityError, match="Genesis.*maps to 1.*Exod.*maps to 2"):
        BookResolver({"Genesis": 1}, {}).resolve("Genesis", "Exod.1.1")


def test_without_osis_the_source_name_takes_priority_over_an_ambiguous_abbreviation():
    resolver = BookResolver({}, {})
    first = resolver.resolve("Future First Book", abbreviation="Fut")
    second = resolver.resolve("Future Second Book", abbreviation="Fut")

    assert first.number != second.number
    assert first.name == "Future First Book"
    assert first.key == "name:future first book"


def test_unknown_name_uses_unicode_canonical_normalization():
    resolver = BookResolver({}, {})
    assert resolver.resolve("Caf\u00e9 Book").number == resolver.resolve("Cafe\u0301 Book").number


def test_an_abbreviation_is_a_last_resort_identity_and_display_name():
    resolver = BookResolver({}, {})
    known = resolver.resolve("", abbreviation="1En")
    unknown = resolver.resolve("", abbreviation="Future")

    assert known.number == 87
    assert known.name == "1En"
    assert unknown.number >= EXTENSION_MIN
    assert unknown.name == "Future"


def test_source_osis_is_the_display_fallback_when_the_name_is_missing():
    identity = BookResolver({}, {}).resolve("", "Jub.1.1")
    assert identity.name == "Jub"


def test_dotted_work_ids_remain_distinct_and_cache_does_not_grow_per_verse():
    resolver = BookResolver({}, {})
    mandates = resolver.resolve("Mandates", "Herm.Mand.1.1")
    for chapter in range(1, 10):
        for verse in range(1, 100):
            assert resolver.resolve("Mandates", f"Herm.Mand.{chapter}.{verse}") is mandates
    visions = resolver.resolve("Visions", "Herm.Vis.1.1")

    assert mandates.key == "osis:herm.mand"
    assert visions.key == "osis:herm.vis"
    assert mandates.number != visions.number
    assert len(resolver._cache) == 2


def test_legacy_esther_addresses_keep_distinct_source_identities():
    resolver = BookResolver(
        {"Additions to Esther": 71, "Esther (Greek)": 71}, {}
    )
    additions = resolver.resolve("Additions to Esther", "AddEsth.1.1")
    greek = resolver.resolve("Esther (Greek)", "EsthGr.1.1")

    assert additions.number == greek.number == 71
    assert additions.key != greek.key


def test_a_hash_collision_cannot_silently_merge_book_content(monkeypatch):
    monkeypatch.setattr(book_identity, "extension_number", lambda _key: EXTENSION_MIN)
    resolver = BookResolver({}, {})
    resolver.resolve("First book", "FutureOne.1.1")

    with pytest.raises(BookIdentityError, match="refusing to merge"):
        resolver.resolve("Second book", "FutureTwo.1.1")


@pytest.mark.parametrize("invalid", [0, -1, True, "87", EXTENSION_MIN])
def test_configured_addresses_cannot_overlap_the_reserved_extension_range(invalid):
    with pytest.raises(BookIdentityError, match="positive integer below"):
        BookResolver({"Invalid": invalid}, {})


def test_a_record_without_any_book_identity_is_rejected():
    with pytest.raises(BookIdentityError, match="no name, OSIS identity, or abbreviation"):
        BookResolver({}, {}).resolve(" ", " ", " ")
