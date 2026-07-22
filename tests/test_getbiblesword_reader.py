import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from getbiblesword_contract import ContractError, ContractSummary
from getbiblesword_reader import (
    GetBibleSwordError,
    GetBibleSwordReader,
    materialize_sword_root,
)


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_materializes_a_deterministic_sword_root(tmp_path):
    first = tmp_path / "A.zip"
    second = tmp_path / "B.zip"
    make_zip(first, {"mods.d/a.conf": "[A]\n", "modules/a.dat": "A"})
    make_zip(second, {"mods.d/b.conf": "[B]\n", "modules/b.dat": "B"})
    root = materialize_sword_root([str(second), str(first)], str(tmp_path / "root"))
    assert (root / "mods.d" / "a.conf").read_text() == "[A]\n"
    assert (root / "modules" / "b.dat").read_text() == "B"


def test_rejects_zip_slip(tmp_path):
    archive = tmp_path / "bad.zip"
    make_zip(archive, {"../outside": "no", "mods.d/a.conf": "[A]\n"})
    with pytest.raises(GetBibleSwordError, match="unsafe ZIP member"):
        materialize_sword_root([str(archive)], str(tmp_path / "root"))


def test_rejects_conflicting_module_paths(tmp_path):
    first = tmp_path / "A.zip"
    second = tmp_path / "B.zip"
    make_zip(first, {"mods.d/shared.conf": "first"})
    make_zip(second, {"mods.d/shared.conf": "second"})
    with pytest.raises(GetBibleSwordError, match="conflicting path"):
        materialize_sword_root([str(first), str(second)], str(tmp_path / "root"))


def test_retries_a_transient_invalid_contract(tmp_path, monkeypatch):
    executable = tmp_path / "getbiblesword"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    sword_root = tmp_path / "root"
    sword_root.mkdir()
    destination = tmp_path / "KJV.ndjson"
    invocations = []
    validations = []

    def run(command, **kwargs):
        assert "--output" not in command
        kwargs["stdout"].write(b"contract\n")
        invocations.append(tuple(command))
        return SimpleNamespace(returncode=0, stderr=b"")

    def validate(path, **_kwargs):
        validations.append(Path(path))
        if len(validations) == 1:
            raise ContractError("truncated contract")
        return ContractSummary(
            path=Path(path),
            producer_version="0.1.1",
            sword_version="1.9.0",
            module_name="KJV",
            classification="bible",
            entries=1,
            artifacts=0,
            artifact_bytes=0,
            stream_sha256="0" * 64,
            diagnostics=(),
            unknown_record_types=(),
        )

    monkeypatch.setattr("getbiblesword_reader.subprocess.run", run)
    monkeypatch.setattr("getbiblesword_reader.validate_contract", validate)

    summary = GetBibleSwordReader(
        str(executable), validation_attempts=2
    ).extract("KJV", str(sword_root), str(destination))

    assert len(invocations) == 2
    assert len(validations) == 2
    assert summary.path == destination
    assert destination.read_bytes() == b"contract\n"
