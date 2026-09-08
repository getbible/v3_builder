import hashlib
import http.client
import io
import json
import tarfile
import time
import urllib.error

import pytest

from scripts import install_getbiblesword as installer


def _archive(
    payload=b"native-binary",
    *,
    extra_members=(),
    include_executable=True,
):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
        if include_executable:
            member = tarfile.TarInfo("package/usr/bin/getbiblesword")
            member.mode = 0o755
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        for member in extra_members:
            bundle.addfile(member)
    return stream.getvalue()


def _symlink(name, target):
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    return member


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


def _http_release(archive):
    release, archive_name, digest = _release(archive)
    for asset_id, asset in enumerate(release["assets"], start=101):
        asset["url"] = (
            "https://api.github.com/repos/getbible/getbiblesword/releases/assets/"
            f"{asset_id}"
        )
        asset["browser_download_url"] = (
            "https://github.com/getbible/getbiblesword/releases/download/"
            f"{release['tag_name']}/{asset['name']}"
        )
    routes = {
        "https://api.github.com/repos/getbible/getbiblesword/releases/latest":
            json.dumps(release).encode(),
    }
    for asset, payload in zip(
        release["assets"],
        (archive, f"{digest}  {archive_name}\n".encode()),
    ):
        routes[asset["url"]] = payload
        routes[asset["browser_download_url"]] = payload
    return release, archive_name, digest, routes


def _http_error(url, status, *, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError(url, status, "injected HTTP failure", headers, None)


def _http_responses(monkeypatch, routes):
    """Exercise the real request/retry logic without any network or waiting."""

    outcomes = {
        url: list(value) if isinstance(value, list) else [value]
        for url, value in routes.items()
    }
    requests = []
    sleeps = []

    def urlopen(request, *, timeout):
        requests.append(request.full_url)
        assert timeout > 0
        assert request.full_url in outcomes, f"unexpected URL: {request.full_url}"
        sequence = outcomes[request.full_url]
        outcome = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome) if isinstance(outcome, bytes) else outcome

    monkeypatch.setattr(installer.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(installer, "_architecture", lambda: "x86_64")
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return requests, sleeps


def test_checked_in_release_policy_is_the_central_latest_stable_authority():
    policy = installer.load_release_policy(installer.DEFAULT_POLICY)

    assert policy.repository == "getbible/getbiblesword"
    assert policy.version == "latest"


def test_release_policy_rejects_unreviewed_fields(tmp_path):
    policy = tmp_path / "release.json"
    policy.write_text(
        json.dumps(
            {
                "schema": installer.POLICY_SCHEMA,
                "repository": "getbible/getbiblesword",
                "version": "latest",
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(installer.InstallError, match="fields must be exactly"):
        installer.load_release_policy(policy)


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


def test_release_library_links_are_ignored_without_being_created(
    tmp_path, monkeypatch
):
    archive = _archive(
        extra_members=(
            _symlink(
                "package/usr/lib/libgetbiblesword.so",
                "libgetbiblesword.so.0",
            ),
            _symlink(
                "package/usr/lib/libgetbiblesword.so.0",
                "libgetbiblesword.so.0.3.0",
            ),
        )
    )
    release, archive_name, digest = _release(archive, tag="v0.3.0")
    _responses(monkeypatch, release, archive, archive_name, digest)

    destination = tmp_path / "getbiblesword"
    installed = installer.install_release(
        "getbible/getbiblesword", "latest", str(destination)
    )

    assert installed.version == "0.3.0"
    assert destination.read_bytes() == b"native-binary"
    assert not destination.is_symlink()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["getbiblesword"]


def test_release_executable_must_not_be_a_link(tmp_path):
    archive = _archive(
        extra_members=(
            _symlink(
                "package/usr/bin/getbiblesword",
                "../../lib/getbiblesword",
            ),
        ),
        include_executable=False,
    )

    with pytest.raises(installer.InstallError, match="must be a regular file"):
        installer._extract_executable(archive, tmp_path / "getbiblesword")


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


def test_checksum_http_500_recovers_without_changing_resolved_release(
    tmp_path, monkeypatch
):
    archive = _archive()
    release, archive_name, digest, routes = _http_release(archive)
    archive_url, checksum_url = (asset["url"] for asset in release["assets"])
    routes[checksum_url] = [_http_error(checksum_url, 500), routes[checksum_url]]
    requests, sleeps = _http_responses(monkeypatch, routes)

    installed = installer.install_release(
        "getbible/getbiblesword", "latest", str(tmp_path / "getbiblesword")
    )

    assert requests == [next(iter(routes)), archive_url, checksum_url, checksum_url]
    assert sleeps == [1]
    assert installed.release_id == release["id"]
    assert installed.tag == release["tag_name"]
    assert installed.asset == archive_name
    assert installed.sha256 == digest
    assert (tmp_path / "getbiblesword").read_bytes() == b"native-binary"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_failures_retry_before_returning_bytes(monkeypatch, status):
    url = "https://api.github.com/releases/assets/101"
    requests, sleeps = _http_responses(
        monkeypatch, {url: [_http_error(url, status), b"complete asset"]}
    )

    assert installer._request(url, accept="application/octet-stream") == b"complete asset"
    assert requests == [url, url]
    assert sleeps == [1]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("temporary DNS failure"),
        TimeoutError("read timed out"),
        ConnectionResetError("connection reset"),
    ],
)
def test_transient_connection_failures_retry(monkeypatch, failure):
    url = "https://api.github.com/releases/assets/101"
    requests, sleeps = _http_responses(
        monkeypatch, {url: [failure, b"complete asset"]}
    )

    assert installer._request(url, accept="application/octet-stream") == b"complete asset"
    assert requests == [url, url]
    assert sleeps == [1]


def test_incomplete_body_is_discarded_before_retry(monkeypatch):
    class IncompleteResponse(io.BytesIO):
        def read(self, *args, **kwargs):
            raise http.client.IncompleteRead(b"partial asset", 100)

    url = "https://api.github.com/releases/assets/101"
    incomplete = IncompleteResponse()
    requests, sleeps = _http_responses(
        monkeypatch, {url: [incomplete, b"complete asset"]}
    )

    assert installer._request(url, accept="application/octet-stream") == b"complete asset"
    assert incomplete.closed
    assert requests == [url, url]
    assert sleeps == [1]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_http_failure_does_not_retry_or_use_public_fallback(
    monkeypatch, status
):
    asset = {
        "url": "https://api.github.com/releases/assets/101",
        "browser_download_url": "https://github.com/example/releases/download/v1/asset",
    }
    requests, sleeps = _http_responses(
        monkeypatch, {asset["url"]: _http_error(asset["url"], status)}
    )

    with pytest.raises(installer.InstallError, match=str(status)) as caught:
        installer._download_asset(asset)

    assert not isinstance(caught.value, installer.DownloadError)
    assert requests == [asset["url"]]
    assert sleeps == []


@pytest.mark.parametrize(
    "failure",
    [
        _http_error("https://api.github.com/releases/assets/101", 500),
        urllib.error.URLError("network unavailable"),
        TimeoutError("read timed out"),
    ],
)
def test_persistent_transport_failure_has_bounded_exponential_retries(
    monkeypatch, failure
):
    url = "https://api.github.com/releases/assets/101"
    requests, sleeps = _http_responses(monkeypatch, {url: failure})

    with pytest.raises(installer.DownloadError):
        installer._request(url, accept="application/octet-stream")

    assert requests == [url] * 4
    assert sleeps == [1, 2, 4]


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [("3", 3), ("600", 60), ("invalid", 1)],
)
def test_retry_after_is_honored_with_a_bounded_delay(
    monkeypatch, retry_after, expected_delay
):
    url = "https://api.github.com/releases/assets/101"
    requests, sleeps = _http_responses(
        monkeypatch,
        {url: [_http_error(url, 429, retry_after=retry_after), b"complete asset"]},
    )

    assert installer._request(url, accept="application/octet-stream") == b"complete asset"
    assert requests == [url, url]
    assert sleeps == [expected_delay]


def test_retry_after_http_date_is_honored(monkeypatch):
    url = "https://api.github.com/releases/assets/101"
    requests, sleeps = _http_responses(
        monkeypatch,
        {
            url: [
                _http_error(url, 503, retry_after="Wed, 21 Oct 2015 07:28:00 GMT"),
                b"complete asset",
            ]
        },
    )
    monkeypatch.setattr(time, "time", lambda: 1445412465)

    assert installer._request(url, accept="application/octet-stream") == b"complete asset"
    assert requests == [url, url]
    assert sleeps == [15]


@pytest.mark.parametrize("asset_index", [0, 1], ids=["archive", "checksum"])
def test_exhausted_asset_api_recovers_through_same_release_public_url(
    tmp_path, monkeypatch, asset_index
):
    archive = _archive()
    release, archive_name, digest, routes = _http_release(archive)
    asset = release["assets"][asset_index]
    routes[asset["url"]] = _http_error(asset["url"], 503)
    requests, sleeps = _http_responses(monkeypatch, routes)

    installed = installer.install_release(
        "getbible/getbiblesword", "latest", str(tmp_path / "getbiblesword")
    )

    expected = [next(iter(routes))]
    for candidate in release["assets"]:
        if candidate is asset:
            expected.extend([asset["url"]] * 4)
            expected.append(asset["browser_download_url"])
        else:
            expected.append(candidate["url"])
    assert requests == expected
    assert sleeps == [1, 2, 4]
    assert installed.release_id == release["id"]
    assert installed.tag == release["tag_name"]
    assert installed.asset == archive_name
    assert installed.sha256 == digest
    assert (tmp_path / "getbiblesword").read_bytes() == b"native-binary"


@pytest.mark.parametrize("asset_index", [0, 1], ids=["archive", "checksum"])
def test_persistent_outage_preserves_existing_executable_and_provenance(
    tmp_path, monkeypatch, capsys, asset_index
):
    release, _, _, routes = _http_release(_archive())
    asset = release["assets"][asset_index]
    for key in ("url", "browser_download_url"):
        routes[asset[key]] = _http_error(asset[key], 503)
    requests, sleeps = _http_responses(monkeypatch, routes)
    executable = tmp_path / "getbiblesword"
    metadata = tmp_path / "release.json"
    executable.write_bytes(b"previous verified executable")
    executable.chmod(0o755)
    prior_metadata = b'{"release_id":41,"version":"0.1.0"}\n'
    metadata.write_bytes(prior_metadata)

    result = installer.main(
        [
            "--repository", "getbible/getbiblesword", "--version", "latest",
            "--destination", str(executable), "--metadata", str(metadata),
        ]
    )

    assert result == 1
    output = capsys.readouterr()
    assert "error:" in output.err
    assert "installed getBibleSWORD" not in output.out
    assert requests.count(asset["url"]) == 4
    assert requests.count(asset["browser_download_url"]) == 4
    assert sleeps == [1, 2, 4, 1, 2, 4]
    assert executable.read_bytes() == b"previous verified executable"
    assert executable.stat().st_mode & 0o777 == 0o755
    assert metadata.read_bytes() == prior_metadata
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "getbiblesword", "release.json"
    ]


@pytest.mark.parametrize("integrity_failure", ["checksum", "api_digest"])
@pytest.mark.parametrize("use_public_fallback", [False, True], ids=["api", "public"])
def test_integrity_failure_does_not_fallback_or_install(
    tmp_path, monkeypatch, integrity_failure, use_public_fallback
):
    release, archive_name, _, routes = _http_release(_archive())
    archive_asset, checksum_asset = release["assets"]
    if integrity_failure == "checksum":
        routes[checksum_asset["url"]] = f"{'0' * 64}  {archive_name}\n".encode()
    else:
        archive_asset["digest"] = f"sha256:{'0' * 64}"
        routes[next(iter(routes))] = json.dumps(release).encode()
    if use_public_fallback:
        routes[archive_asset["url"]] = _http_error(archive_asset["url"], 500)
    requests, sleeps = _http_responses(monkeypatch, routes)
    destination = tmp_path / "getbiblesword"

    with pytest.raises(installer.InstallError, match="checksum|digest"):
        installer.install_release("getbible/getbiblesword", "latest", str(destination))

    expected = [next(iter(routes))]
    if use_public_fallback:
        expected.extend([archive_asset["url"]] * 4)
        expected.append(archive_asset["browser_download_url"])
    else:
        expected.append(archive_asset["url"])
    expected.append(checksum_asset["url"])
    assert requests == expected
    assert sleeps == ([1, 2, 4] if use_public_fallback else [])
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("version", ["main", "", "1", "1.2", "../0.1.1"])
def test_unsafe_or_non_release_versions_are_rejected(version):
    with pytest.raises(installer.InstallError, match="semantic"):
        installer._requested_version(version)
