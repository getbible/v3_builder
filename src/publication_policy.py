# SPDX-License-Identifier: GPL-2.0-only
"""Default-deny publication authorization for SWORD modules."""

from __future__ import annotations

import json
from dataclasses import dataclass
class PublicationPolicyError(ValueError):
    """Raised when a requested module is not explicitly approved."""


@dataclass(frozen=True)
class PublicationPolicy:
    approved_modules: frozenset[str]
    schema_version: int = 1

    @classmethod
    def from_file(cls, path: str) -> "PublicationPolicy":
        with open(path, "r", encoding="utf-8") as stream:
            document = json.load(stream)
        if document.get("schema_version") != 1:
            raise PublicationPolicyError("unsupported publication policy schema")
        if document.get("default") != "deny":
            raise PublicationPolicyError("publication policy must be default-deny")
        approved = document.get("approved_modules")
        if not isinstance(approved, list) or not all(
            isinstance(module, str) and module for module in approved
        ):
            raise PublicationPolicyError("approved_modules must be a list of names")
        if len(approved) != len(set(approved)):
            raise PublicationPolicyError("approved_modules contains duplicates")
        return cls(frozenset(approved))

    def require_approved(self, module_names) -> None:
        requested = set(module_names)
        missing = sorted(requested - self.approved_modules)
        if missing:
            rendered = ", ".join(missing)
            raise PublicationPolicyError(
                "publication approval is missing for module(s): " + rendered
            )

    def select_approved(self, module_names) -> list[str]:
        self.require_approved(module_names)
        return [module for module in module_names if module in self.approved_modules]
