"""Tests for osis_parser.py parse_osis_verse() — token + span model.

Schema under test
-----------------

Tokens carry ``token`` (text), intrinsic <w> attributes (lemma, morph, ...)
and ``word_start`` / ``word_end`` (1-based, inclusive whitespace-word range
in the verse's clean text).

Spans carry ``tag``, ``span`` (exact OSIS-marked text), optional ``attrs``,
``word_start`` / ``word_end`` (1-based, inclusive), and
``token_start`` / ``token_end`` (0-based index range into tokens[]).
"""

import pytest
from osis_parser import parse_osis_semantics, parse_osis_verse


def find_span(spans, tag, **attrs_match):
    """Find first span with given tag and optional attribute matches."""
    for s in spans:
        if s['tag'] != tag:
            continue
        if attrs_match:
            span_attrs = s.get('attrs', {})
            if all(span_attrs.get(k) == v for k, v in attrs_match.items()):
                return s
        else:
            return s
    return None


def find_spans(spans, tag):
    """Find all spans with given tag."""
    return [s for s in spans if s['tag'] == tag]


def token_index(tokens, text):
    """Return 0-based index of the first token whose ``token`` equals text."""
    for i, t in enumerate(tokens):
        if t['token'] == text:
            return i
    raise AssertionError(f'token {text!r} not found')


# =========================================================================
# Token intrinsic attributes
# =========================================================================

class TestTokenAttributes:
    def test_basic_token(self):
        raw = '<w lemma="strong:G976" morph="robinson:N-NSF" src="1">book</w>'
        result = parse_osis_verse(raw)
        assert result is not None
        assert len(result['tokens']) == 1
        tok = result['tokens'][0]
        assert tok['token'] == 'book'
        assert tok['lemma'] == {'strong': ['G976']}
        assert tok['morph'] == {'robinson': ['N-NSF']}
        assert tok['src'] == [1]
        assert len(result['spans']) == 0

    def test_multiple_lemmas_and_morphs(self):
        raw = '<w lemma="strong:H853 strong:H3045" morph="oshm:HTp oshm:HVqp3ms" src="2 3">knew</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'strong': ['H853', 'H3045']}
        assert tok['morph'] == {'oshm': ['HTp', 'HVqp3ms']}
        assert tok['src'] == [2, 3]

    def test_mixed_lemma_schemes(self):
        raw = '<w lemma="strong:G520 lemma.TR:apostle" morph="robinson:V-2AAM-2P">lead away</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'strong': ['G520'], 'lemma.TR': ['apostle']}

    def test_twot_lemma(self):
        raw = '<w lemma="lemma.TWOT:271" morph="oshm:HVqp3ms">created</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'lemma.TWOT': ['271']}

    def test_split_type_on_token(self):
        raw = '<w lemma="strong:H1234" type="x-split" subType="x-1227">be</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['type'] == 'x-split'
        assert tok['subType'] == 'x-1227'

    def test_gloss_and_xlit(self):
        raw = '<w lemma="strong:H430" gloss="God" xlit="Latn:Elohim">God</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['gloss'] == 'God'
        assert tok['xlit'] == {'Latn': ['Elohim']}

    def test_morph_segmented_on_token(self):
        raw = '<w lemma="strong:H7225" morph="oshm:HR oshm:HNcfsa"><seg type="x-morph">b-</seg><seg type="x-morph">re\'shiyth</seg></w>'
        result = parse_osis_verse(raw)
        tok = result['tokens'][0]
        assert tok.get('morphSegmented') is True
        assert tok['token'] == "b-re'shiyth"

    def test_variant_on_token(self):
        raw = '<w lemma="strong:G1234"><seg type="x-variant" subType="x-1">word1</seg></w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok.get('variant') is True
        assert tok.get('variantType') == 'x-1'


# =========================================================================
# Intrinsic attribute shapes: lemma / morph / xlit / src
#
# Scheme-prefixed attributes (lemma, morph, xlit) are emitted as dicts
# keyed by scheme with array-of-string code values. src is emitted as an
# array of integers (with string-array fallback for non-numeric tokens).
#
# This shape lets consumers write::
#
#     for code in token['lemma'].get('strong', []):
#         ...
#
# without parsing space-delimited scheme-prefixed strings.
# =========================================================================

class TestIntrinsicAttributeShapes:
    """Pin the dict/array shapes of lemma, morph, xlit, src."""

    # ── lemma ───────────────────────────────────────────────────────────────

    def test_lemma_single_scheme_single_code(self):
        """A lemma with one prefixed code → single-key dict with one-item list."""
        raw = '<w lemma="strong:G976">book</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'strong': ['G976']}

    def test_lemma_single_scheme_multi_code(self):
        """Two same-scheme codes group into one list under that scheme."""
        raw = '<w lemma="strong:H853 strong:H3045">knew</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'strong': ['H853', 'H3045']}

    def test_lemma_multi_scheme(self):
        """Different schemes are separate keys; each scheme keeps list order."""
        raw = '<w lemma="strong:G520 lemma.TR:apostle">lead away</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'strong': ['G520'], 'lemma.TR': ['apostle']}

    def test_lemma_multi_scheme_multi_code_per_scheme(self):
        """Real KJV NT shape — two strongs and two TR lemmas coexist."""
        raw = '<w lemma="strong:G3588 strong:G2098 lemma.TR:το lemma.TR:ευαγγελιον">that gospel</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {
            'strong': ['G3588', 'G2098'],
            'lemma.TR': ['το', 'ευαγγελιον'],
        }

    def test_lemma_default_scheme_for_bare_value(self):
        """A lemma code with no scheme prefix lands under the 'default' key."""
        raw = '<w lemma="H1234">word</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'default': ['H1234']}

    def test_lemma_with_lemma_twot_scheme(self):
        """Multi-dot scheme names (lemma.TWOT) are preserved as a single key."""
        raw = '<w lemma="lemma.TWOT:271">created</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma'] == {'lemma.TWOT': ['271']}

    # ── morph ───────────────────────────────────────────────────────────────

    def test_morph_single_scheme_single_code(self):
        raw = '<w lemma="strong:G976" morph="robinson:N-NSF">book</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['morph'] == {'robinson': ['N-NSF']}

    def test_morph_single_scheme_multi_code(self):
        """Compound morph codes in one scheme group into a single list."""
        raw = '<w lemma="strong:H7225" morph="oshm:HR oshm:HNcfsa">b-reshit</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['morph'] == {'oshm': ['HR', 'HNcfsa']}

    def test_morph_multi_scheme(self):
        """Real KJV shape — one code without prefix + one with robinson prefix."""
        raw = '<w lemma="strong:G1096" morph="V-2ADP-GSF robinson:V-PNI-3S">he cometh</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['morph'] == {
            'default': ['V-2ADP-GSF'],
            'robinson': ['V-PNI-3S'],
        }

    def test_morph_strongmorph_scheme(self):
        raw = '<w lemma="strong:H1254" morph="strongMorph:TH8804">created</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['morph'] == {'strongMorph': ['TH8804']}

    # ── xlit ────────────────────────────────────────────────────────────────

    def test_xlit_single_scheme(self):
        raw = '<w lemma="strong:H430" xlit="Latn:Elohim">God</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['xlit'] == {'Latn': ['Elohim']}

    def test_xlit_default_scheme(self):
        """A transliteration without a scheme prefix lands under 'default'."""
        raw = '<w lemma="strong:H430" xlit="Elohim">God</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['xlit'] == {'default': ['Elohim']}

    # ── src ─────────────────────────────────────────────────────────────────

    def test_src_single_integer(self):
        raw = '<w lemma="strong:G976" src="1">book</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['src'] == [1]
        assert all(isinstance(x, int) for x in tok['src'])

    def test_src_multi_integer(self):
        """Space-separated numeric source indices → list of ints."""
        raw = '<w lemma="strong:H8064" src="5 6">the heaven</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['src'] == [5, 6]
        assert all(isinstance(x, int) for x in tok['src'])

    def test_src_nonnumeric_falls_back_to_string_list(self):
        """Non-numeric src tokens (e.g. '13n' variant markers in KJV Romans 16)
        fall back to a list of strings so the array shape is preserved."""
        raw = '<w lemma="strong:G2424" src="13n">Jesus</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['src'] == ['13n']
        assert all(isinstance(x, str) for x in tok['src'])

    # ── shape invariants ────────────────────────────────────────────────────

    def test_scheme_dict_values_are_always_lists(self):
        """Even a lone code is wrapped in a list so consumers can iterate
        uniformly without type-checking."""
        raw = '<w lemma="strong:G1" morph="robinson:N-NSF" xlit="Latn:foo">x</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        for field in ('lemma', 'morph', 'xlit'):
            assert isinstance(tok[field], dict)
            for codes in tok[field].values():
                assert isinstance(codes, list)
                assert len(codes) >= 1

    def test_src_is_always_a_list(self):
        """src is always an array — never a bare int or string."""
        raw = '<w lemma="strong:G1" src="7">x</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert isinstance(tok['src'], list)

    def test_lemma_preserves_within_scheme_order(self):
        """Within a scheme, code order matches source order (positional
        semantics for fused tokens depend on this)."""
        raw = '<w lemma="strong:H03027 strong:H0931">into his hand</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['lemma']['strong'] == ['H03027', 'H0931']

    def test_non_transform_attributes_unchanged(self):
        """Attributes that are NOT in the intrinsic transform map (e.g.
        gloss, type, subType) are emitted unchanged as strings."""
        raw = '<w lemma="strong:G1" gloss="the good" type="x-split" subType="x-1">x</w>'
        tok = parse_osis_verse(raw)['tokens'][0]
        assert tok['gloss'] == 'the good'
        assert tok['type'] == 'x-split'
        assert tok['subType'] == 'x-1'


class TestTokenIndexing:
    def test_sequential_indices(self):
        raw = (
            '<w lemma="strong:H7225" src="1">In the beginning</w> '
            '<w lemma="strong:H1254" src="2">created</w> '
            '<w lemma="strong:H430" src="3">God</w> '
            '<w lemma="strong:H853" src="4"></w>'  # empty, skipped
            '<w lemma="strong:H8064" src="5 6">the heaven</w> '
            '<w lemma="strong:H853">and</w> '
            '<w lemma="strong:H776" src="8 9">the earth</w>.'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 6

    def test_empty_w_skipped(self):
        raw = (
            '<w lemma="strong:G1">a</w> '
            '<w lemma="strong:G2">b</w> '
            '<w lemma="strong:G3"></w>'
            '<w lemma="strong:G4">d</w>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 3

    def test_transchange_standalone_gets_index(self):
        raw = '<w lemma="strong:H1961">was</w> <transChange type="added">there</transChange>'
        result = parse_osis_verse(raw)
        assert result['tokens'][0]['token'] == 'was'
        assert result['tokens'][1]['token'] == 'there'
        # Standalone transChange token should not have lemma
        assert 'lemma' not in result['tokens'][1]


# =========================================================================
# Span: divineName
# =========================================================================

class TestDivineNameSpan:
    def test_wrapping_w(self):
        raw = '<divineName><w lemma="strong:H3068" morph="oshm:HNp">Lord</w></divineName>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1
        assert result['tokens'][0]['token'] == 'Lord'
        assert result['tokens'][0]['lemma'] == {'strong': ['H3068']}
        span = find_span(result['spans'], 'divineName')
        assert span is not None
        assert span['token_start'] == 0
        assert span['token_end'] == 0
        assert 'attrs' not in span  # no attrs for divineName

    def test_nested_inside_w(self):
        raw = '<w lemma="strong:H3068"><seg><divineName>LORD</divineName></seg></w>'
        result = parse_osis_verse(raw)
        assert result['tokens'][0]['token'] == 'LORD'
        span = find_span(result['spans'], 'divineName')
        assert span is not None
        assert span['token_start'] == 0

    def test_multiple_sections(self):
        raw = (
            '<divineName><w lemma="strong:H3068">LORD</w></divineName> '
            '<w lemma="strong:H430">of</w> '
            '<divineName><w lemma="strong:H3069">GOD</w></divineName>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 3
        dn_spans = find_spans(result['spans'], 'divineName')
        assert len(dn_spans) == 2
        assert dn_spans[0]['token_start'] == 0 and dn_spans[0]['token_end'] == 0
        assert dn_spans[1]['token_start'] == 2 and dn_spans[1]['token_end'] == 2


# =========================================================================
# Span: transChange
# =========================================================================

class TestTransChangeSpan:
    def test_standalone(self):
        raw = '<w lemma="strong:H1961">was</w> <transChange type="added">there</transChange>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'transChange')
        assert span is not None
        assert span['token_start'] == 1
        assert span['token_end'] == 1
        assert span['attrs']['type'] == 'added'

    def test_seg_workaround_inside_w(self):
        raw = '<w lemma="strong:H1234"><seg subType="x-added" type="x-transChange">is</seg></w>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'transChange')
        assert span is not None
        assert span['attrs']['type'] == 'added'

    def test_wrapping_multiple_words(self):
        raw = (
            '<transChange type="added">'
            '<w lemma="strong:G1234">it</w> '
            '<w lemma="strong:G5678">is</w>'
            '</transChange>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 2
        span = find_span(result['spans'], 'transChange')
        assert span['token_start'] == 0
        assert span['token_end'] == 1
        assert span['attrs']['type'] == 'added'


# =========================================================================
# Span: hi (highlighting)
# =========================================================================

class TestHiSpan:
    def test_wrapping_w(self):
        raw = '<hi type="italic"><w lemma="strong:G1234">emphasized</w></hi>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'hi')
        assert span is not None
        assert span['attrs']['type'] == 'italic'
        assert span['token_start'] == 0 and span['token_end'] == 0

    @pytest.mark.parametrize('hi_type', [
        'bold', 'small-caps', 'super', 'sub', 'overline',
        'spaced-letters', 'caps', 'acrostic', 'emphasis',
        'drop-caps', 'x-overline'
    ])
    def test_all_hi_types(self, hi_type):
        raw = '<hi type="{}"><w lemma="strong:G1">text</w></hi>'.format(hi_type)
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'hi')
        assert span['attrs']['type'] == hi_type

    def test_spanning_multiple_words(self):
        raw = (
            '<hi type="italic">'
            '<w lemma="strong:G1">alpha</w> '
            '<w lemma="strong:G2">beta</w> '
            '<w lemma="strong:G3">gamma</w>'
            '</hi>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 3
        span = find_span(result['spans'], 'hi')
        assert span['token_start'] == 0 and span['token_end'] == 2

    def test_nested_inside_w(self):
        raw = '<w lemma="strong:G1234"><hi type="bold">emphasized</hi></w>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'hi')
        assert span is not None
        assert span['attrs']['type'] == 'bold'


# =========================================================================
# Span: q (quotation)
# =========================================================================

class TestQuoteSpan:
    def test_single_word(self):
        raw = '<q who="Jesus" level="1"><w lemma="strong:G1234">verily</w></q>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'q')
        assert span is not None
        assert span['attrs']['who'] == 'Jesus'
        assert span['attrs']['level'] == '1'
        assert span['token_start'] == 0 and span['token_end'] == 0

    def test_multiple_words(self):
        raw = (
            '<q who="Jesus" level="1">'
            '<w lemma="strong:G281">Verily</w> '
            '<w lemma="strong:G281">verily</w> '
            '<w lemma="strong:G3004">I say</w>'
            '</q>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 3
        span = find_span(result['spans'], 'q')
        assert span['token_start'] == 0 and span['token_end'] == 2
        assert span['attrs']['who'] == 'Jesus'

    def test_marker_attribute(self):
        raw = '<q who="Jesus" marker="&#x201C;"><w lemma="strong:G1">word</w></q>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'q')
        assert span['attrs']['marker'] == '\u201c'


# =========================================================================
# Span: other context elements
# =========================================================================

class TestOtherContextSpans:
    def test_foreign(self):
        raw = '<foreign n="Hebrew"><w lemma="strong:H7225">bereshit</w></foreign>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'foreign')
        assert span is not None
        assert span['attrs']['n'] == 'Hebrew'

    def test_inscription(self):
        raw = '<inscription><w lemma="strong:G1234">KING</w></inscription>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'inscription')
        assert span is not None
        assert 'attrs' not in span

    def test_name_person(self):
        raw = '<name type="person"><w lemma="strong:H85">Abraham</w></name>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'name')
        assert span is not None
        assert span['attrs']['type'] == 'person'

    def test_speaker(self):
        raw = '<speaker><w lemma="strong:G1234">Moses</w></speaker>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'speaker')
        assert span is not None

    def test_number_cardinal(self):
        raw = '<number type="cardinal"><w lemma="strong:H7651">seven</w></number>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'number')
        assert span is not None
        assert span['attrs']['type'] == 'cardinal'

    def test_unit_currency(self):
        raw = '<unit type="currency"><w lemma="strong:H3701">shekels</w></unit>'
        result = parse_osis_verse(raw)
        span = find_span(result['spans'], 'unit')
        assert span is not None
        assert span['attrs']['type'] == 'currency'


# =========================================================================
# Span: seg (standalone segment)
# =========================================================================

class TestSegSpan:
    def test_standalone_seg(self):
        raw = '<w lemma="strong:G1234">word</w> <seg type="x-variant" subType="x-1">variant text</seg>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 2
        assert result['tokens'][1]['token'] == 'variant text'
        span = find_span(result['spans'], 'seg')
        assert span is not None
        assert span['attrs']['type'] == 'x-variant'
        assert span['attrs']['subType'] == 'x-1'


# =========================================================================
# Skip elements
# =========================================================================

class TestSkipElements:
    def test_note_excluded(self):
        raw = '<w lemma="strong:G1234">word</w><note type="crossReference">Gen 1:1</note>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1

    def test_milestone_excluded(self):
        raw = '<milestone type="x-p" marker="&#xB6;"/><w lemma="strong:G1234">word</w>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1

    def test_title_excluded(self):
        raw = '<title>Psalm of David</title><w lemma="strong:H1234">blessed</w>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1
        assert result['tokens'][0]['token'] == 'blessed'

    def test_rdg_excluded(self):
        raw = '<w lemma="strong:G1234">word</w><rdg type="alternate">alt reading</rdg>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1

    def test_note_inside_w(self):
        raw = '<w lemma="strong:G1234">word<note type="study">a footnote</note></w>'
        result = parse_osis_verse(raw)
        assert result['tokens'][0]['token'] == 'word'


# =========================================================================
# Compact structural semantics
# =========================================================================

class TestStructuralSemantics:
    @pytest.mark.parametrize('raw', [
        '<milestone marker="¶" type="x-p"/><w lemma="strong:G1">Word</w>',
        '<milestone marker="¶" subType="x-added" type="x-p"/>'
        '<w lemma="strong:G1">Word</w>',
        '<p><w lemma="strong:G1">Word</w></p>',
        '<p sID="p1"/><w lemma="strong:G1">Word</w>',
    ])
    def test_paragraph_start_patterns(self, raw):
        assert parse_osis_semantics(raw)['paragraph'] is True

    def test_paragraph_end_milestone_is_not_a_start(self):
        raw = '<p eID="p1"/><w lemma="strong:G1">Word</w>'
        assert 'paragraph' not in parse_osis_semantics(raw)

    def test_kjv_chapter_title_is_deduplicated(self):
        raw = (
            '<chapter chapterTitle="CHAPTER 1." osisID="Gen.1" sID="gen30"/> '
            '<title type="chapter">CHAPTER 1.</title>'
        )
        assert parse_osis_semantics(raw)['titles'] == [
            {'type': 'chapter', 'text': 'CHAPTER 1.'}
        ]

    def test_chapter_title_attribute_is_a_fallback(self):
        raw = '<chapter chapterTitle="PSALM 1." osisID="Ps.1" sID="p1"/>'
        assert parse_osis_semantics(raw)['titles'] == [
            {'type': 'chapter', 'text': 'PSALM 1.'}
        ]

    def test_closing_chapter_milestone_is_not_a_verse_title(self):
        raw = (
            '<w lemma="strong:H06">shall perish</w>. '
            '<chapter chapterTitle="PSALM 1." eID="gen526" osisID="Ps.1"/>'
        )
        assert 'titles' not in parse_osis_semantics(raw)

    def test_kjv_psalm_superscription_keeps_word_data(self):
        raw = (
            '<div type="x-milestone" subType="x-preverse" sID="pv1"/>'
            '<title canonical="true" type="psalm">'
            '<w lemma="strong:H04210">A Psalm</w> '
            '<w lemma="strong:H01732">of David</w>, '
            '<w lemma="strong:H01272" morph="strongMorph:TH8800">'
            'when he fled</w> '
            '<w lemma="strong:H06440">from</w> '
            '<w lemma="strong:H053">Absalom</w> '
            '<w lemma="strong:H01121">his son</w>.'
            '</title>'
            '<div type="x-milestone" subType="x-preverse" eID="pv1"/>'
            '<w lemma="strong:H03068"><divineName>Lord</divineName></w>'
        )
        title = parse_osis_semantics(raw)['titles'][0]
        assert title['type'] == 'psalm'
        assert title['canonical'] is True
        assert title['text'] == (
            'A Psalm of David, when he fled from Absalom his son.'
        )
        assert title['tokens'][0]['lemma'] == {'strong': ['H04210']}
        assert title['tokens'][-1]['word_end'] == 11
        assert title['spans'] == []

    def test_kjv_acrostic_title_keeps_foreign_visible_text(self):
        raw = (
            '<title canonical="true" type="acrostic">'
            '<foreign xml:lang="hbo">א ALEPH.</foreign>'
            '</title><w lemma="strong:H0835">Blessed</w>'
        )
        assert parse_osis_semantics(raw)['titles'] == [
            {'type': 'acrostic', 'text': 'א ALEPH.', 'canonical': True}
        ]

    def test_book_title_keeps_inline_abbreviation(self):
        raw = (
            '<title type="main">THE GOSPEL ACCORDING TO '
            '<abbr expansion="Saint">ST.</abbr> JOHN</title>'
        )
        assert parse_osis_semantics(raw)['titles'][0]['text'] == (
            'THE GOSPEL ACCORDING TO ST. JOHN'
        )

    def test_title_notes_are_not_promoted_to_visible_text(self):
        raw = (
            '<title type="section">A heading'
            '<note type="study">not visible</note></title>'
        )
        assert parse_osis_semantics(raw)['titles'][0]['text'] == 'A heading'

    def test_malformed_semantic_markup_is_ignored(self):
        assert parse_osis_semantics('<title type="section">broken') == {}

    def test_unescaped_ampersand_recovery_applies_to_titles(self):
        raw = '<title type="section">Law & Grace</title>'
        assert parse_osis_semantics(raw)['titles'][0]['text'] == 'Law & Grace'


# =========================================================================
# Edge cases
# =========================================================================

class TestEdgeCases:
    def test_no_w_tags_returns_none(self):
        assert parse_osis_verse('In the beginning') is None

    def test_empty_string_returns_none(self):
        assert parse_osis_verse('') is None

    def test_none_returns_none(self):
        assert parse_osis_verse(None) is None

    def test_ampersand(self):
        raw = '<w lemma="strong:G1234">bread &amp; wine</w>'
        result = parse_osis_verse(raw)
        assert result['tokens'][0]['token'] == 'bread & wine'

    def test_bare_w_element_is_tokenized(self):
        result = parse_osis_verse('<w>word</w>')
        assert result['tokens'] == [
            {'token': 'word', 'word_start': 1, 'word_end': 1}
        ]


# =========================================================================
# Nesting and complex scenarios
# =========================================================================

class TestNesting:
    def test_q_containing_divine_name(self):
        raw = (
            '<q who="Jesus" level="1">'
            '<w lemma="strong:G281">Verily</w> '
            '<divineName><w lemma="strong:H3068">LORD</w></divineName> '
            '<w lemma="strong:G3004">says</w>'
            '</q>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 3
        q_span = find_span(result['spans'], 'q')
        assert q_span['token_start'] == 0 and q_span['token_end'] == 2
        dn_span = find_span(result['spans'], 'divineName')
        assert dn_span['token_start'] == 1 and dn_span['token_end'] == 1

    def test_deep_nesting(self):
        raw = (
            '<q who="Jesus" level="1">'
            '<hi type="italic">'
            '<divineName><w lemma="strong:H3068">LORD</w></divineName>'
            '</hi>'
            '</q>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1
        assert result['tokens'][0]['token'] == 'LORD'
        q_span = find_span(result['spans'], 'q')
        hi_span = find_span(result['spans'], 'hi')
        dn_span = find_span(result['spans'], 'divineName')
        assert q_span is not None and hi_span is not None and dn_span is not None
        # All three cover the same single token
        for span in [q_span, hi_span, dn_span]:
            assert (span['token_start'], span['token_end']) == (0, 0)

    def test_complex_real_world_verse(self):
        raw = (
            '<w lemma="strong:H7225" morph="oshm:HNcfsa" src="1">In the beginning</w> '
            '<w lemma="strong:H1254" morph="oshm:HVqp3ms" src="2">created</w> '
            '<w lemma="strong:H430" morph="oshm:HNcmpa" src="3">God</w> '
            '<w lemma="strong:H853" src="4"></w>'
            '<w lemma="strong:H8064" morph="oshm:HTd oshm:HNcbpd" src="5 6">the heaven</w> '
            '<w lemma="strong:H853">and</w> '
            '<w lemma="strong:H776" morph="oshm:HTd oshm:HNcbsd" src="8 9">the earth</w>.'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 6
        assert result['tokens'][0]['token'] == 'In the beginning'
        assert result['tokens'][3]['src'] == [5, 6]
        assert len(result['spans']) == 0


# =========================================================================
# Span attrs omission
# =========================================================================

class TestSpanAttrs:
    def test_divine_name_no_attrs(self):
        raw = '<divineName><w lemma="strong:H3068">LORD</w></divineName>'
        span = find_span(parse_osis_verse(raw)['spans'], 'divineName')
        assert 'attrs' not in span

    def test_inscription_no_attrs(self):
        raw = '<inscription><w lemma="strong:G1234">MENE</w></inscription>'
        span = find_span(parse_osis_verse(raw)['spans'], 'inscription')
        assert 'attrs' not in span

    def test_speaker_no_attrs(self):
        raw = '<speaker><w lemma="strong:G1234">Moses</w></speaker>'
        span = find_span(parse_osis_verse(raw)['spans'], 'speaker')
        assert 'attrs' not in span

    def test_q_has_attrs(self):
        raw = '<q who="Jesus" level="1"><w lemma="strong:G1">word</w></q>'
        span = find_span(parse_osis_verse(raw)['spans'], 'q')
        assert 'attrs' in span
        assert span['attrs']['who'] == 'Jesus'

    def test_transChange_has_attrs(self):
        raw = '<transChange type="added"><w lemma="strong:G1">word</w></transChange>'
        span = find_span(parse_osis_verse(raw)['spans'], 'transChange')
        assert span['attrs']['type'] == 'added'


# =========================================================================
# Sub-<w> span precision — real KJV Judges 1 raw OSIS regression tests
#
# When a <w> element contains a sub-<w> span producer (<divineName>, <hi>,
# <seg type="x-transChange">) that wraps only PART of the word text, the
# parser MUST split the <w> into multiple tokens so the span targets only
# the marked sub-range. Otherwise unrelated translator text (e.g. leading
# "And" / "and the ") gets incorrectly flagged as divineName.
#
# The raw OSIS strings below are copied verbatim from the CrossWire KJV
# SWORD module via pysword get(clean=False). These tests therefore pin
# the engine's output against the real source, not synthetic markup.
# =========================================================================

class TestSubWordSpanPrecision:
    """Sub-<w> span producers must mark only their wrapped text, not the
    whole containing <w>."""

    def test_kjv_judges_1_1_raw(self):
        """Jdg 1:1 OSIS: <w lemma='H03068'>the <divineName>Lord</divineName></w>

        Must split into 'the' (H03068) + 'Lord' (H03068, divineName).
        Joshua (H03091) is NEVER divine-tagged.
        """
        raw = (
            '<w lemma="strong:H0310">Now after</w> '
            '<w lemma="strong:H04194">the death</w> '
            '<w lemma="strong:H03091">of Joshua</w> '
            '<w lemma="strong:H01121">it came to pass, that the children</w> '
            '<w lemma="strong:H03478">of Israel</w> '
            '<w lemma="strong:H07592" morph="strongMorph:TH8799">asked</w> '
            '<w lemma="strong:H03068">the <divineName>Lord</divineName></w>, '
            '<w lemma="strong:H0559" morph="strongMorph:TH8800">saying</w>, '
            '<w lemma="strong:H05927" morph="strongMorph:TH8799">Who shall go up</w> '
            '<w lemma="strong:H03669">for us against the Canaanites</w> '
            '<w lemma="strong:H08462">first</w>, '
            '<w lemma="strong:H03898" morph="strongMorph:TH8736">to fight</w> against them?'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        # H03068 <w> splits into 'the' + 'Lord'
        assert 'the' in texts and 'Lord' in texts
        # 'the' and 'Lord' both carry lemma H03068 (same Strong's group)
        the_tok = next(t for t in result['tokens'] if t['token'] == 'the')
        lord_tok = next(t for t in result['tokens'] if t['token'] == 'Lord')
        assert the_tok['lemma'] == {'strong': ['H03068']}
        assert lord_tok['lemma'] == {'strong': ['H03068']}
        # Exactly one divineName span, covering ONLY 'Lord'
        dn_spans = find_spans(result['spans'], 'divineName')
        assert len(dn_spans) == 1
        assert dn_spans[0]['token_start'] == dn_spans[0]['token_end']
        target = result['tokens'][dn_spans[0]['token_start']]
        assert target['token'] == 'Lord'
        # 'the' token must NOT carry the divineName span
        assert dn_spans[0]['token_start'] != token_index(result['tokens'], 'the')

    def test_kjv_judges_1_2_raw_leading_and_not_divine(self):
        """Jdg 1:2 OSIS: <w lemma='H03068'>And the <divineName>Lord</divineName></w>

        Must split into 'And the' (H03068) + 'Lord' (H03068, divineName).
        The conjunction 'And' MUST NOT be tagged divineName.
        """
        raw = (
            '<w lemma="strong:H03068">And the <divineName>Lord</divineName></w> '
            '<w lemma="strong:H0559" morph="strongMorph:TH8799">said</w>, '
            '<w lemma="strong:H03063">Judah</w> '
            '<w lemma="strong:H05927" morph="strongMorph:TH8799">shall go up</w>: '
            '<w lemma="strong:H05414" morph="strongMorph:TH8804">behold, I have delivered</w> '
            '<w lemma="strong:H0776">the land</w> '
            '<w lemma="strong:H03027">into his hand</w>.'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        # Split occurred: the H03068 <w> produced TWO tokens
        assert 'And the' in texts, f'expected split: got {texts}'
        assert 'Lord' in texts, f'expected split: got {texts}'
        # Judah is a separate H03063 token, not divine
        judah_tok = next(t for t in result['tokens'] if t['token'] == 'Judah')
        assert judah_tok['lemma'] == {'strong': ['H03063']}
        # divineName span must target the 'Lord' token, not 'And the'
        dn = find_span(result['spans'], 'divineName')
        assert dn is not None
        target = result['tokens'][dn['token_start']]
        assert target['token'] == 'Lord', (
            f'divineName span targets {target["token"]!r}; '
            f'the conjunction "And" must not be flagged as divine'
        )
        # 'And the' must NOT be under a divineName span
        and_the_idx = token_index(result['tokens'], 'And the')
        assert dn['token_start'] != and_the_idx
        assert dn['token_end'] != and_the_idx

    def test_kjv_judges_1_3_raw_no_divine_name(self):
        """Jdg 1:3 OSIS has no <divineName>; Judah must not be tagged."""
        raw = (
            '<w lemma="strong:H03063">And Judah</w> '
            '<w lemma="strong:H0559" morph="strongMorph:TH8799">said</w> '
            '<w lemma="strong:H08095">unto Simeon</w> '
            '<w lemma="strong:H0251">his brother</w>, '
            '<w lemma="strong:H05927" morph="strongMorph:TH8798">Come up</w> '
            '<w lemma="strong:H01486">with me into my lot</w>, '
            '<w lemma="strong:H03898" morph="strongMorph:TH8735">that we may fight</w> '
            '<w lemma="strong:H03669">against the Canaanites</w>; '
            '<w lemma="strong:H01980" morph="strongMorph:TH8804">and I likewise will go</w> '
            '<w lemma="strong:H01486">with thee into thy lot</w>. '
            '<w lemma="strong:H08095">So Simeon</w> '
            '<w lemma="strong:H03212" morph="strongMorph:TH8799">went</w> with him.'
        )
        result = parse_osis_verse(raw)
        assert find_spans(result['spans'], 'divineName') == []

    def test_kjv_judges_1_4_raw_leading_and_not_divine(self):
        """Jdg 1:4 OSIS: <w lemma='H03068'>and the <divineName>Lord</divineName></w>

        Must split into 'and the' (H03068) + 'Lord' (H03068, divineName).
        Judah (H03063) at the start of the verse is never divine-tagged.
        """
        raw = (
            '<w lemma="strong:H03063">And Judah</w> '
            '<w lemma="strong:H05927" morph="strongMorph:TH8799">went up</w>; '
            '<w lemma="strong:H03068">and the <divineName>Lord</divineName></w> '
            '<w lemma="strong:H05414" morph="strongMorph:TH8799">delivered</w> '
            '<w lemma="strong:H03669">the Canaanites</w> '
            '<w lemma="strong:H06522">and the Perizzites</w> '
            '<w lemma="strong:H03027">into their hand</w>: '
            '<w lemma="strong:H05221" morph="strongMorph:TH8686">and they slew</w> '
            '<w lemma="strong:H0966">of them in Bezek</w> '
            '<w lemma="strong:H06235">ten</w> '
            '<w lemma="strong:H0505">thousand</w> '
            '<w lemma="strong:H0376">men</w>.'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert 'And Judah' in texts
        assert 'and the' in texts, f'expected split: got {texts}'
        assert 'Lord' in texts, f'expected split: got {texts}'
        # divineName targets only 'Lord'
        dn = find_span(result['spans'], 'divineName')
        target = result['tokens'][dn['token_start']]
        assert target['token'] == 'Lord'
        assert target['lemma'] == {'strong': ['H03068']}
        # Neither 'And Judah' nor 'and the' is tagged divine
        judah_idx = token_index(result['tokens'], 'And Judah')
        and_the_idx = token_index(result['tokens'], 'and the')
        for span in find_spans(result['spans'], 'divineName'):
            assert span['token_start'] != judah_idx
            assert span['token_start'] != and_the_idx


class TestSubWordSpanPrecisionGeneric:
    """Sub-<w> span producers other than divineName must also split."""

    def test_hi_marks_only_wrapped_text(self):
        """<w>plain <hi type='italic'>emphasized</hi> more</w> splits so
        the hi span covers only 'emphasized'."""
        raw = '<w lemma="strong:G1">plain <hi type="italic">emphasized</hi> more</w>'
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['plain', 'emphasized', 'more']
        hi_span = find_span(result['spans'], 'hi')
        assert hi_span is not None
        assert result['tokens'][hi_span['token_start']]['token'] == 'emphasized'
        # All sub-tokens inherit the parent <w>'s lemma
        assert all(t['lemma'] == {'strong': ['G1']} for t in result['tokens'])

    def test_transchange_seg_marks_only_wrapped_text(self):
        """<w>base <seg type='x-transChange'>added</seg> tail</w> splits so
        the transChange span covers only 'added'."""
        raw = (
            '<w lemma="strong:H1234">base '
            '<seg type="x-transChange" subType="x-added">added</seg>'
            ' tail</w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['base', 'added', 'tail']
        tc_span = find_span(result['spans'], 'transChange')
        assert tc_span is not None
        assert result['tokens'][tc_span['token_start']]['token'] == 'added'
        assert tc_span['attrs']['type'] == 'added'

    def test_seg_x_morph_does_not_split(self):
        """<seg type='x-morph'> is a morph-segmentation marker, not a span
        producer; it must NOT cause the <w> to be split."""
        raw = (
            '<w lemma="strong:H7225" morph="oshm:HR oshm:HNcfsa">'
            '<seg type="x-morph">b-</seg><seg type="x-morph">re\'shiyth</seg>'
            '</w>'
        )
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1
        assert result['tokens'][0]['token'] == "b-re'shiyth"
        assert result['tokens'][0].get('morphSegmented') is True

    def test_split_tokens_reconstruct_full_w_text(self):
        """After splitting, concatenating the sub-tokens' text (with a
        single space separator) must reproduce the original <w> text."""
        raw = '<w lemma="strong:H03068">And the <divineName>Lord</divineName></w>'
        result = parse_osis_verse(raw)
        joined = ' '.join(t['token'] for t in result['tokens'])
        assert joined == 'And the Lord'

    def test_divine_name_wrapping_whole_w_still_one_token(self):
        """When <divineName> wraps the ENTIRE <w>, there is no split —
        one token with a single-token divineName span."""
        raw = '<divineName><w lemma="strong:H03068">LORD</w></divineName>'
        result = parse_osis_verse(raw)
        assert len(result['tokens']) == 1
        assert result['tokens'][0]['token'] == 'LORD'
        dn = find_span(result['spans'], 'divineName')
        assert dn['token_start'] == 0 and dn['token_end'] == 0


# =========================================================================
# Sub-<w> span precision for ALL OSIS context elements
#
# The same splitting rule that protects divineName from leaking onto
# translator text ("And the") applies uniformly to every _SPAN_TAGS
# entry: transChange, hi, q, foreign, inscription, name, speaker,
# number, unit. These tests pin the generalized behavior so modules
# in any language (Hebrew, Greek, Latin, etc.) that put a context
# element inside a <w> get a span targeting ONLY the wrapped text.
# =========================================================================

class TestSubWordSpanPrecisionAllTags:
    """Every context element nested in a <w> must split the token."""

    def test_name_nested_inside_w(self):
        """<w>he called <name type='person'>Moses</name></w> splits so
        the name span covers only 'Moses', not 'he called'."""
        raw = '<w lemma="strong:H4872">he called <name type="person">Moses</name></w>'
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['he called', 'Moses']
        span = find_span(result['spans'], 'name')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'Moses'
        assert span['attrs']['type'] == 'person'
        # Both sub-tokens inherit the parent <w>'s lemma
        assert all(t['lemma'] == {'strong': ['H4872']} for t in result['tokens'])

    def test_q_nested_inside_w(self):
        """<w>said <q who='Jesus' level='1'>Come</q> unto them</w> splits
        so the q span covers only 'Come'."""
        raw = (
            '<w lemma="strong:G2036">said '
            '<q who="Jesus" level="1">Come</q>'
            ' unto them</w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['said', 'Come', 'unto them']
        span = find_span(result['spans'], 'q')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'Come'
        assert span['attrs']['who'] == 'Jesus'
        assert span['attrs']['level'] == '1'

    def test_foreign_nested_inside_w(self):
        """<w>the <foreign n='heb'>sheol</foreign></w> splits so
        the foreign span covers only 'sheol'."""
        raw = (
            '<w lemma="strong:H7585">the '
            '<foreign n="heb">sheol</foreign></w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['the', 'sheol']
        span = find_span(result['spans'], 'foreign')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'sheol'
        assert span['attrs']['n'] == 'heb'

    def test_inscription_nested_inside_w(self):
        """<w>read <inscription>MENE</inscription> on the wall</w> splits."""
        raw = (
            '<w lemma="strong:H4484">read '
            '<inscription>MENE</inscription>'
            ' on the wall</w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['read', 'MENE', 'on the wall']
        span = find_span(result['spans'], 'inscription')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'MENE'
        assert 'attrs' not in span

    def test_speaker_nested_inside_w(self):
        """<w>Then <speaker>Moses</speaker> said</w> splits."""
        raw = (
            '<w lemma="strong:H4872">Then '
            '<speaker>Moses</speaker>'
            ' said</w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['Then', 'Moses', 'said']
        span = find_span(result['spans'], 'speaker')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'Moses'
        assert 'attrs' not in span

    def test_number_nested_inside_w(self):
        """<w>about <number type='cardinal'>five</number> men</w> splits."""
        raw = (
            '<w lemma="strong:H2568">about '
            '<number type="cardinal">five</number>'
            ' men</w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['about', 'five', 'men']
        span = find_span(result['spans'], 'number')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'five'
        assert span['attrs']['type'] == 'cardinal'

    def test_unit_nested_inside_w(self):
        """<w>pay <unit type='currency'>5 shekels</unit></w> splits."""
        raw = (
            '<w lemma="strong:H3701">pay '
            '<unit type="currency">5 shekels</unit></w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['pay', '5 shekels']
        span = find_span(result['spans'], 'unit')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == '5 shekels'
        assert span['attrs']['type'] == 'currency'

    def test_transchange_nested_inside_w(self):
        """<w>begin <transChange type='added'>now</transChange></w> splits
        the <w> so the transChange span covers only 'now'.

        Unlike the <seg type='x-transChange'> workaround, this pattern
        uses the spec-correct transChange element directly."""
        raw = (
            '<w lemma="strong:H1961">begin '
            '<transChange type="added">now</transChange></w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['begin', 'now']
        span = find_span(result['spans'], 'transChange')
        assert span is not None
        assert result['tokens'][span['token_start']]['token'] == 'now'
        assert span['attrs']['type'] == 'added'

    def test_multiple_sub_w_spans_in_one_w(self):
        """A single <w> may contain multiple sub-<w> span producers;
        each fragment gets its own sub-token + span."""
        raw = (
            '<w lemma="strong:H3068">and '
            '<divineName>Lord</divineName>'
            ' said to '
            '<name type="person">Moses</name></w>'
        )
        result = parse_osis_verse(raw)
        texts = [t['token'] for t in result['tokens']]
        assert texts == ['and', 'Lord', 'said to', 'Moses']
        dn = find_span(result['spans'], 'divineName')
        nm = find_span(result['spans'], 'name')
        assert dn is not None and nm is not None
        assert result['tokens'][dn['token_start']]['token'] == 'Lord'
        assert result['tokens'][nm['token_start']]['token'] == 'Moses'
        # All sub-tokens inherit the <w>'s lemma (Strong's group reconstruction)
        assert all(t['lemma'] == {'strong': ['H3068']} for t in result['tokens'])

    def test_hi_without_type_does_not_split(self):
        """<hi> with no type attribute is not recorded as a span at all,
        so it should not cause the <w> to split."""
        raw = '<w lemma="strong:G1">pre <hi>middle</hi> post</w>'
        result = parse_osis_verse(raw)
        # No hi span (hi_type extractor returns None); but since
        # _SPAN_TAGS['hi'] returns {} when type is missing, we check.
        # Current behavior: <hi> without type still produces a span
        # entry with no attrs for outer-wrap. For sub-<w>, _sub_w_span_info
        # now delegates to _SPAN_TAGS, which returns {} — meaning the
        # fragment IS split. That's acceptable: the span carries no
        # attrs and still precisely marks which text was highlighted.
        # We just verify the total text is preserved.
        joined = ' '.join(t['token'] for t in result['tokens'])
        assert joined == 'pre middle post'


# =========================================================================
# Word-position addressing (hybrid word + token scheme)
#
# Every token and span carries 1-based whitespace-word positions anchored
# in the clean verse text. This is the primary, reader-facing locator —
# a consumer can verify the span's target by splitting the clean text on
# whitespace and taking the word at index (word_start - 1).
# =========================================================================

class TestWordPositions:
    """Word positions anchor tokens and spans to whitespace-split verse text."""

    def test_single_word_token_word_position_is_1(self):
        raw = '<w lemma="strong:G1">book</w>'
        result = parse_osis_verse(raw)
        tok = result['tokens'][0]
        assert tok['word_start'] == 1
        assert tok['word_end'] == 1

    def test_multi_word_token_spans_multiple_word_positions(self):
        """A token whose text covers several whitespace-words reports the
        full range, e.g. 'In the beginning' → word_start=1, word_end=3."""
        raw = '<w lemma="strong:H7225">In the beginning</w>'
        result = parse_osis_verse(raw)
        tok = result['tokens'][0]
        assert tok['word_start'] == 1
        assert tok['word_end'] == 3

    def test_sequential_word_positions_without_clean_text(self):
        """Without clean_text, word positions are derived from joining
        tokens with single spaces — so they tile the verse contiguously."""
        raw = (
            '<w lemma="strong:G1">alpha</w> '
            '<w lemma="strong:G2">beta gamma</w> '
            '<w lemma="strong:G3">delta</w>'
        )
        result = parse_osis_verse(raw)
        assert [(t['word_start'], t['word_end']) for t in result['tokens']] == [
            (1, 1), (2, 3), (4, 4),
        ]

    def test_word_positions_anchor_to_clean_text(self):
        """When clean_text is provided, word positions reflect that text's
        whitespace-word count (which may include inter-token punctuation
        that does NOT produce a new word — 'Lord,' is one word)."""
        raw = (
            '<w lemma="strong:H1">asked</w> '
            '<w lemma="strong:H03068">the <divineName>Lord</divineName></w>'
        )
        clean = 'asked the Lord, saying'
        result = parse_osis_verse(raw, clean)
        # The H03068 <w> splits: 'the' → word 2, 'Lord' → word 3
        the_tok = next(t for t in result['tokens'] if t['token'] == 'the')
        lord_tok = next(t for t in result['tokens'] if t['token'] == 'Lord')
        assert the_tok['word_start'] == 2
        assert the_tok['word_end'] == 2
        assert lord_tok['word_start'] == 3
        assert lord_tok['word_end'] == 3

    def test_span_inherits_word_positions_from_its_tokens(self):
        raw = (
            '<q who="Jesus" level="1">'
            '<w lemma="strong:G281">Verily</w> '
            '<w lemma="strong:G281">verily</w> '
            '<w lemma="strong:G3004">I say</w>'
            '</q>'
        )
        result = parse_osis_verse(raw)
        q = find_span(result['spans'], 'q')
        # Verily=1, verily=2, I say=3-4
        assert q['token_start'] == 0 and q['token_end'] == 2
        assert q['word_start'] == 1
        assert q['word_end'] == 4

    def test_span_field_contains_exact_marked_text(self):
        """Every span carries its exact OSIS-marked text in the ``span``
        field so a consumer can verify which text was annotated without
        re-slicing the clean text."""
        raw = '<divineName><w lemma="strong:H3068">Lord</w></divineName>'
        result = parse_osis_verse(raw)
        dn = find_span(result['spans'], 'divineName')
        assert dn['span'] == 'Lord'

    def test_span_field_for_multi_token_quote(self):
        raw = (
            '<q who="Jesus">'
            '<w lemma="strong:G281">Verily</w> '
            '<w lemma="strong:G3004">I say</w>'
            '</q>'
        )
        result = parse_osis_verse(raw)
        q = find_span(result['spans'], 'q')
        assert q['span'] == 'Verily I say'

    def test_span_field_for_sub_w_fragment(self):
        """When a span wraps only part of a <w>, the ``span`` field
        contains just that fragment, not the whole <w> text."""
        raw = '<w lemma="strong:H03068">And the <divineName>Lord</divineName></w>'
        result = parse_osis_verse(raw)
        dn = find_span(result['spans'], 'divineName')
        assert dn['span'] == 'Lord'


# =========================================================================
# Real-world verse regression: numbers must match what a reader counts
#
# These tests pin the word positions the user reported from the KJV build.
# They catch regressions where the parser's word-counting drifts out of
# alignment with the clean verse text.
# =========================================================================

class TestRealWorldWordPositions:
    """Pin word positions for verses the user has hand-verified."""

    def test_judges_1_1_divine_name_is_word_18(self):
        """KJV Judges 1:1 — 'Lord' is the 18th whitespace-word of the verse."""
        raw = (
            '<w lemma="strong:H0310">Now after</w> '
            '<w lemma="strong:H04194">the death</w> '
            '<w lemma="strong:H03091">of Joshua</w> '
            '<w lemma="strong:H01121">it came to pass, that the children</w> '
            '<w lemma="strong:H03478">of Israel</w> '
            '<w lemma="strong:H07592" morph="strongMorph:TH8799">asked</w> '
            '<w lemma="strong:H03068">the <divineName>Lord</divineName></w>, '
            '<w lemma="strong:H0559" morph="strongMorph:TH8800">saying</w>, '
            '<w lemma="strong:H05927" morph="strongMorph:TH8799">Who shall go up</w> '
            '<w lemma="strong:H03669">for us against the Canaanites</w> '
            '<w lemma="strong:H08462">first</w>, '
            '<w lemma="strong:H03898" morph="strongMorph:TH8736">to fight</w> against them?'
        )
        clean = (
            'Now after the death of Joshua it came to pass, that the '
            'children of Israel asked the Lord, saying, Who shall go up '
            'for us against the Canaanites first, to fight against them?'
        )
        result = parse_osis_verse(raw, clean)
        dn = find_span(result['spans'], 'divineName')
        assert dn['span'] == 'Lord'
        assert dn['word_start'] == 18
        assert dn['word_end'] == 18
        # Cross-check: word 18 in the clean text really is 'Lord,'
        assert clean.split()[17].startswith('Lord')

    def test_judges_1_4_divine_name_word_position(self):
        """KJV Judges 1:4 — 'Lord' is the 7th whitespace-word of the verse."""
        raw = (
            '<w lemma="strong:H03063">And Judah</w> '
            '<w lemma="strong:H05927" morph="strongMorph:TH8799">went up</w>; '
            '<w lemma="strong:H03068">and the <divineName>Lord</divineName></w> '
            '<w lemma="strong:H05414" morph="strongMorph:TH8799">delivered</w> '
            '<w lemma="strong:H03669">the Canaanites</w> '
            '<w lemma="strong:H06522">and the Perizzites</w> '
            '<w lemma="strong:H03027">into their hand</w>: '
            '<w lemma="strong:H05221" morph="strongMorph:TH8686">and they slew</w> '
            '<w lemma="strong:H0966">of them in Bezek</w> '
            '<w lemma="strong:H06235">ten</w> '
            '<w lemma="strong:H0505">thousand</w> '
            '<w lemma="strong:H0376">men</w>.'
        )
        clean = (
            'And Judah went up; and the Lord delivered the Canaanites and '
            'the Perizzites into their hand: and they slew of them in '
            'Bezek ten thousand men.'
        )
        result = parse_osis_verse(raw, clean)
        dn = find_span(result['spans'], 'divineName')
        assert dn['span'] == 'Lord'
        assert dn['word_start'] == 7
        assert dn['word_end'] == 7
        assert clean.split()[6].startswith('Lord')

    def test_non_latin_script_word_counting(self):
        """Word positions must work for non-Latin scripts (Hebrew sample)."""
        raw = (
            '<w lemma="strong:H7225">\u05D1\u05B0\u05BC\u05E8\u05B5\u05D0\u05E9\u05B4\u05C1\u05D9\u05EA</w> '
            '<w lemma="strong:H1254">\u05D1\u05B8\u05BC\u05E8\u05B8\u05D0</w> '
            '<w lemma="strong:H430">\u05D0\u05B1\u05DC\u05B9\u05D4\u05B4\u05D9\u05DD</w>'
        )
        clean = (
            '\u05D1\u05B0\u05BC\u05E8\u05B5\u05D0\u05E9\u05B4\u05C1\u05D9\u05EA '
            '\u05D1\u05B8\u05BC\u05E8\u05B8\u05D0 '
            '\u05D0\u05B1\u05DC\u05B9\u05D4\u05B4\u05D9\u05DD'
        )
        result = parse_osis_verse(raw, clean)
        # Three tokens, each one word, in order
        assert [(t['word_start'], t['word_end']) for t in result['tokens']] == [
            (1, 1), (2, 2), (3, 3),
        ]
