"""Integration tests for token+span model on real OSIS modules.

Tests that modules with OSIS word-level markup (<w> tags) produce
correct tokens and spans. KJV and KJVA have word data; TR does not
despite being OSIS (it lacks <w> tags). Some verses in OSIS modules
(e.g. apocryphal books in KJVA) may not have word markup for individual
verses even though the module overall is detected as having word data.
"""

import json
import os

import pytest

pytestmark = pytest.mark.integration

# Modules known to have OSIS word-level markup (<w> tags with lemma/morph).
# TR (textusreceptus) is OSIS but does NOT have <w> tags.
OSIS_WORD_MODULES = {'kjv', 'kjva'}


class TestTokenSpanOnOsisModules:
    """Validate token+span data on modules known to have OSIS word markup.

    Note: Not every verse in an OSIS word-data module necessarily has
    tokens. Some books (e.g. apocryphal additions in KJVA) may lack
    <w> tags. The osis_random_verse_with_tokens fixture retries to
    find a verse that has tokens, skipping the test if none is found
    after several attempts.
    """

    @pytest.fixture
    def osis_module(self, per_module):
        """Skip non-OSIS modules."""
        abbr = per_module['abbreviation']
        if abbr not in OSIS_WORD_MODULES:
            pytest.skip(f"{abbr} is not an OSIS word-data module")
        return per_module

    @pytest.fixture
    def osis_random_verse_with_tokens(self, osis_module, integration_rng):
        """Pick a random verse from an OSIS module that has tokens.

        Some verses in OSIS modules lack <w> tags (e.g. apocryphal books).
        Retries up to 20 times to find a verse with tokens.
        """
        books = osis_module['version_data']['books']
        for _ in range(20):
            book = integration_rng.choice(books)
            chapter = integration_rng.choice(book['chapters'])
            verse = integration_rng.choice(chapter['verses'])
            if 'tokens' in verse:
                return verse
        pytest.skip(
            f"Could not find a verse with tokens in {osis_module['abbreviation']} "
            f"after 20 random attempts"
        )

    def test_module_has_some_verses_with_tokens(self, osis_module):
        """At least one verse in the module should have token data."""
        for book in osis_module['version_data']['books']:
            for chapter in book['chapters']:
                for verse in chapter['verses']:
                    if 'tokens' in verse:
                        return  # Found one, test passes
        pytest.fail(f"No verses with tokens found in {osis_module['abbreviation']}")

    def test_verse_has_tokens(self, osis_random_verse_with_tokens):
        assert isinstance(osis_random_verse_with_tokens['tokens'], list)
        assert len(osis_random_verse_with_tokens['tokens']) > 0

    def test_verse_has_spans(self, osis_random_verse_with_tokens):
        assert 'spans' in osis_random_verse_with_tokens, (
            f"OSIS verse missing spans: {osis_random_verse_with_tokens['name']}"
        )
        assert isinstance(osis_random_verse_with_tokens['spans'], list)

    def test_token_has_required_fields(self, osis_random_verse_with_tokens):
        for token in osis_random_verse_with_tokens['tokens']:
            assert 'token' in token, f"Token missing 'token': {token}"
            assert 'word_start' in token, f"Token missing 'word_start': {token}"
            assert 'word_end' in token, f"Token missing 'word_end': {token}"

    def test_token_word_positions_valid(self, osis_random_verse_with_tokens):
        """Each token's word_start should not exceed word_end."""
        for idx, token in enumerate(osis_random_verse_with_tokens['tokens']):
            ws = token['word_start']
            we = token['word_end']
            assert isinstance(ws, int) and isinstance(we, int)
            # Allow 0/0 (degenerate: token text not found in clean text).
            if ws == 0 and we == 0:
                continue
            assert ws <= we, (
                f"Token {idx} has word_start {ws} > word_end {we} "
                f"in {osis_random_verse_with_tokens['name']}"
            )

    def test_token_text_nonempty(self, osis_random_verse_with_tokens):
        for idx, token in enumerate(osis_random_verse_with_tokens['tokens']):
            assert isinstance(token['token'], str)
            assert len(token['token'].strip()) > 0, f"Empty token text at index {idx}"

    def test_span_has_required_fields(self, osis_random_verse_with_tokens):
        for span in osis_random_verse_with_tokens['spans']:
            assert 'tag' in span, f"Span missing 'tag': {span}"
            assert 'token_start' in span, f"Span missing 'token_start': {span}"
            assert 'token_end' in span, f"Span missing 'token_end': {span}"

    def test_span_indices_within_bounds(self, osis_random_verse_with_tokens):
        num_tokens = len(osis_random_verse_with_tokens['tokens'])
        for span in osis_random_verse_with_tokens['spans']:
            assert 0 <= span['token_start'] < num_tokens, (
                f"Span token_start {span['token_start']} out of bounds "
                f"(0-{num_tokens-1})"
            )
            assert 0 <= span['token_end'] < num_tokens, (
                f"Span token_end {span['token_end']} out of bounds "
                f"(0-{num_tokens-1})"
            )
            assert span['token_start'] <= span['token_end'], (
                f"Span token_start > token_end: "
                f"{span['token_start']} > {span['token_end']}"
            )

    def test_span_tag_is_string(self, osis_random_verse_with_tokens):
        for span in osis_random_verse_with_tokens['spans']:
            assert isinstance(span['tag'], str)
            assert len(span['tag']) > 0

    def test_span_attrs_is_dict_when_present(self, osis_random_verse_with_tokens):
        for span in osis_random_verse_with_tokens['spans']:
            if 'attrs' in span:
                assert isinstance(span['attrs'], dict)

    def test_tokens_have_lemma(self, osis_random_verse_with_tokens):
        """At least some tokens in a Strong's-tagged module should have lemma."""
        tokens_with_lemma = [
            t for t in osis_random_verse_with_tokens['tokens'] if 'lemma' in t
        ]
        assert len(tokens_with_lemma) > 0, (
            f"No tokens with lemma in {osis_random_verse_with_tokens['name']}"
        )


class TestNonOsisModulesNoTokens:
    """Verify that non-OSIS modules do NOT have tokens/spans."""

    @pytest.fixture
    def non_osis_module(self, per_module):
        """Skip OSIS modules."""
        abbr = per_module['abbreviation']
        if abbr in OSIS_WORD_MODULES:
            pytest.skip(f"{abbr} is an OSIS module")
        return per_module

    def test_verse_has_no_tokens(self, non_osis_module, integration_rng):
        books = non_osis_module['version_data']['books']
        book = integration_rng.choice(books)
        chapter = integration_rng.choice(book['chapters'])
        verse = integration_rng.choice(chapter['verses'])
        assert 'tokens' not in verse, (
            f"Non-OSIS verse should not have tokens: {verse['name']}"
        )


class TestTokenSpanOnChapterFiles:
    """Validate token+span data in the actual chapter-level JSON files on disk."""

    @pytest.fixture
    def osis_chapter_with_tokens(self, per_module, integration_rng):
        """Load a random chapter JSON file from an OSIS module.

        Retries to find a chapter where at least one verse has tokens.
        """
        abbr = per_module['abbreviation']
        if abbr not in OSIS_WORD_MODULES:
            pytest.skip(f"{abbr} is not an OSIS module")

        books = per_module['version_data']['books']
        for _ in range(10):
            book = integration_rng.choice(books)
            chapter = integration_rng.choice(book['chapters'])

            ch_path = os.path.join(
                per_module['output_dir'], abbr,
                str(book['nr']), f"{chapter['chapter']}.json",
            )
            with open(ch_path, 'r', encoding='utf-8') as f:
                ch_data = json.load(f)

            if any('tokens' in v for v in ch_data['verses']):
                return ch_data

        pytest.skip(f"Could not find a chapter with tokens in {abbr}")

    def test_chapter_file_verses_with_tokens_are_valid(self, osis_chapter_with_tokens):
        """Verses in chapter JSON files that have tokens should be well-formed."""
        for verse in osis_chapter_with_tokens['verses']:
            if 'tokens' not in verse:
                continue  # Some verses in OSIS modules may lack <w> tags
            assert 'spans' in verse, f"Verse has tokens but no spans: {verse['name']}"
            assert isinstance(verse['tokens'], list)
            assert isinstance(verse['spans'], list)
            assert len(verse['tokens']) > 0
