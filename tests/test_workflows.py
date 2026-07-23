from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NATIVE_BUILD_WORKFLOWS = (
    REPOSITORY_ROOT / ".github/workflows/build.yml",
    REPOSITORY_ROOT / ".github/workflows/test.yml",
    REPOSITORY_ROOT / ".github/workflows/test-public.yml",
)
KJV_INSPECTION_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/inspect-kjv-api.yml"
KJV_INSPECTION_MAP = REPOSITORY_ROOT / "conf/CrosswireModulesMapKJVInspection.json"
PREVIEW_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/preview-build.yml"
NATIVE_SMOKE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/native-smoke.yml"
GETBIBLESWORD_RELEASE_POLICY = (
    REPOSITORY_ROOT / "conf/GetBibleSwordRelease.json"
)


@pytest.mark.parametrize(
    "workflow_path", NATIVE_BUILD_WORKFLOWS, ids=lambda path: path.name
)
def test_native_build_workflows_install_and_select_getbiblesword(workflow_path):
    workflow = workflow_path.read_text(encoding="utf-8")

    install = workflow.index("python3 scripts/install_getbiblesword.py")
    build = workflow.index("python3 src/builder.py")

    assert install < build
    assert "GETBIBLESWORD_BIN: ${{ github.workspace }}/.tools/getbiblesword" in workflow


@pytest.mark.parametrize(
    "workflow_path", NATIVE_BUILD_WORKFLOWS, ids=lambda path: path.name
)
def test_publishing_workflows_do_not_second_guess_content_growth(workflow_path):
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "allow_output_growth" not in workflow
    assert "ALLOW_OUTPUT_GROWTH" not in workflow
    assert "--allow-output-growth" not in workflow
    assert "cancel-in-progress: false" in workflow


def test_publishing_workflows_serialize_each_destination_repository():
    production = (REPOSITORY_ROOT / ".github/workflows/build.yml").read_text(
        encoding="utf-8"
    )
    test_build = (REPOSITORY_ROOT / ".github/workflows/test.yml").read_text(
        encoding="utf-8"
    )
    public_domain = (
        REPOSITORY_ROOT / ".github/workflows/test-public.yml"
    ).read_text(encoding="utf-8")

    assert "group: getbible-v3-production-publication" in production
    assert "group: getbible-v3-test-publication" in test_build
    assert "group: getbible-v3-test-publication" in public_domain


@pytest.mark.parametrize(
    "workflow_path", NATIVE_BUILD_WORKFLOWS, ids=lambda path: path.name
)
def test_publishing_workflows_use_central_release_policy(workflow_path):
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "scripts/install_getbiblesword.py" in workflow
    assert "scripts/install_getbiblesword.py --version" not in workflow
    assert "scripts/install_getbiblesword.py --repository" not in workflow


def test_checked_in_release_policy_follows_latest_stable_getbiblesword():
    import json

    assert json.loads(GETBIBLESWORD_RELEASE_POLICY.read_text(encoding="utf-8")) == {
        "schema": "getbiblesword-release-policy/v1",
        "repository": "getbible/getbiblesword",
        "version": "latest",
    }


def test_every_installer_workflow_uses_the_central_policy_without_overrides():
    workflows = sorted((REPOSITORY_ROOT / ".github/workflows").glob("*.yml"))
    installer_workflows = [
        path
        for path in workflows
        if "scripts/install_getbiblesword.py" in path.read_text(encoding="utf-8")
    ]

    assert installer_workflows
    for path in installer_workflows:
        workflow = path.read_text(encoding="utf-8")
        assert "scripts/install_getbiblesword.py --version" not in workflow, path
        assert "scripts/install_getbiblesword.py --repository" not in workflow, path


def test_native_smoke_continues_to_follow_latest_extractor():
    workflow = NATIVE_SMOKE_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/install_getbiblesword.py" in workflow
    assert "scripts/install_getbiblesword.py --version" not in workflow
    assert "conf/GetBibleSwordRelease.json" in workflow


def test_kjv_inspection_workflow_is_fresh_read_only_and_never_publishes():
    workflow = KJV_INSPECTION_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "python scripts/install_getbiblesword.py" in workflow
    assert "--version" not in workflow
    assert "CrosswireModulesMapKJVInspection.json" in workflow
    assert "actions/cache" not in workflow
    assert "actions/upload-artifact" not in workflow
    assert "--pull" not in workflow
    assert "--push" not in workflow
    assert "${{ runner.temp }}" not in workflow
    assert "$RUNNER_TEMP/getbible-kjv-" in workflow
    assert "sword_contracts" in workflow
    assert "rm -rf --" in workflow


def test_kjv_inspection_workflow_prints_requested_structure_and_enforces_size():
    workflow = KJV_INSPECTION_WORKFLOW.read_text(encoding="utf-8")

    assert "scripts/inspect_api_output.py" in workflow
    assert '--scripture-root="$WORK_ROOT/api_scripture"' in workflow
    assert '--output-root="$WORK_ROOT/api_scripture"' in workflow
    assert '--output-root="$WORK_ROOT/api"' in workflow
    assert workflow.count('--output-root="$WORK_ROOT/api_scripture"') == 1
    assert workflow.count('--output-root="$WORK_ROOT/api"') == 1
    assert "--size-limit-mib=95" in workflow


def test_kjv_inspection_map_contains_only_kjv():
    import json

    assert json.loads(KJV_INSPECTION_MAP.read_text(encoding="utf-8")) == {"KJV": "kjv"}


def test_preview_workflow_does_not_retain_contract_artifacts():
    workflow = PREVIEW_WORKFLOW.read_text(encoding="utf-8")

    assert "Upload lossless NDJSON contracts" not in workflow
    assert "name: getbiblesword-contracts" not in workflow
    assert "Discard transient module and extraction data" in workflow
