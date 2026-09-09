"""Regression gates for complete, unpublished real-catalog validation."""

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/full-catalog.yml"


def test_native_integration_includes_lxx_from_the_production_map():
    def read(name):
        return json.loads((REPOSITORY_ROOT / "conf" / name).read_text(encoding="utf-8"))

    production = read("CrosswireModulesMap.json")
    integration = read("CrosswireModulesMapTest.json")
    assert integration["LXX"] == production["LXX"] == "lxx"
    assert all(production[name] == abbreviation for name, abbreviation in integration.items())


def test_full_catalog_build_is_read_only_and_cannot_inherit_publication_config():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "python src/builder.py --conf=/dev/null" in workflow
    assert "--bconf=conf/CrosswireModulesMap.json" in workflow
    assert "--publication-policy=conf/PublicationPolicy.json" in workflow
    assert "--repo-hash= --repo-scripture=" in workflow
    assert "--test" not in workflow
    assert "--pull" not in workflow
    assert "--push" not in workflow
    assert "secrets." not in workflow
    assert "continue-on-error" not in workflow
    assert "actions/cache" not in workflow
    assert "actions/upload-artifact" not in workflow


def test_full_catalog_validation_runs_before_merge_with_the_current_release_policy():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "\n  pull_request:\n" in workflow
    assert "\n  workflow_dispatch:\n" in workflow
    assert "timeout-minutes: 180" in workflow
    assert "python scripts/install_getbiblesword.py" in workflow
    assert ".tools/getbiblesword-release.json" in workflow
    assert "scripts/install_getbiblesword.py --version" not in workflow
    assert "scripts/install_getbiblesword.py --repository" not in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
