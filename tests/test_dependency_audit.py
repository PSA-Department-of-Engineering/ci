"""Exercise the dependency-audit composite's judgement without npm, pip-audit, or the network."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

AUDIT = Path(__file__).parents[1] / '.github/actions/dependency-audit/audit.py'
spec = importlib.util.spec_from_file_location('dependency_audit', AUDIT)
audit = importlib.util.module_from_spec(spec)
sys.modules['dependency_audit'] = audit
spec.loader.exec_module(audit)

# The shape `npm audit --omit=dev --json` prints: an advisory sits in the `via` of
# the package it names; a package vulnerable only through a dependency lists that
# dependency's name instead.
NPM_REPORT = {
    'auditReportVersion': 2,
    'vulnerabilities': {
        'astro': {
            'name': 'astro',
            'severity': 'high',
            'via': ['cache-lib'],
            'fixAvailable': {'name': 'astro', 'version': '2.10.9', 'isSemVerMajor': True},
        },
        'serial-lib': {
            'name': 'serial-lib',
            'severity': 'high',
            'via': [
                {'source': 1, 'name': 'serial-lib', 'title': 'Shared memory', 'severity': 'high',
                 'url': 'https://github.com/advisories/GHSA-aaaa-bbbb-0001', 'range': '<=5.9.2'},
                {'source': 2, 'name': 'serial-lib', 'title': 'Eager allocation', 'severity': 'low',
                 'url': 'https://github.com/advisories/GHSA-aaaa-bbbb-0002', 'range': '<=5.9.2'},
            ],
            'fixAvailable': True,
        },
        'cache-lib': {
            'name': 'cache-lib',
            'severity': 'critical',
            'via': [
                {'source': 3, 'name': 'cache-lib', 'title': 'Cross-user cache', 'severity': 'critical',
                 'url': 'https://github.com/advisories/GHSA-aaaa-bbbb-0003', 'range': '<=4.2.0'},
            ],
        },
    },
}

# The shape `pip-audit -f json` prints.
PIP_REPORT = {
    'dependencies': [
        {'name': 'template-lib', 'version': '3.1.2', 'vulns': [
            {'id': 'PYSEC-2026-1', 'fix_versions': [], 'aliases': ['GHSA-cccc-dddd-0001'], 'description': 'x'},
        ]},
        {'name': 'web-lib', 'version': '0.1.0', 'vulns': []},
        {'name': 'unauditable', 'skip_reason': 'not on PyPI'},
    ],
    'fixes': [],
}

TRACKED = ['app/frontend/package-lock.json', 'app/backend/pyproject.toml']
NO_FINDINGS = {'vulnerabilities': {}}


def entry(vulnerability='GHSA-aaaa-bbbb-0003', **overrides):
    return {
        'vulnerability': vulnerability,
        'justification': 'vulnerable_code_not_in_execute_path',
        'impact_statement': 'Used only by the build-time remote image cache; the site builds static.',
        **overrides,
    }


def write_accepted(root, entries):
    (root / audit.ACCEPTED_FILE).write_text(yaml.safe_dump(entries), encoding='utf-8')


def app(tmp_path):
    (tmp_path / 'app/backend').mkdir(parents=True)
    (tmp_path / 'app/backend/pyproject.toml').write_text('[project]\nname = "backend"\n', encoding='utf-8')
    return tmp_path


def run(root, *, npm=NPM_REPORT, pip=None):
    _, failures = audit.run(
        root,
        tracked=TRACKED,
        npm_audit=lambda r, b: npm,
        pip_audit=lambda r, b: pip or {'dependencies': []},
    )
    return failures


def kinds(failures):
    return sorted(f.kind for f in failures)


def test_discovery_skips_docs_harnesses_and_tool_only_pyprojects(tmp_path):
    (tmp_path / 'svc').mkdir()
    (tmp_path / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n', encoding='utf-8')
    (tmp_path / 'svc/pyproject.toml').write_text('[project]\nname = "svc"\n', encoding='utf-8')
    (tmp_path / 'odd').mkdir()
    (tmp_path / 'odd/pyproject.toml').write_text('[project]\nname = "odd"\n', encoding='utf-16')
    tracked = [
        'pyproject.toml',
        'svc/pyproject.toml',
        'odd/pyproject.toml',
        'web/package-lock.json',
        'api/requirements.txt',
        'api/requirements-dev.txt',
        'data/00-customer-requirements.txt',
        'docs/package-lock.json',
        'e2e/package-lock.json',
        'e2e-deployed/package-lock.json',
        'tests/fixtures/app/pyproject.toml',
        'package-lock.json',
    ]
    found = {(b.ecosystem, b.directory) for b in audit.discover(tmp_path, tracked)}
    assert found == {('npm', '.'), ('npm', 'web'), ('pypi', 'api'), ('pypi', 'svc')}


def test_npm_counts_high_and_critical_advisories_on_the_package_they_name():
    bundle = audit.Bundle('npm', 'app/frontend', 'app/frontend/package-lock.json')
    found = {(f.package, f.id, f.severity) for f in audit.npm_findings(bundle, NPM_REPORT)}
    assert found == {('serial-lib', 'GHSA-aaaa-bbbb-0001', 'high'), ('cache-lib', 'GHSA-aaaa-bbbb-0003', 'critical')}


def test_pip_counts_every_advisory():
    bundle = audit.Bundle('pypi', 'app/backend', 'app/backend/pyproject.toml')
    [finding] = audit.pip_findings(bundle, PIP_REPORT)
    assert (finding.package, finding.id, finding.aliases) == ('template-lib', 'PYSEC-2026-1', {'GHSA-cccc-dddd-0001'})


def test_an_audit_that_cannot_run_is_an_error(tmp_path):
    assert kinds(run(app(tmp_path), npm={'error': {'code': 'ENOLOCK', 'summary': 'no lockfile'}})) == ['error']


def test_no_file_and_no_findings_passes(tmp_path):
    assert run(app(tmp_path), npm=NO_FINDINGS) == []


def test_a_finding_no_entry_accepts_fails(tmp_path):
    assert kinds(run(app(tmp_path))) == ['unaccepted', 'unaccepted']


def test_accepted_findings_pass(tmp_path):
    root = app(tmp_path)
    write_accepted(root, [entry(), entry('GHSA-aaaa-bbbb-0001')])
    assert run(root) == []


def test_an_entry_accepts_a_pip_advisory_by_its_alias(tmp_path):
    root = app(tmp_path)
    write_accepted(root, [entry('GHSA-cccc-dddd-0001')])
    assert run(root, npm=NO_FINDINGS, pip=PIP_REPORT) == []


@pytest.mark.parametrize('accepted', [
    [entry(justification='inconvenient')],
    [entry(package='cache-lib')],
    [{'vulnerability': 'GHSA-aaaa-bbbb-0003'}],
    [entry('not-an-advisory-id')],
])
def test_an_entry_that_breaks_the_schema_fails(tmp_path, accepted):
    root = app(tmp_path)
    write_accepted(root, accepted)
    assert kinds(run(root, npm=NO_FINDINGS)) == ['malformed']


@pytest.mark.parametrize('content, encoding', [
    ('', 'utf-8'),
    ('accepted: []\n', 'utf-8'),
    ('[\n', 'utf-8'),
    ('- vulnerability: GHSA-aaaa-bbbb-0003\n', 'utf-16'),
])
def test_a_file_that_is_not_a_list_of_entries_fails(tmp_path, content, encoding):
    root = app(tmp_path)
    (root / audit.ACCEPTED_FILE).write_text(content, encoding=encoding)
    assert kinds(run(root, npm=NO_FINDINGS)) == ['malformed']


def test_the_audits_run_on_production_dependencies(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs['cwd']))
        return audit.subprocess.CompletedProcess(args, 0, stdout='{}', stderr='')

    monkeypatch.setattr(audit.subprocess, 'run', fake_run)
    audit.run_npm_audit(tmp_path, audit.Bundle('npm', 'web', 'web/package-lock.json'))
    audit.run_pip_audit(tmp_path, audit.Bundle('pypi', 'api', 'api/pyproject.toml'))
    audit.run_pip_audit(tmp_path, audit.Bundle('pypi', 'svc', 'svc/requirements.txt'))
    (npm, npm_cwd), (project, _), (requirements, _) = calls
    assert npm[1:] == ['audit', '--omit=dev', '--json'] and npm_cwd == tmp_path / 'web'
    assert project[1:] == ['-m', 'pip_audit', '-f', 'json', '--progress-spinner', 'off', 'api']
    assert requirements[-2:] == ['-r', 'svc/requirements.txt']


def test_report_only_exits_zero_and_a_gate_exits_one(capsys):
    failures = [audit.Failure('unaccepted', 'npm .: GHSA-aaaa-bbbb-0003 in cache-lib')]
    assert audit.report([], failures, report_only=True) == 0
    assert '::warning title=Dependency audit (unaccepted)::' in capsys.readouterr().out
    assert audit.report([], failures, report_only=False) == 1
    assert '::error title=Dependency audit (unaccepted)::' in capsys.readouterr().out
    assert audit.report([], [], report_only=False) == 0
