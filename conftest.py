"""Root conftest: registers the integration marker and CLI options."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration", action="store_true", default=False,
        help="Run slow integration tests (downloads real SWORD modules)",
    )
    parser.addoption(
        "--integration-seed", type=int, default=None,
        help="Random seed for integration test sampling (default: random)",
    )
    parser.addoption(
        "--integration-cache-dir", type=str, default=None,
        help="Directory to cache downloaded SWORD modules (default: .sword_cache/)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: slow tests that download and convert real SWORD modules",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_marker = pytest.mark.skip(reason="needs --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
