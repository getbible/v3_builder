# SPDX-License-Identifier: GPL-2.0-only
"""Create a deterministic inventory for validated GetBibleSWORD contracts."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Iterable

from file_ops import write_json_minified
from getbiblesword_contract import CONTRACT_ID, ContractSummary


ARCHIVE_MANIFEST_ID = "getbible.contract-archive-manifest/v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_contract_manifest(
    contracts_directory: str | Path,
    summaries: Iterable[ContractSummary],
) -> Path:
    """Write the complete, deterministic inventory of an extraction run.

    ``stream_sha256`` authenticates the records before the contract footer.
    ``file_sha256`` authenticates the complete NDJSON file, including its footer.
    Both are retained so an archived file can be checked without reinterpreting
    the contract framing.
    """

    root = Path(contracts_directory).resolve()
    modules = []
    producer_versions = set()
    sword_versions = set()
    for summary in summaries:
        path = summary.path.resolve()
        if path.parent != root:
            raise ValueError(f"contract is outside archive directory: {path}")
        severities = Counter(
            diagnostic.get("severity", "unknown")
            for diagnostic in summary.diagnostics
        )
        producer_versions.add(summary.producer_version)
        sword_versions.add(summary.sword_version)
        modules.append(
            {
                "module": summary.module_name,
                "classification": summary.classification,
                "file": path.name,
                "file_size": path.stat().st_size,
                "file_sha256": _sha256_file(path),
                "stream_sha256": summary.stream_sha256,
                "entries": summary.entries,
                "artifacts": summary.artifacts,
                "artifact_bytes": summary.artifact_bytes,
                "diagnostics": dict(sorted(severities.items())),
                "unknown_record_types": list(summary.unknown_record_types),
            }
        )

    document = {
        "schema": ARCHIVE_MANIFEST_ID,
        "contract": CONTRACT_ID,
        "producer_versions": sorted(producer_versions),
        "sword_versions": sorted(sword_versions),
        "module_count": len(modules),
        "modules": modules,
    }
    target = root / "manifest.json"
    write_json_minified(document, target)
    return target
