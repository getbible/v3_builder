import json
from pathlib import Path

import pytest

from publication_policy import PublicationPolicy, PublicationPolicyError


def write_policy(path, approved, default="deny"):
    path.write_text(json.dumps({
        "schema_version": 1,
        "default": default,
        "approved_modules": approved,
    }))


def test_policy_allows_only_explicit_modules(tmp_path):
    path = tmp_path / "policy.json"
    write_policy(path, ["KJV", "WLC"])
    policy = PublicationPolicy.from_file(str(path))
    policy.require_approved(["KJV"])
    with pytest.raises(PublicationPolicyError, match="NewTranslation"):
        policy.require_approved(["KJV", "NewTranslation"])


def test_policy_must_be_default_deny(tmp_path):
    path = tmp_path / "policy.json"
    write_policy(path, ["KJV"], default="allow")
    with pytest.raises(PublicationPolicyError, match="default-deny"):
        PublicationPolicy.from_file(str(path))


def test_checked_in_catalog_is_fully_approved():
    root = Path(__file__).resolve().parents[1]
    policy = PublicationPolicy.from_file(str(root / "conf" / "PublicationPolicy.json"))
    module_map = json.loads((root / "conf" / "CrosswireModulesMap.json").read_text())
    policy.require_approved(module_map)
