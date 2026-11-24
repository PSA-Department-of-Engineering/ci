"""Exercise the release step against real git tags without publishing."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def test_release_ignores_fetched_delivery_and_historical_tags(tmp_path):
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/build.yml').read_text())
    step = next(s for s in workflow['jobs']['release']['steps'] if s.get('id') == 'release')
    script = step['run']
    invocation = next(line for line in script.splitlines() if line.startswith('npx '))
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'first')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'second')
    git('tag', 'v1.10.0')
    bash = shutil.which('bash')
    if os.name == 'nt':
        bash = str(Path(shutil.which('git')).parents[1] / 'bin/bash.exe')
    env = {**os.environ, 'GITHUB_OUTPUT': 'result.txt'}
    def run(replacement):
        (tmp_path / 'result.txt').write_text('')
        subprocess.run([bash, '-c', script.replace(invocation, replacement)], cwd=tmp_path, env=env, check=True, capture_output=True)
        return (tmp_path / 'result.txt').read_text()
    result = run('git tag delivered/app/P1; git tag v1.11.0 HEAD~1; git tag v1.12.0')
    assert 'new_release_version=1.12.0' in result
    assert 'delivered' not in result
    assert run(':') == 'new_release_published=false\n'
    env['GITHUB_RUN_ATTEMPT'] = '2'
    with pytest.raises(subprocess.CalledProcessError):
        run(':')  # Two release tags on the same commit are ambiguous.
    git('tag', '-d', 'v1.10.0')
    assert 'new_release_version=1.12.0' in run(':')


def test_retry_preserves_existing_versions_and_stops_on_an_unreadable_registry(tmp_path):
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/build.yml').read_text())
    build = next(s['run'] for s in workflow['jobs']['release']['steps'] if s.get('name') == 'Build and push image(s), chart, docs')
    functions = build[build.index('missing_on_retry()'):build.index('# cycjimmy/')]
    harness = r"""
docker() {
    test "$1 $2 $3 $4" = "buildx imagetools inspect ghcr.io/example/package:1.2.0" || return 3
    case "$REGISTRY_RESULT" in
        present) return 0;;
        missing) echo "manifest unknown" >&2; return 1;;
        unavailable) echo "registry temporarily unavailable" >&2; return 1;;
    esac
}
publish() { echo "$1" > published; }
"""
    bash = shutil.which('bash') if os.name != 'nt' else str(Path(shutil.which('git')).parents[1] / 'bin/bash.exe')
    marker = tmp_path / 'published'
    for result, expected_status, expected_publish in [('present', 0, False), ('missing', 0, True), ('unavailable', 2, False)]:
        response = subprocess.run([bash, '-ec', harness + functions + '\npublish_version ghcr.io/example/package:1.2.0 publish release'],
                                  cwd=tmp_path, env={**os.environ, 'REGISTRY_RESULT': result, 'GITHUB_RUN_ATTEMPT': '2'},
                                  capture_output=True)
        assert response.returncode == expected_status, response.stderr
        assert marker.exists() is expected_publish
        marker.unlink(missing_ok=True)
