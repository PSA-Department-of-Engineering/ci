"""Fixtures for the per-app conformance suite (REF-Homelab section 4).

This suite is the single home of the chart-side app-contract claims and runs in
each app's own CI, where the app is checked out: the shared build workflow
checks out this repo beside the app and points the suite at the app via
CONFORMANCE_APP_DIR (the checkout path) and CONFORMANCE_APP_NAME (the app's
fleet name: its install-manifest entry, and the string every platform artifact
is keyed on). The two are independent: a repo's name is a GitHub label, the
fleet name is what the platform binds, and a repo may ship an app named
differently from itself. The install-side claims (manifest parity, realm
isolation, promotion wiring) stay in the platform repo, which reads its own
deploy-* branches.

The tests read the checkout straight from CONFORMANCE_APP_DIR (see
`_repo_dir`), so `repo_root` here is a phantom path whose parent is the app
checkout's parent directory, kept for the (root, name) call shape.

Local pre-flight: run against any app clone with
  CONFORMANCE_APP_DIR=../<app> CONFORMANCE_APP_NAME=<app> pytest conformance -q
Every check here reads the app checkout alone, with ONE deliberate exception:
the reconcile check (INT-HOMELAB-061) reads the platform repository's deploy-*
branches read-only (PLATFORM_REPO + PLATFORM_READ_TOKEN), because the drift it
catches - the app growing past the promotion record the platform holds for
it - is caused by app-side changes and must bite in the app's own pipeline,
never fail the platform's standing report. Unarmed (no token), it reports a
named skip. Rules needing richer install context (the credential demand-grant
cross-check) stay in platform-studio.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(
            f"{key} is not set; the conformance suite judges exactly one app checkout "
            "(set CONFORMANCE_APP_DIR and CONFORMANCE_APP_NAME)"
        )
    return value


def _app_dir() -> Path:
    path = Path(_require_env("CONFORMANCE_APP_DIR")).resolve()
    _require_env("CONFORMANCE_APP_NAME")
    if not path.is_dir():
        raise RuntimeError(f"CONFORMANCE_APP_DIR {path} is not a directory")
    return path


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Phantom root whose parent holds the app checkout (see module docstring)."""
    return _app_dir().parent / "__conformance-root__"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "deploy_repo" in metafunc.fixturenames:
        metafunc.parametrize("deploy_repo", [_require_env("CONFORMANCE_APP_NAME").lower()])
