# SPDX-License-Identifier: GPL-2.0-only
"""Independent validator and reader for getBibleSWORD NDJSON contracts.

The exporter is deliberately treated as an untrusted subprocess boundary.  This
module validates framing, sequence numbers, every byte envelope, artifact groups,
and the footer digest before any record is allowed into the API conversion layer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


CONTRACT_ID = "getbiblesword.ndjson/v1"
MAX_RECORD_BYTES = 32 * 1024 * 1024


class ContractError(ValueError):
    """Raised when an NDJSON contract is incomplete, corrupt, or unsafe."""


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ContractSummary:
    """Trusted facts established by :func:`validate_contract`."""

    path: Path
    producer_version: str
    sword_version: str
    module_name: str
    classification: str
    entries: int
    artifacts: int
    artifact_bytes: int
    stream_sha256: str
    diagnostics: tuple[dict[str, Any], ...]
    unknown_record_types: tuple[str, ...]


def decode_byte_value(value: Any, *, location: str = "byte value") -> bytes:
    """Decode and verify a contract byte-value envelope."""

    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    required = {"base64", "encoding", "sha256", "size"}
    missing = required - value.keys()
    if missing:
        raise ContractError(f"{location} is missing {sorted(missing)}")
    extra = set(value) - required - {"utf8"}
    if extra:
        raise ContractError(f"{location} has unknown members {sorted(extra)}")
    if value["encoding"] != "base64":
        raise ContractError(f"{location} has unsupported encoding")
    if not isinstance(value["base64"], str):
        raise ContractError(f"{location}.base64 must be a string")
    try:
        decoded = base64.b64decode(value["base64"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContractError(f"{location} contains invalid base64") from exc
    if not _is_integer(value["size"]):
        raise ContractError(f"{location}.size must be an integer")
    if value["size"] != len(decoded):
        raise ContractError(f"{location} size does not match decoded bytes")
    digest = hashlib.sha256(decoded).hexdigest()
    if value["sha256"] != digest:
        raise ContractError(f"{location} SHA-256 does not match decoded bytes")
    if "utf8" in value:
        try:
            utf8 = decoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(f"{location}.utf8 is present for non-UTF-8 bytes") from exc
        if value["utf8"] != utf8:
            raise ContractError(f"{location}.utf8 does not match decoded bytes")
    return decoded


def byte_value_text(value: Any, *, location: str = "byte value") -> str:
    """Return verified UTF-8 text, failing instead of silently replacing bytes."""

    return decode_byte_value(value, location=location).decode("utf-8")


def iter_contract(path: os.PathLike[str] | str) -> Iterator[dict[str, Any]]:
    """Yield JSON records without asserting trust; validate the file first."""

    with open(path, "rb") as stream:
        for line_number, raw_line in _bounded_lines(stream):
            if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
                raise ContractError(f"line {line_number} is not LF-terminated")
            try:
                record = json.loads(raw_line[:-1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContractError(f"line {line_number} is not valid UTF-8 JSON") from exc
            if not isinstance(record, dict):
                raise ContractError(f"line {line_number} must contain a JSON object")
            yield record


def _bounded_lines(stream):
    line_number = 0
    while True:
        raw_line = stream.readline(MAX_RECORD_BYTES + 1)
        if not raw_line:
            return
        line_number += 1
        if len(raw_line) > MAX_RECORD_BYTES:
            raise ContractError(f"line {line_number} exceeds the record size limit")
        yield line_number, raw_line


def _verify_nested_byte_values(value: Any, location: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _verify_nested_byte_values(item, f"{location}[{index}]")
        return
    if not isinstance(value, dict):
        return
    envelope_keys = {"base64", "encoding", "sha256", "size"}
    if value.get("encoding") == "base64" or envelope_keys.issubset(value):
        decode_byte_value(value, location=location)
        return
    for key, item in value.items():
        _verify_nested_byte_values(item, f"{location}.{key}")


def validate_contract(
    path: os.PathLike[str] | str,
    *,
    expected_module: str | None = None,
    expected_classification: str | None = None,
    require_success: bool = True,
) -> ContractSummary:
    """Independently validate a complete getBibleSWORD NDJSON v1 stream."""

    contract_path = Path(path)
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    diagnostics: list[dict[str, Any]] = []
    unknown_types: set[str] = set()
    known_types = {
        "header", "module", "config_source", "config_entry", "entry",
        "artifact_begin", "artifact_chunk", "artifact_end", "diagnostic", "footer",
    }
    header: dict[str, Any] | None = None
    module: dict[str, Any] | None = None
    footer: dict[str, Any] | None = None
    expected_sequence = 0
    entry_count = 0
    expected_ordinals = {"config_source": 0, "config_entry": 0, "entry": 0}
    artifact_count = 0
    artifact_bytes = 0
    active_artifact: dict[str, Any] | None = None
    last_stage = -1
    stages = {
        "header": 0,
        "module": 1,
        "config_source": 2,
        "config_entry": 3,
        "entry": 4,
        "artifact_begin": 5,
        "artifact_chunk": 5,
        "artifact_end": 5,
    }

    with open(contract_path, "rb") as stream:
        for line_number, raw_line in _bounded_lines(stream):
            if footer is not None:
                raise ContractError("footer must be the final record")
            if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
                raise ContractError(f"line {line_number} is not LF-terminated")
            try:
                record = json.loads(raw_line[:-1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ContractError(f"line {line_number} is not valid UTF-8 JSON") from exc
            if not isinstance(record, dict):
                raise ContractError(f"line {line_number} must contain a JSON object")
            record_type = record.get("type")
            sequence = record.get("sequence")
            if not _is_integer(sequence) or sequence != expected_sequence:
                raise ContractError(
                    f"line {line_number} has sequence {sequence!r}; expected {expected_sequence}"
                )
            expected_sequence += 1
            if not isinstance(record_type, str) or not record_type:
                raise ContractError(f"line {line_number} has no record type")
            _verify_nested_byte_values(record, f"line {line_number}")

            if record_type == "footer":
                footer = record
                continue

            digest.update(raw_line)
            counts[record_type] += 1
            if record_type not in known_types:
                unknown_types.add(record_type)

            if record_type in stages:
                stage = stages[record_type]
                if stage < last_stage:
                    raise ContractError(f"{record_type} record is out of contract order")
                last_stage = stage

            if header is None:
                if record_type != "header":
                    raise ContractError("header must be the first record")
                header = record
                if record.get("contract") != CONTRACT_ID or record.get("contract_version") != 1:
                    raise ContractError("unsupported getBibleSWORD contract")
                if record.get("command") != "extract":
                    raise ContractError("contract was not produced by the extract command")
                for field in ("producer", "producer_version", "sword_version"):
                    if not isinstance(record.get(field), str) or not record[field]:
                        raise ContractError(f"header.{field} must be a non-empty string")
                continue

            if record_type == "header":
                raise ContractError("contract contains more than one header")
            if record_type == "module":
                if module is not None:
                    raise ContractError("extract contract contains more than one module")
                module = record
            elif record_type == "entry":
                entry_count += 1
            elif record_type == "diagnostic":
                diagnostics.append(record)
            elif record_type == "artifact_begin":
                if active_artifact is not None:
                    raise ContractError("artifact groups may not overlap")
                active_artifact = {
                    "id": record.get("artifact_id"),
                    "next_index": 0,
                    "size": 0,
                    "digest": hashlib.sha256(),
                    "file_type": record.get("file_type"),
                }
                if not _is_integer(active_artifact["id"]):
                    raise ContractError("artifact_id must be an integer")
                if active_artifact["id"] != artifact_count:
                    raise ContractError("artifact_id values must be zero-based and contiguous")
                if active_artifact["file_type"] == "symlink":
                    target = decode_byte_value(
                        record.get("target"), location="artifact_begin.target"
                    )
                    active_artifact["digest"].update(target)
                    active_artifact["size"] = len(target)
            elif record_type == "artifact_chunk":
                if active_artifact is None:
                    raise ContractError("artifact_chunk appears outside an artifact group")
                if record.get("artifact_id") != active_artifact["id"]:
                    raise ContractError("artifact_chunk has the wrong artifact_id")
                if not _is_integer(record.get("index")) or record.get("index") != active_artifact["next_index"]:
                    raise ContractError("artifact_chunk indices are not contiguous")
                data = decode_byte_value(record.get("data"), location="artifact_chunk.data")
                active_artifact["digest"].update(data)
                active_artifact["size"] += len(data)
                active_artifact["next_index"] += 1
            elif record_type == "artifact_end":
                if active_artifact is None:
                    raise ContractError("artifact_end appears without artifact_begin")
                if record.get("artifact_id") != active_artifact["id"]:
                    raise ContractError("artifact_end has the wrong artifact_id")
                if record.get("size") != active_artifact["size"]:
                    raise ContractError("artifact_end size does not match its chunks")
                if record.get("sha256") != active_artifact["digest"].hexdigest():
                    raise ContractError("artifact_end SHA-256 does not match its chunks")
                artifact_count += 1
                artifact_bytes += active_artifact["size"]
                active_artifact = None

            if record_type in expected_ordinals:
                if not _is_integer(record.get("ordinal")) or record.get("ordinal") != expected_ordinals[record_type]:
                    raise ContractError(f"{record_type} ordinals are not zero-based and contiguous")
                expected_ordinals[record_type] += 1

    if header is None:
        raise ContractError("contract is empty or missing its header")
    if module is None:
        raise ContractError("extract contract is missing its module record")
    if active_artifact is not None:
        raise ContractError("contract ends inside an artifact group")
    if footer is None:
        raise ContractError("contract is missing its footer")
    if footer.get("stream_sha256") != digest.hexdigest():
        raise ContractError("footer stream SHA-256 does not match preceding lines")
    if require_success and footer.get("success") is not True:
        raise ContractError("getBibleSWORD reported an unsuccessful extraction")
    if not isinstance(footer.get("counts"), dict):
        raise ContractError("footer counts must be an object")
    if footer["counts"] != dict(sorted(counts.items())) and footer["counts"] != dict(counts):
        raise ContractError("footer record counts do not match the stream")
    if footer.get("entries") != entry_count:
        raise ContractError("footer entry count does not match the stream")
    if footer.get("artifacts") != artifact_count:
        raise ContractError("footer artifact count does not match the stream")
    if footer.get("artifact_bytes") != artifact_bytes:
        raise ContractError("footer artifact byte count does not match the stream")
    for field in ("entries", "artifacts", "artifact_bytes"):
        if not _is_integer(footer.get(field)):
            raise ContractError(f"footer.{field} must be an integer")
    severity_counts = Counter(record.get("severity") for record in diagnostics)
    declared_diagnostics = footer.get("diagnostics")
    if declared_diagnostics is not None:
        expected_diagnostics = {
            "error": severity_counts.get("error", 0),
            "info": severity_counts.get("info", 0),
            "warning": severity_counts.get("warning", 0),
        }
        if declared_diagnostics != expected_diagnostics:
            raise ContractError("footer diagnostic counts do not match the stream")

    module_name = byte_value_text(module.get("name"), location="module.name")
    classification = module.get("classification")
    if expected_module is not None and module_name != expected_module:
        raise ContractError(
            f"contract contains module {module_name!r}, expected {expected_module!r}"
        )
    if expected_classification is not None and classification != expected_classification:
        raise ContractError(
            f"module classification is {classification!r}, expected {expected_classification!r}"
        )

    return ContractSummary(
        path=contract_path,
        producer_version=str(header.get("producer_version", "")),
        sword_version=str(header.get("sword_version", "")),
        module_name=module_name,
        classification=str(classification),
        entries=entry_count,
        artifacts=artifact_count,
        artifact_bytes=artifact_bytes,
        stream_sha256=digest.hexdigest(),
        diagnostics=tuple(diagnostics),
        unknown_record_types=tuple(sorted(unknown_types)),
    )


def _safe_artifact_path(root: Path, path_value: Any) -> Path:
    relative_text = byte_value_text(path_value, location="artifact path")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ContractError(f"unsafe artifact path: {relative_text!r}")
    if any(part in {"", "."} for part in relative.parts):
        raise ContractError(f"unsafe artifact path: {relative_text!r}")
    return root.joinpath(*relative.parts)


def reassemble_artifacts(
    contract_path: os.PathLike[str] | str,
    output_directory: os.PathLike[str] | str,
) -> list[Path]:
    """Reassemble regular-file artifacts after full contract validation.

    Symlinks are intentionally not created.  They remain represented losslessly in
    the NDJSON contract without becoming a filesystem escape primitive.
    """

    validate_contract(contract_path)
    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    target: Path | None = None
    temporary: Path | None = None
    stream = None
    file_type = None
    try:
        for record in iter_contract(contract_path):
            if record["type"] == "artifact_begin":
                target = _safe_artifact_path(root, record["path"])
                file_type = record.get("file_type")
                if file_type == "directory":
                    target.mkdir(parents=True, exist_ok=True)
                elif file_type == "regular":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(target.name + ".partial")
                    if temporary.exists():
                        temporary.unlink()
                    stream = open(temporary, "xb")
            elif record["type"] == "artifact_chunk" and stream is not None:
                stream.write(decode_byte_value(record["data"], location="artifact_chunk.data"))
            elif record["type"] == "artifact_end":
                if stream is not None and target is not None and temporary is not None:
                    stream.close()
                    stream = None
                    os.replace(temporary, target)
                    written.append(target)
                target = None
                temporary = None
                file_type = None
    finally:
        if stream is not None:
            stream.close()
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return written
