# SPDX-License-Identifier: GPL-2.0-only
"""Resolve SWORD book identities without imposing a closed list of books."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping


# Existing getBible addresses, using CrossWire's standard OSIS identities:
# https://wiki.crosswire.org/OSIS_Book_Abbreviations
# This compatibility registry is not an allow-list: other identities receive
# deterministic addresses from the reserved extension range below.
_OSIS_ORDER = (
    "Gen Exod Lev Num Deut Josh Judg Ruth 1Sam 2Sam 1Kgs 2Kgs 1Chr 2Chr "
    "Ezra Neh Esth Job Ps Prov Eccl Song Isa Jer Lam Ezek Dan Hos Joel Amos "
    "Obad Jonah Mic Nah Hab Zeph Hag Zech Mal Matt Mark Luke John Acts Rom "
    "1Cor 2Cor Gal Eph Phil Col 1Thess 2Thess 1Tim 2Tim Titus Phlm Heb Jas "
    "1Pet 2Pet 1John 2John 3John Jude Rev 1Esd 2Esd Tob Jdt AddEsth AddPs "
    "Wis Sir Bar PrAzar Sus Bel PrMan 1Macc 2Macc 3Macc 4Macc EpJer PssSol "
    "Odes 1En AddDan EpLao"
).split()
OSIS_NUMBERS = {name: number for number, name in enumerate(_OSIS_ORDER, 1)}
# Preserve the historical shared address, but retain its separate identity.
# A module containing both forms must never merge their records.
OSIS_NUMBERS["EsthGr"] = 71
_OSIS_NUMBERS = {name.casefold(): number for name, number in OSIS_NUMBERS.items()}
_NUMBER_OSIS = {number: name.casefold() for number, name in enumerate(_OSIS_ORDER, 1)}
_ROMAN_PREFIXES = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
EXTENSION_MIN = 1_000_000
EXTENSION_MAX = EXTENSION_MIN + (1 << 48) - 1
MAX_BOOK_NUMBER = EXTENSION_MAX


class BookIdentityError(ValueError):
    """Source identities or configured addresses would merge distinct books."""


@dataclass(frozen=True)
class BookIdentity:
    number: int
    key: str
    name: str


def _text_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def _name_key(value: str) -> str:
    """Match ordinal spelling variants only against explicit configured names."""

    normalized = _text_key(value)
    first, separator, rest = normalized.partition(" ")
    if separator and first in _ROMAN_PREFIXES:
        return f"{_ROMAN_PREFIXES[first]} {rest}"
    return normalized


def extension_number(key: str) -> int:
    """Versioned, order-independent identity address, exact in JSON/JavaScript."""

    digest = hashlib.sha256(("getbible-book/v1\0" + key).encode("utf-8")).digest()
    return EXTENSION_MIN + int.from_bytes(digest[:6], "big")


class BookResolver:
    """Resolve source names and OSIS IDs while preserving existing addresses.

    OSIS provides identity independently of localized display names. An explicit
    unknown OSIS ID stays distinct even if its display name matches a known book.
    Without OSIS, use an unambiguous configured name alias, then the source name,
    and only then an abbreviation. Local testament/book positions are not IDs.
    """

    def __init__(
        self,
        book_numbers: Mapping[str, int],
        book_names: Mapping[str, str],
    ) -> None:
        self._numbers = dict(book_numbers)
        self._names = dict(book_names)
        self._aliases: dict[str, int | None] = {}
        self._display_aliases: dict[str, str | None] = {}
        self._claims: dict[int, str] = {}
        self._cache: dict[tuple[str, str, str], BookIdentity] = {}
        for name, number in self._numbers.items():
            if type(number) is not int or not 0 < number < EXTENSION_MIN:
                raise BookIdentityError(
                    f"configured book number for {name!r} must be a positive "
                    f"integer below {EXTENSION_MIN}"
                )
            normalized = _name_key(name)
            if normalized in self._aliases and self._aliases[normalized] != number:
                self._aliases[normalized] = None
            else:
                self._aliases[normalized] = number
        for name, display_name in self._names.items():
            normalized = _name_key(name)
            if normalized in self._display_aliases and self._display_aliases[normalized] != display_name:
                self._display_aliases[normalized] = None
            else:
                self._display_aliases[normalized] = display_name

    def resolve(
        self,
        name: str,
        osis_reference: str = "",
        abbreviation: str = "",
    ) -> BookIdentity:
        # Preserve dotted work IDs such as Herm.Mand, removing only the numeric
        # chapter/verse suffix. Cache one item per book, not per verse reference.
        osis_book = re.split(r"\.(?=[0-9])", osis_reference.strip(), maxsplit=1)[0]
        cache_key = (name, osis_book, abbreviation)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        normalized_name = _name_key(name)
        number_from_name = self._numbers.get(name)
        if number_from_name is None:
            number_from_name = self._aliases.get(normalized_name)
        osis_key = _text_key(osis_book)
        if osis_key:
            key = f"osis:{osis_key}"
            number = _OSIS_NUMBERS.get(osis_key)
            if number is not None and number_from_name is not None and number != number_from_name:
                raise BookIdentityError(
                    f"SWORD book name {name!r} maps to {number_from_name}, "
                    f"but OSIS identity {osis_book!r} maps to {number}"
                )
            if number is None:
                number = extension_number(key)
        elif number_from_name is not None:
            number = number_from_name
            canonical = _NUMBER_OSIS.get(number)
            if number == 71 and normalized_name == "esther (greek)":
                canonical = "esthgr"
            key = f"osis:{canonical}" if canonical else f"configured:{number}"
        elif _text_key(name):
            key = f"name:{_text_key(name)}"
            number = extension_number(key)
        elif _text_key(abbreviation):
            abbreviation_key = _text_key(abbreviation)
            number = _OSIS_NUMBERS.get(abbreviation_key)
            if number is None:
                key = f"abbreviation:{abbreviation_key}"
                number = extension_number(key)
            else:
                key = f"osis:{abbreviation_key}"
        else:
            raise BookIdentityError(
                "SWORD book has no name, OSIS identity, or abbreviation"
            )

        claimed = self._claims.get(number)
        if number >= EXTENSION_MIN and claimed is not None and claimed != key:
            raise BookIdentityError(
                f"book identities {claimed!r} and {key!r} both map to "
                f"book number {number}; refusing to merge their content"
            )
        self._claims[number] = key
        display_name = (
            self._names.get(name)
            or self._display_aliases.get(normalized_name)
            or name.strip()
            or osis_book
            or abbreviation.strip()
        )
        identity = BookIdentity(number, key, display_name)
        # Bound cache memory independently of how many different source labels an
        # untrusted contract uses. Number claims remain for collision detection.
        if len(self._cache) < 4096:
            self._cache[cache_key] = identity
        return identity
