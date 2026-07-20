from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NATIVE_BUILD_WORKFLOWS = (
    REPOSITORY_ROOT / ".github/workflows/build.yml",
    REPOSITORY_ROOT / ".github/workflows/test.yml",
    REPOSITORY_ROOT / ".github/workflows/test-public.yml",
)


@pytest.mark.parametrize("workflow_path", NATIVE_BUILD_WORKFLOWS, ids=lambda path: path.name)
def test_native_build_workflows_install_and_select_getbiblesword(workflow_path):
    workflow = workflow_path.read_text(encoding="utf-8")

    install = workflow.index("python3 scripts/install_getbiblesword.py")
    build = workflow.index("python3 src/builder.py")

    assert install < build
    assert "GETBIBLESWORD_BIN: ${{ github.workspace }}/.tools/getbiblesword" in workflow
