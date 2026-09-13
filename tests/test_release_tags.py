"""Exercise the release step against real git tags without publishing."""
import os
import shutil
import subprocess
from pathlib import Path

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
