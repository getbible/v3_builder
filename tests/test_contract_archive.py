import hashlib
import json

import pytest

from contract_archive import ARCHIVE_MANIFEST_ID, write_contract_manifest
from getbiblesword_contract import ContractSummary


def _summary(path):
    return ContractSummary(
        path=path,
        producer_version="0.1.1",
        sword_version="1.9.0",
        module_name="KJV",
        classification="bible",
        entries=31102,
        artifacts=4,
        artifact_bytes=1234,
        stream_sha256="a" * 64,
        diagnostics=({"severity": "info"}, {"severity": "warning"}),
        unknown_record_types=("future_record",),
    )


def test_manifest_authenticates_complete_contract_files(tmp_path):
    contract = tmp_path / "KJV.ndjson"
    contract.write_bytes(b'{"type":"header"}\n')

    manifest = write_contract_manifest(tmp_path, [_summary(contract)])
    document = json.loads(manifest.read_text(encoding="utf-8"))

    assert document["schema"] == ARCHIVE_MANIFEST_ID
    assert document["producer_versions"] == ["0.1.1"]
    assert document["module_count"] == 1
    module = document["modules"][0]
    assert module["module"] == "KJV"
    assert module["file_size"] == contract.stat().st_size
    assert module["file_sha256"] == hashlib.sha256(contract.read_bytes()).hexdigest()
    assert module["stream_sha256"] == "a" * 64
    assert module["diagnostics"] == {"info": 1, "warning": 1}
    assert module["unknown_record_types"] == ["future_record"]


def test_manifest_rejects_contracts_outside_archive_root(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    outside = tmp_path / "KJV.ndjson"
    outside.write_text("contract", encoding="utf-8")

    with pytest.raises(ValueError, match="outside archive"):
        write_contract_manifest(archive, [_summary(outside)])
