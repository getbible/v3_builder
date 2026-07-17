# SPDX-License-Identifier: GPL-2.0-only
"""Secure subprocess adapter for the getBibleSWORD extraction executable."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from getbiblesword_contract import ContractSummary, validate_contract


class ModuleReader(Protocol):
    """Boundary implemented by native module extractors."""

    def extract(self, module_name: str, sword_path: str, output_path: str) -> ContractSummary:
        """Extract and independently validate one installed SWORD module."""


class GetBibleSwordError(RuntimeError):
    """Raised when the native extractor cannot be invoked safely."""


class GetBibleSwordReader:
    """Run getBibleSWORD as an isolated subprocess and validate its output."""

    def __init__(self, executable: str = "getbiblesword", *, timeout: int = 1800):
        self.executable = executable
        self.timeout = timeout

    def _resolve_executable(self) -> str:
        if os.path.sep in self.executable:
            path = Path(self.executable).expanduser().resolve()
            if not path.is_file() or not os.access(path, os.X_OK):
                raise GetBibleSwordError(f"getBibleSWORD executable is not runnable: {path}")
            return str(path)
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise GetBibleSwordError(
                f"getBibleSWORD executable {self.executable!r} was not found on PATH"
            )
        return resolved

    def extract(self, module_name: str, sword_path: str, output_path: str) -> ContractSummary:
        if not module_name or any(character in module_name for character in "\x00\r\n"):
            raise GetBibleSwordError("invalid SWORD module name")
        executable = self._resolve_executable()
        root = Path(sword_path).resolve(strict=True)
        if not root.is_dir():
            raise GetBibleSwordError(f"SWORD root is not a directory: {root}")
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
        )
        os.close(file_descriptor)
        os.unlink(temporary_name)  # exporter refuses an existing output without --force
        temporary = Path(temporary_name)
        command = [
            executable,
            "extract",
            "--sword-path", str(root),
            "--module", module_name,
            "--output", str(temporary),
        ]
        environment = os.environ.copy()
        environment.setdefault("LC_ALL", "C.UTF-8")
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=self.timeout,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            temporary.unlink(missing_ok=True)
            raise GetBibleSwordError(f"getBibleSWORD invocation failed for {module_name}") from exc
        if completed.returncode != 0:
            quarantine = destination.with_suffix(destination.suffix + ".failed")
            if temporary.exists():
                os.replace(temporary, quarantine)
            detail = completed.stderr.strip()[-4000:]
            raise GetBibleSwordError(
                f"getBibleSWORD failed for {module_name} with exit code "
                f"{completed.returncode}; contract quarantined at {quarantine}: {detail}"
            )
        if completed.stdout:
            temporary.unlink(missing_ok=True)
            raise GetBibleSwordError("getBibleSWORD wrote unexpected data to stdout")
        try:
            summary = validate_contract(
                temporary,
                expected_module=module_name,
                expected_classification="bible",
            )
        except Exception:
            quarantine = destination.with_suffix(destination.suffix + ".invalid")
            if temporary.exists():
                os.replace(temporary, quarantine)
            raise
        os.replace(temporary, destination)
        return replace(summary, path=destination)


def _safe_zip_member(name: str) -> PurePosixPath:
    if "\x00" in name or "\\" in name:
        raise GetBibleSwordError(f"unsafe ZIP member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise GetBibleSwordError(f"unsafe ZIP member path: {name!r}")
    if any(part in {"", "."} for part in path.parts):
        raise GetBibleSwordError(f"unsafe ZIP member path: {name!r}")
    return path


def materialize_sword_root(
    archives: list[str] | tuple[str, ...],
    output_directory: str,
    *,
    max_members_per_archive: int = 100_000,
    max_uncompressed_bytes_per_archive: int = 4 * 1024 * 1024 * 1024,
) -> Path:
    """Safely install downloaded module ZIPs into one explicit SWORD root.

    Duplicate paths are accepted only when their bytes are identical.  This makes
    combining independently packaged modules deterministic and prevents one module
    from silently replacing another module's files.
    """

    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    seen: dict[PurePosixPath, str] = {}
    for archive_name in sorted(archives):
        with zipfile.ZipFile(archive_name) as archive:
            members = archive.infolist()
            if len(members) > max_members_per_archive:
                raise GetBibleSwordError(f"ZIP has too many members: {archive_name}")
            total_size = sum(member.file_size for member in members)
            if total_size > max_uncompressed_bytes_per_archive:
                raise GetBibleSwordError(f"ZIP expands beyond the safety limit: {archive_name}")
            for member in members:
                relative = _safe_zip_member(member.filename)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise GetBibleSwordError(
                        f"module ZIP contains a symlink: {member.filename!r}"
                    )
                target = root.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                temporary = target.with_name(target.name + ".partial")
                temporary.unlink(missing_ok=True)
                try:
                    with archive.open(member, "r") as source, open(temporary, "xb") as output:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            digest.update(chunk)
                    fingerprint = digest.hexdigest()
                    previous = seen.get(relative)
                    if previous is not None and previous != fingerprint:
                        raise GetBibleSwordError(
                            f"module ZIPs contain conflicting path {relative.as_posix()!r}"
                        )
                    if target.exists():
                        existing = hashlib.sha256(target.read_bytes()).hexdigest()
                        if existing != fingerprint:
                            raise GetBibleSwordError(
                                f"SWORD root already has conflicting path {relative.as_posix()!r}"
                            )
                        temporary.unlink()
                    else:
                        os.replace(temporary, target)
                    seen[relative] = fingerprint
                finally:
                    temporary.unlink(missing_ok=True)
    if not (root / "mods.d").is_dir() and not (root / "mods.conf").is_file():
        raise GetBibleSwordError("materialized module set has no SWORD configuration")
    return root
