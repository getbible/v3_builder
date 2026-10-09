# SPDX-License-Identifier: GPL-2.0-only
"""Explicit source encodings must never fall through to accidental UTF-8."""

import pytest

from getbiblesword_converter import ConversionError, _entry_text, _source_projection
from test_getbiblesword_converter import bv, bv_bytes


def record(raw, stripped=None):
    return {
        'raw': bv_bytes(raw),
        'stripped': bv_bytes(raw if stripped is None else stripped),
        'normalized_raw': None,
        'normalized_stripped': None,
    }


@pytest.mark.parametrize(('encoding', 'raw'), [
    ('SCSU', b'\x0e\x04\x01'),
    ('UTF-16LE', b'A\x00B'),
    ('UTF-16BE', b'\x00A\x00'),
])
def test_failed_unicode_source_never_publishes_original_bytes_as_utf8(encoding, raw):
    # These byte strings happen to be syntactically valid UTF-8, but their
    # controls/NULs are encoding bytes, not scripture text.
    assert raw.decode('utf-8')
    source, diagnostics = _source_projection(record(raw), 'Plain', encoding)
    assert source is None
    assert diagnostics[0]['code'] == 'source.encoding'
    with pytest.raises(ConversionError):
        _entry_text(record(raw), 'Plain', encoding)


def test_valid_utf16_stripped_projection_can_rescue_invalid_raw_markup():
    item = record(b'\x00\xd8', 'Word'.encode('utf-16-le'))
    assert _entry_text(item, 'OSIS', 'UTF-16LE') == 'Word'


@pytest.mark.parametrize('encoding', ['UTF-16LE', 'UTF_16_LE', 'utf16le'])
def test_unicode_encoding_aliases_have_identical_display_and_source_decoding(encoding):
    item = record('Word'.encode('utf-16-le'))
    assert _source_projection(item, 'Plain', encoding) == ('Word', [])
    assert _entry_text(item, 'Plain', encoding) == 'Word'


def test_explicit_single_byte_codec_is_not_replaced_by_accidental_utf8():
    raw = b'\xd0\xb0'
    assert raw.decode('utf-8') == 'а'
    assert raw.decode('cp1251') == 'Р°'
    assert _entry_text(record(raw), 'Plain', 'cp1251') == 'Р°'


def test_unknown_explicit_encoding_without_native_projection_is_actionable():
    with pytest.raises(ConversionError, match='(?i)encoding'):
        _entry_text(record(b'Word'), 'Plain', 'UnknownSourceEncoding')


def test_native_normalized_utf8_is_authoritative_for_scsu():
    item = record(b'\x0e\x04\x01')
    item['normalized_raw'] = bv('<w>Ё</w>')
    item['normalized_stripped'] = bv('Ё')
    assert _source_projection(item, 'OSIS', 'SCSU') == ('<w>Ё</w>', [])
    assert _entry_text(item, 'OSIS', 'SCSU') == 'Ё'


def test_native_normalized_raw_can_supply_display_when_normalized_stripped_is_null():
    item = record(b'\x0e\x04\x01')
    item['normalized_raw'] = bv('<title>Heading</title><w>Ё</w>')
    assert _entry_text(item, 'OSIS', 'SCSU') == 'Ё'


@pytest.mark.parametrize(('encoding', 'data', 'expected'), [
    ('UTF-8', 'λόγος café'.encode(), 'λόγος café'),
    ('UTF-8', 'λόγος '.encode() + b'caf\xe9', 'λόγος café'),
    ('Latin-1', b'\xc3\xa9', 'Ã©'),
    ('Windows-1252', b'\x93Word\x94', '“Word”'),
])
def test_pre_04_mainstream_contracts_remain_encoding_compatible(encoding, data, expected):
    item = record(data)
    del item['normalized_raw']
    del item['normalized_stripped']
    assert _entry_text(item, 'Plain', encoding) == expected
