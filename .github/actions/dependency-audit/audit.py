"""The dependency audit: every npm and Python bundle a repo ships, judged against
the advisories its optional root accepted-advisories.yaml accepts.

A bundle is a tracked package-lock.json (npm), or a pyproject.toml with a
[project] table or a requirements.txt (Python). Discovery skips the docs site
(REF-Foundry section 4: the docs bundle is not the bar) and test harnesses
(tests/, test/, fixtures/, e2e*/).

npm bundles are audited with `npm audit --omit=dev`, counting high and critical
advisories. Python bundles are audited with pip-audit, counting every advisory,
since pip-audit reports no severity. The audit fails on a finding that no entry
accepts, on an accepted-advisories.yaml that does not validate against
accepted-advisories.schema.json beside this script, and on a bundle it cannot
audit.

Run locally from a repo root:
  python <this folder>/audit.py [--report-only]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import jsonschema
import yaml

SCHEMA = Path(__file__).resolve().parent / "accepted-advisories.schema.json"
ACCEPTED_FILE = "accepted-advisories.yaml"
NPM_SEVERITIES = {"high", "critical"}
HARNESS_DIRS = {"node_modules", "tests", "test", "fixtures"}


class AuditError(Exception):
    """An audit tool failed, so the bundle's state is unknown."""


class MalformedFile(Exception):
    """accepted-advisories.yaml does not validate against the schema."""


@dataclass(frozen=True)
class Bundle:
    ecosystem: str  # "npm" or "pypi"
    directory: str  # relative to the repo root, forward slashes; "." is the root
    source: str  # the file the audit reads


@dataclass(frozen=True)
class Finding:
    ecosystem: str
    bundle: str
    package: str
    id: str
    aliases: frozenset[str] = frozenset()
    severity: str = "any"
    detail: str = ""


@dataclass(frozen=True)
class Failure:
    kind: str  # unaccepted, malformed, error
    message: str


def _one_line(text: str) -> str:
    return " ".join(text.split())


# --- Discovery ---------------------------------------------------------------


def git_tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [name for name in out.decode("utf-8").split("\0") if name]


def _excluded(rel: PurePosixPath) -> bool:
    parts = rel.parts[:-1]
    if parts and parts[0] == "docs":
        return True
    return any(part in HARNESS_DIRS or part.startswith("e2e") for part in parts)


def _has_project_table(file: Path) -> bool:
    try:
        return "project" in tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False


def discover(root: Path, tracked: list[str]) -> list[Bundle]:
    """The bundles among the tracked files."""
    bundles = []
    for name in sorted(tracked):
        rel = PurePosixPath(name)
        if _excluded(rel):
            continue
        directory = str(rel.parent)
        if rel.name == "package-lock.json":
            bundles.append(Bundle("npm", directory, name))
        elif rel.name == "requirements.txt" or (rel.name == "pyproject.toml" and _has_project_table(root / rel)):
            bundles.append(Bundle("pypi", directory, name))
    return bundles


# --- Audits ------------------------------------------------------------------


def run_npm_audit(root: Path, bundle: Bundle) -> dict:
    npm = shutil.which("npm") or "npm"
    proc = subprocess.run(
        [npm, "audit", "--omit=dev", "--json"],
        cwd=root / bundle.directory,
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AuditError(f"npm audit printed no report (exit {proc.returncode}): {_one_line(proc.stderr)[-400:]}")


def npm_findings(bundle: Bundle, report: dict) -> list[Finding]:
    """High and critical advisories, each on the package it names.

    A package that is vulnerable only through a dependency lists that
    dependency's name as a string in `via`; the advisory itself is counted once,
    on the dependency.
    """
    if "error" in report:
        error = report["error"]
        raise AuditError(f"npm audit failed: {_one_line(str(error.get('summary') or error))}")
    found = []
    for vuln in report.get("vulnerabilities", {}).values():
        for via in vuln.get("via", []):
            if not isinstance(via, dict) or via.get("severity") not in NPM_SEVERITIES:
                continue
            url = str(via.get("url", ""))
            advisory = url.rsplit("/", 1)[-1] if "/advisories/" in url else str(via.get("source"))
            found.append(
                Finding(
                    "npm",
                    bundle.directory,
                    str(via.get("name") or vuln.get("name")),
                    advisory,
                    severity=via["severity"],
                    detail=_one_line(f"{via.get('title', '')} (vulnerable {via.get('range', '?')})"),
                )
            )
    return found


def run_pip_audit(root: Path, bundle: Bundle) -> dict:
    target = ["-r", bundle.source] if PurePosixPath(bundle.source).name == "requirements.txt" else [bundle.directory]
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-f", "json", "--progress-spinner", "off", *target],
        cwd=root,
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AuditError(f"pip-audit printed no report (exit {proc.returncode}): {_one_line(proc.stderr)[-400:]}")


def pip_findings(bundle: Bundle, report: dict) -> list[Finding]:
    """Every advisory pip-audit reports: it carries no severity to filter on."""
    found = []
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns") or []:
            found.append(
                Finding(
                    "pypi",
                    bundle.directory,
                    str(dep["name"]),
                    str(vuln["id"]),
                    aliases=frozenset(vuln.get("aliases") or []),
                    detail=_one_line(f"{dep['name']} {dep.get('version', '?')}"),
                )
            )
    return found


# --- The accepted file and the judgement -------------------------------------


def load_accepted(path: Path) -> list[dict]:
    """The accepted entries; none when the file is absent."""
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise MalformedFile(f"{ACCEPTED_FILE} is not UTF-8")
    except yaml.YAMLError as exc:
        raise MalformedFile(f"{ACCEPTED_FILE} is not YAML: {_one_line(str(exc))}")
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    errors = sorted(validator.iter_errors(data), key=lambda error: [str(p) for p in error.absolute_path])
    if errors:
        problems = "; ".join(
            f"{'/'.join(str(p) for p in error.absolute_path) or '(root)'}: {_one_line(error.message)}"
            for error in errors
        )
        raise MalformedFile(f"{ACCEPTED_FILE} does not match its schema: {problems}")
    return data


def judge(findings: list[Finding], entries: list[dict]) -> list[Failure]:
    """A failure for every finding no entry accepts, by its id or one of its aliases."""
    accepted = {entry["vulnerability"] for entry in entries}
    return [
        Failure(
            "unaccepted",
            f"{finding.ecosystem} {finding.bundle}: {finding.id} in {finding.package} ({finding.severity}): "
            f"{finding.detail}. Fix it; if no fix exists and the repo is not affected by it, "
            f"accept it in {ACCEPTED_FILE}.",
        )
        for finding in findings
        if finding.id not in accepted and not finding.aliases & accepted
    ]


def run(
    root: Path,
    *,
    tracked: list[str] | None = None,
    npm_audit: Callable[[Path, Bundle], dict] = run_npm_audit,
    pip_audit: Callable[[Path, Bundle], dict] = run_pip_audit,
) -> tuple[list[Bundle], list[Failure]]:
    failures = []
    try:
        entries = load_accepted(root / ACCEPTED_FILE)
    except MalformedFile as exc:
        failures.append(Failure("malformed", str(exc)))
        entries = []
    bundles = discover(root, git_tracked(root) if tracked is None else tracked)
    findings: dict[tuple[str, str, str, str], Finding] = {}
    for bundle in bundles:
        try:
            if bundle.ecosystem == "npm":
                found = npm_findings(bundle, npm_audit(root, bundle))
            else:
                found = pip_findings(bundle, pip_audit(root, bundle))
        except AuditError as exc:
            failures.append(Failure("error", f"{bundle.source}: {exc}"))
            continue
        for finding in found:
            findings.setdefault((finding.ecosystem, finding.bundle, finding.package, finding.id), finding)
    failures += judge(list(findings.values()), entries)
    return bundles, failures


# --- The report --------------------------------------------------------------


def report(bundles: list[Bundle], failures: list[Failure], report_only: bool) -> int:
    level = "warning" if report_only else "error"
    print("Bundles audited: " + (", ".join(b.source for b in bundles) or "none"))
    for failure in failures:
        print(f"::{level} title=Dependency audit ({failure.kind})::{failure.message}")
    if not failures:
        print("  OK: every finding is accepted")
    elif report_only:
        print(f"{len(failures)} failure(s), reported only: this audit does not fail the build.")
    else:
        print(f"Dependency audit failed: {len(failures)} failure(s) above.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## Dependency audit", ""]
        lines.append("Bundles: " + (", ".join(f"`{b.source}`" for b in bundles) or "none"))
        lines.append("")
        if failures:
            if report_only:
                lines += ["Reported only: these do not fail the build.", ""]
            lines += [f"- **{failure.kind}**: {failure.message}" for failure in failures]
        else:
            lines.append("Every finding is accepted.")
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0 if report_only or not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the repo checkout (default: here)")
    parser.add_argument("--report-only", action="store_true", help="report every failure and exit 0")
    args = parser.parse_args(argv)
    bundles, failures = run(args.root.resolve())
    return report(bundles, failures, args.report_only)


if __name__ == "__main__":
    sys.exit(main())
