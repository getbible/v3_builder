#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install the checksum-verified getBibleSWORD release selected by policy.

The checked-in release policy is the default authority for every workflow.
Operators can still pass an exact version when reproducing or investigating an
older build.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


POLICY_SCHEMA = "getbiblesword-release-policy/v1"
DEFAULT_POLICY = (
    Path(__file__).resolve().parents[1] / "conf" / "GetBibleSwordRelease.json"
)
_VERSION_PATTERN = re.compile(
    r"^v?([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)$"
)
_REPOSITORY_PATTERN = re.compile(r"^[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+$")


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleasePolicy:
    """Central selection policy shared by local and GitHub builds."""

    repository: str
    version: str


@dataclass(frozen=True)
class InstalledRelease:
    """Auditable facts about the exact release installed for this build."""

    repository: str
    release_id: int
    release_url: str
    tag: str
    version: str
    asset: str
    sha256: str
    executable: str


def _request(url: str, *, accept: str) -> bytes:
    headers = {
        "Accept": accept,
        "User-Agent": "getbible-v3-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise InstallError(f"GitHub returned HTTP {exc.code} for {url}") from exc


def _requested_version(version: str) -> str:
    if version == "latest":
        return version
    match = _VERSION_PATTERN.fullmatch(version)
    if not match:
        raise InstallError("version must be 'latest' or a semantic release version")
    return match.group(1)


def load_release_policy(path: str | Path) -> ReleasePolicy:
    """Load and strictly validate the central getBibleSWORD release policy."""

    source = Path(path).resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InstallError(f"release policy is missing: {source}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"release policy is not valid UTF-8 JSON: {source}") from exc
    if not isinstance(document, dict):
        raise InstallError("release policy must be a JSON object")
    expected_fields = {"schema", "repository", "version"}
    if set(document) != expected_fields:
        raise InstallError(
            "release policy fields must be exactly: "
            + ", ".join(sorted(expected_fields))
        )
    if document["schema"] != POLICY_SCHEMA:
        raise InstallError(f"unsupported release policy schema: {document['schema']!r}")
    repository = document["repository"]
    if (
        not isinstance(repository, str)
        or _REPOSITORY_PATTERN.fullmatch(repository) is None
    ):
        raise InstallError("release policy repository must be in owner/name form")
    version = document["version"]
    if not isinstance(version, str):
        raise InstallError("release policy version must be a string")
    return ReleasePolicy(
        repository=repository,
        version=_requested_version(version),
    )


def _release(repository: str, version: str) -> tuple[dict, str]:
    requested = _requested_version(version)
    endpoint = (
        f"https://api.github.com/repos/{repository}/releases/latest"
        if requested == "latest"
        else f"https://api.github.com/repos/{repository}/releases/tags/v{requested}"
    )
    payload = _request(
        endpoint,
        accept="application/vnd.github+json",
    )
    try:
        release = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InstallError("GitHub returned invalid release metadata") from exc
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise InstallError("release metadata has no tag")
    match = _VERSION_PATTERN.fullmatch(tag)
    if not match:
        raise InstallError("release tag is not a supported semantic version")
    resolved = match.group(1)
    if requested != "latest" and resolved != requested:
        raise InstallError("release tag does not match the requested version")
    if release.get("draft") is True or release.get("prerelease") is True:
        raise InstallError("draft and prerelease builds are not installable")
    if not isinstance(release.get("id"), int):
        raise InstallError("release metadata has no numeric id")
    return release, resolved


def _architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise InstallError(f"unsupported machine architecture: {machine}")


def _asset(release: dict, name: str) -> dict:
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(matches) != 1:
        raise InstallError(f"release must contain exactly one {name!r} asset")
    return matches[0]


def _download_asset(asset: dict) -> bytes:
    url = asset.get("url")
    if not isinstance(url, str):
        raise InstallError("release asset has no API URL")
    return _request(url, accept="application/octet-stream")


def _expected_digest(checksum: bytes, archive_name: str) -> str:
    try:
        text = checksum.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise InstallError("checksum asset is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([^\s]+)", text)
    if not match or match.group(2) != archive_name:
        raise InstallError("checksum asset has an unexpected format or filename")
    return match.group(1)


def _extract_executable(archive_bytes: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        candidates = []
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise InstallError("release archive contains an unsafe path")
            if member.issym() or member.islnk():
                raise InstallError("release archive contains a link")
            if member.isfile() and path.parts[-3:] == ("usr", "bin", "getbiblesword"):
                candidates.append(member)
        if len(candidates) != 1:
            raise InstallError("release archive does not contain one getbiblesword executable")
        source = archive.extractfile(candidates[0])
        if source is None:
            raise InstallError("could not read getbiblesword from the release archive")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                            stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def install_release(repository: str, version: str, destination: str) -> InstalledRelease:
    architecture = _architecture()
    release, resolved_version = _release(repository, version)
    archive_name = (
        f"getbiblesword-{resolved_version}-linux-{architecture}.tar.gz"
    )
    archive_asset = _asset(release, archive_name)
    archive = _download_asset(archive_asset)
    checksum = _download_asset(_asset(release, archive_name + ".sha256"))
    expected = _expected_digest(checksum, archive_name)
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise InstallError("getBibleSWORD release checksum verification failed")
    api_digest = archive_asset.get("digest")
    if api_digest is not None and api_digest != f"sha256:{actual}":
        raise InstallError("GitHub release asset digest does not match its bytes")
    target = Path(destination).resolve()
    _extract_executable(archive, target)
    return InstalledRelease(
        repository=repository,
        release_id=release["id"],
        release_url=str(release.get("html_url", "")),
        tag=f"v{resolved_version}",
        version=resolved_version,
        asset=archive_name,
        sha256=actual,
        executable=str(target),
    )


def _write_metadata(installed: InstalledRelease, destination: str) -> Path:
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(asdict(installed), output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def install(repository: str, version: str, destination: str) -> Path:
    """Compatibility wrapper returning only the installed executable path."""

    return Path(install_release(repository, version, destination).executable)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        default=str(DEFAULT_POLICY),
        help="central release-policy JSON file",
    )
    parser.add_argument(
        "--repository",
        help="override the policy repository for reproduction",
    )
    parser.add_argument(
        "--version",
        help="override the policy with a semantic release version or 'latest'",
    )
    parser.add_argument("--destination", default=".tools/getbiblesword")
    parser.add_argument(
        "--metadata",
        default=".tools/getbiblesword-release.json",
        help="write exact resolved release provenance to this JSON file",
    )
    args = parser.parse_args(argv)
    try:
        policy = load_release_policy(args.policy)
        repository = (
            args.repository
            or os.environ.get("GETBIBLESWORD_REPOSITORY")
            or policy.repository
        )
        version = (
            args.version
            or os.environ.get("GETBIBLESWORD_VERSION")
            or policy.version
        )
        installed = install_release(repository, version, args.destination)
        metadata = _write_metadata(installed, args.metadata)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"installed getBibleSWORD {installed.tag} at {installed.executable}")
    print(f"release metadata: {metadata}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
