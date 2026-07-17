#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install a pinned, checksum-verified getBibleSWORD GitHub release asset."""

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
from pathlib import Path, PurePosixPath


DEFAULT_REPOSITORY = "getbible/getbiblesword"
DEFAULT_VERSION = "0.1.0"


class InstallError(RuntimeError):
    pass


def _request(url: str, token: str | None, *, accept: str) -> bytes:
    headers = {
        "Accept": accept,
        "User-Agent": "getbible-v3-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise InstallError(f"GitHub returned HTTP {exc.code} for {url}") from exc


def _release(repository: str, version: str, token: str | None) -> dict:
    payload = _request(
        f"https://api.github.com/repos/{repository}/releases/tags/v{version}",
        token,
        accept="application/vnd.github+json",
    )
    try:
        release = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InstallError("GitHub returned invalid release metadata") from exc
    if release.get("tag_name") != f"v{version}":
        raise InstallError("release tag does not match the pinned version")
    return release


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


def _download_asset(asset: dict, token: str | None) -> bytes:
    url = asset.get("url")
    if not isinstance(url, str):
        raise InstallError("release asset has no API URL")
    return _request(url, token, accept="application/octet-stream")


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


def install(repository: str, version: str, destination: str, token: str | None) -> Path:
    architecture = _architecture()
    archive_name = f"getbiblesword-{version}-linux-{architecture}.tar.gz"
    release = _release(repository, version, token)
    archive = _download_asset(_asset(release, archive_name), token)
    checksum = _download_asset(_asset(release, archive_name + ".sha256"), token)
    expected = _expected_digest(checksum, archive_name)
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise InstallError("getBibleSWORD release checksum verification failed")
    target = Path(destination).resolve()
    _extract_executable(archive, target)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--destination", default=".tools/getbiblesword")
    parser.add_argument("--token", default=os.environ.get("GETBIBLESWORD_TOKEN"))
    args = parser.parse_args(argv)
    try:
        path = install(args.repository, args.version, args.destination, args.token)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
