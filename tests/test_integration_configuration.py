"""A requested integration run must never pass by skipping the native boundary."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("binary_state", ["missing", "not_executable"])
def test_requested_integration_fails_when_native_binary_is_unavailable(
    tmp_path, binary_state
):
    binary = tmp_path / "getbiblesword"
    if binary_state == "not_executable":
        binary.write_text("not an executable", encoding="utf-8")
        binary.chmod(0o644)

    # Exercise pytest collection and fixture setup, without downloading modules.
    hooks = (REPOSITORY_ROOT / "conftest.py").read_text(encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        hooks
        + "\nfrom tests_integration.conftest import getbiblesword_executable\n",
        encoding="utf-8",
    )
    (tmp_path / "test_native.py").write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.integration\n"
        "def test_native(getbiblesword_executable):\n"
        "    assert getbiblesword_executable\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "GETBIBLESWORD_BIN": str(binary),
        "PYTHONPATH": str(REPOSITORY_ROOT),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--run-integration"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    assert "GETBIBLESWORD_BIN" in result.stdout
    assert "1 error" in result.stdout
    assert "skipped" not in result.stdout
