import base64
import hashlib
import json

import pytest

from getbiblesword_contract import ContractError, decode_byte_value, validate_contract


def byte_value(value):
    data = value if isinstance(value, bytes) else value.encode()
    result = {
        "base64": base64.b64encode(data).decode(),
        "encoding": "base64",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    try:
        result["utf8"] = data.decode()
    except UnicodeDecodeError:
        pass
    return result


def write_contract(path, body, *, success=True, digest_override=None):
    records = []
    for sequence, record in enumerate(body):
        records.append({"sequence": sequence, **record})
    payload = b"".join(
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        for record in records
    )
    counts = {}
    for record in records:
        counts[record["type"]] = counts.get(record["type"], 0) + 1
    artifact_ends = [record for record in records if record["type"] == "artifact_end"]
    footer = {
        "sequence": len(records),
        "type": "footer",
        "artifact_bytes": sum(record["size"] for record in artifact_ends),
        "artifacts": len(artifact_ends),
        "counts": counts,
        "entries": sum(record["type"] == "entry" for record in records),
        "stream_sha256": digest_override or hashlib.sha256(payload).hexdigest(),
        "success": success,
    }
    path.write_bytes(
        payload + json.dumps(footer, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )


@pytest.fixture
def body():
    return [
        {
            "type": "header",
            "command": "extract",
            "contract": "getbiblesword.ndjson/v1",
            "contract_version": 1,
            "producer": "getBibleSword",
            "producer_version": "0.1.0",
            "sword_version": "1.9.0",
        },
        {
            "type": "module",
            "classification": "bible",
            "name": byte_value("KJV"),
            "driver": byte_value("zText"),
            "sword_type": byte_value("Biblical Texts"),
        },
        {
            "type": "entry",
            "ordinal": 0,
            "key": byte_value("Genesis 1:1"),
            "scope": {"type": "verse_key", "chapter": 1, "verse": 1},
            "raw": byte_value("In the beginning"),
            "rendered_default": byte_value("In the beginning"),
            "stripped": byte_value("In the beginning"),
            "official_attributes": [],
            "annotation_segments": [],
        },
    ]


def test_valid_contract_establishes_trusted_summary(tmp_path, body):
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body)
    summary = validate_contract(path, expected_module="KJV", expected_classification="bible")
    assert summary.entries == 1
    assert summary.module_name == "KJV"
    assert summary.producer_version == "0.1.0"


def test_rejects_tampered_stream_digest(tmp_path, body):
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body, digest_override="0" * 64)
    with pytest.raises(ContractError, match="stream SHA-256"):
        validate_contract(path)


def test_rejects_tampered_byte_envelope(tmp_path, body):
    body[2]["raw"]["size"] += 1
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body)
    with pytest.raises(ContractError, match="size does not match"):
        validate_contract(path)


def test_rejects_unsuccessful_footer(tmp_path, body):
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body, success=False)
    with pytest.raises(ContractError, match="unsuccessful"):
        validate_contract(path)


def test_artifact_chunks_are_reassembled_for_validation(tmp_path, body):
    data = b"module bytes"
    body.extend([
        {
            "type": "artifact_begin", "artifact_id": 0,
            "file_type": "regular", "mode": 0o644,
            "path": byte_value("modules/texts/ztext/kjv/ot.bzs"), "role": "module_data",
        },
        {"type": "artifact_chunk", "artifact_id": 0, "index": 0, "data": byte_value(data)},
        {
            "type": "artifact_end", "artifact_id": 0, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    ])
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body)
    summary = validate_contract(path)
    assert summary.artifacts == 1
    assert summary.artifact_bytes == len(data)


def test_decode_byte_value_accepts_non_utf8_authoritative_bytes():
    assert decode_byte_value(byte_value(b"\xff\x00")) == b"\xff\x00"


def test_symlink_target_is_verified_without_creating_a_link(tmp_path, body):
    target = b"../shared/data"
    body.extend([
        {
            "type": "artifact_begin", "artifact_id": 0, "file_type": "symlink",
            "mode": 0o777, "path": byte_value("modules/link"),
            "target": byte_value(target), "role": "module_data",
        },
        {
            "type": "artifact_end", "artifact_id": 0, "size": len(target),
            "sha256": hashlib.sha256(target).hexdigest(),
        },
    ])
    path = tmp_path / "KJV.ndjson"
    write_contract(path, body)
    assert validate_contract(path).artifact_bytes == len(target)
