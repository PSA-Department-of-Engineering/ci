"""Fixtures for the per-app conformance suite (REF-Homelab section 4).

This suite is the single home of the chart-side app-contract claims and runs in
each app's own CI, where the app is checked out: the shared build workflow
checks out this repo beside the app and points the suite at the app via
CONFORMANCE_APP_DIR (the checkout path, whose basename is the repo name) and
CONFORMANCE_APP_NAME (the repo name). The install-side claims (manifest parity,
realm isolation, promotion wiring) stay in the platform repo, which reads its
own deploy-* branches.

The tests resolve the app as `repo_root.parent / <name>`, the same shape the
suite had when it lived beside sibling clones, so `repo_root` here is a phantom
path whose parent is the app checkout's parent directory.

Local pre-flight: run against any app clone with
  CONFORMANCE_APP_DIR=../<app> CONFORMANCE_APP_NAME=<app> pytest conformance -q
The INT-HOMELAB-060 cross-check additionally reads the platform repo's deploy
branches when CONFORMANCE_PLATFORM_DIR points at a platform clone, and skips
without it (app CI has no platform credential; the studio and the local
pre-flight have the clone).
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
    name = _require_env("CONFORMANCE_APP_NAME")
    if not path.is_dir():
        raise RuntimeError(f"CONFORMANCE_APP_DIR {path} is not a directory")
    # The fleet app name is the lowercase form (foundry.yaml, chart names,
    # keycloak-<app>); a repo may carry capitals (Alexandria). The tests read
    # the app dir straight from this env, so only a wholly wrong basename is a
    # caller bug worth failing loudly on; case differences are fine.
    if path.name.lower() != name.lower():
        raise RuntimeError(
            f"CONFORMANCE_APP_DIR basename {path.name!r} must be the app name "
            f"{name!r} (case-insensitively); the suite judges exactly that checkout"
        )
    return path


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Phantom root whose parent holds the app checkout (see module docstring)."""
    return _app_dir().parent / "__conformance-root__"


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "deploy_repo" in metafunc.fixturenames:
        metafunc.parametrize("deploy_repo", [_require_env("CONFORMANCE_APP_NAME").lower()])
