import hashlib
import io
import json
import tarfile

import pytest

from scripts import install_getbiblesword as installer


def _archive(payload=b"native-binary"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
        member = tarfile.TarInfo("package/usr/bin/getbiblesword")
        member.mode = 0o755
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def _release(archive, *, tag="v0.1.1", prerelease=False):
    version = tag.removeprefix("v")
    name = f"getbiblesword-{version}-linux-x86_64.tar.gz"
    digest = hashlib.sha256(archive).hexdigest()
    return {
        "id": 42,
        "tag_name": tag,
        "html_url": f"https://github.com/getbible/getbiblesword/releases/tag/{tag}",
        "draft": False,
        "prerelease": prerelease,
        "assets": [
            {
                "name": name,
                "url": "asset://archive",
                "digest": f"sha256:{digest}",
            },
            {"name": name + ".sha256", "url": "asset://checksum"},
        ],
    }, name, digest


def _responses(monkeypatch, release, archive, archive_name, digest):
    def request(url, *, accept):
        if url.startswith("https://api.github.com/repos/"):
            return json.dumps(release).encode()
        if url == "asset://archive":
            return archive
        if url == "asset://checksum":
            return f"{digest}  {archive_name}\n".encode()
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(installer, "_request", request)
    monkeypatch.setattr(installer, "_architecture", lambda: "x86_64")


def test_latest_stable_release_is_resolved_verified_and_recorded(tmp_path, monkeypatch):
    archive = _archive()
    release, archive_name, digest = _release(archive)
    _responses(monkeypatch, release, archive, archive_name, digest)

    installed = installer.install_release(
        "getbible/getbiblesword", "latest", str(tmp_path / "getbiblesword")
    )

    assert installed.version == "0.1.1"
    assert installed.tag == "v0.1.1"
    assert installed.sha256 == digest
    assert installed.asset == archive_name
    assert (tmp_path / "getbiblesword").read_bytes() == b"native-binary"
    assert (tmp_path / "getbiblesword").stat().st_mode & 0o111

    metadata = installer._write_metadata(installed, str(tmp_path / "release.json"))
    document = json.loads(metadata.read_text(encoding="utf-8"))
    assert document["version"] == "0.1.1"
    assert document["release_id"] == 42
    assert document["sha256"] == digest


def test_explicit_version_remains_available_for_reproduction(tmp_path, monkeypatch):
    archive = _archive()
    release, archive_name, digest = _release(archive)
    _responses(monkeypatch, release, archive, archive_name, digest)

    installed = installer.install_release(
        "getbible/getbiblesword", "v0.1.1", str(tmp_path / "getbiblesword")
    )
    assert installed.version == "0.1.1"


def test_prerelease_is_rejected_even_from_latest_endpoint(monkeypatch):
    archive = _archive()
    release, archive_name, digest = _release(archive, prerelease=True)
    _responses(monkeypatch, release, archive, archive_name, digest)

    with pytest.raises(installer.InstallError, match="prerelease"):
        installer.install_release("getbible/getbiblesword", "latest", "/tmp/unused")


def test_checksum_mismatch_is_rejected(tmp_path, monkeypatch):
    archive = _archive()
    release, archive_name, digest = _release(archive)
    _responses(monkeypatch, release, archive, archive_name, "0" * 64)

    with pytest.raises(installer.InstallError, match="checksum"):
        installer.install_release(
            "getbible/getbiblesword", "latest", str(tmp_path / "getbiblesword")
        )


@pytest.mark.parametrize("version", ["main", "", "1", "1.2", "../0.1.1"])
def test_unsafe_or_non_release_versions_are_rejected(version):
    with pytest.raises(installer.InstallError, match="semantic"):
        installer._requested_version(version)
