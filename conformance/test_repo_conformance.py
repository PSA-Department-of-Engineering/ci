"""Per-app conformance: the REF-Homelab section 4 app-repo contract (ADR-018).

The single home of the chart-side app-contract claims (002..010, 013, 018..020,
022..023, 029..030, 034, 037, 039, 045..048, 056..057, 060), run by the shared
build workflow in each app's own CI, where the app is checked out; `release`
needs it, so a non-conformant repo is never tagged or published. conftest.py
resolves the one judged app from CONFORMANCE_APP_DIR/CONFORMANCE_APP_NAME. The
install-side claims (manifest parity, realm isolation, promotion wiring) live
in the platform repo's own suite, which reads its deploy-* branches.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from pytest_intent import intent


def _repo_dir(repo_root: Path, name: str) -> Path:
    """The judged app's checkout, straight from the environment.

    The signature keeps the platform-era (root, name) shape so every call site
    reads unchanged, but resolution is by env rather than sibling lookup: the
    suite judges exactly one checkout, whose on-disk case may differ from the
    fleet name (Alexandria vs alexandria), so reconstructing the path by name
    would silently skip on a case-sensitive filesystem.
    """
    del repo_root, name
    return Path(os.environ["CONFORMANCE_APP_DIR"]).resolve()


def _sans_comments(text: str) -> str:
    """The template text with full-line `#` comments dropped (issue #7).

    The chart checks are deliberately textual (Helm templates are not parseable
    YAML), so every substring heuristic must read code, never commentary: a
    comment naming a trigger substring once misclassified a Service as the docs
    Service (INT-FOUNDRY-056, wttg3-helper sites-service), and a comment quoting
    `.Values.paused` once satisfied INT-FOUNDRY-030 for a Deployment that never
    rendered it - the silent variant, worse than the loud one. INT-FOUNDRY-057
    already took this stance by anchoring on the argument line; stripping at the
    read boundary generalizes it. Full-line comments only: trailing-`#` forms
    are rare in charts, and stripping them naively would mangle legitimate
    values containing '#'.
    """
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))


def _is_docs_component(doc: str) -> bool:
    """The docs-server heuristic shared by INT-FOUNDRY-030/048/056: a document
    is the docs component when its CODE carries the docs component label or a
    -docs-suffixed name. Callers pass comment-stripped text (`_sans_comments`)."""
    return "app.kubernetes.io/component: docs" in doc or "-docs" in doc


@intent("INT-FOUNDRY-002")
def test_has_devops_dockerfile(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-002: <repo>/devops/Dockerfile exists."""
    repo = _repo_dir(repo_root, deploy_repo)
    assert (repo / "devops" / "Dockerfile").is_file(), f"{deploy_repo}: missing devops/Dockerfile"


@intent("INT-FOUNDRY-003")
def test_compose_builds_from_devops_dockerfile(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-003: <repo>/devops/docker-compose.yml builds from devops/Dockerfile."""
    repo = _repo_dir(repo_root, deploy_repo)
    compose = repo / "devops" / "docker-compose.yml"
    assert compose.is_file(), f"{deploy_repo}: missing devops/docker-compose.yml"
    assert "devops/Dockerfile" in compose.read_text(encoding="utf-8"), (
        f"{deploy_repo}: devops/docker-compose.yml must build from devops/Dockerfile "
        "(the same Dockerfile that ships the image)"
    )


@intent("INT-FOUNDRY-004")
def test_no_local_push_script(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-004: no image-push script in <repo>/devops/."""
    repo = _repo_dir(repo_root, deploy_repo)
    offenders = [
        p.name
        for p in (repo / "devops").glob("*")
        if p.is_file() and "push" in p.name.lower() and "image" in p.name.lower()
    ]
    assert not offenders, (
        f"{deploy_repo}: image-push script(s) in devops/ (publishing is CI's job): {offenders}"
    )


@intent("INT-FOUNDRY-005")
def test_has_k8s_helm_chart(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-005: <repo>/k8s/ is a Helm chart, not loose manifests."""
    repo = _repo_dir(repo_root, deploy_repo)
    k8s = repo / "k8s"
    missing = [rel for rel in ("Chart.yaml", "templates", "values.yaml") if not (k8s / rel).exists()]
    assert not missing, f"{deploy_repo}: k8s/ is not a complete Helm chart, missing: {missing}"


@intent("INT-FOUNDRY-006")
def test_docs_is_deployable(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-006: <repo>/docs/ ships a Dockerfile and an Astro config."""
    repo = _repo_dir(repo_root, deploy_repo)
    docs = repo / "docs"
    assert docs.is_dir(), f"{deploy_repo}: missing docs/"
    assert (docs / "Dockerfile").is_file(), (
        f"{deploy_repo}: docs/ has no Dockerfile (docs must be deployable, not npm-run-dev only)"
    )
    assert any(docs.glob("astro.config.*")), (
        f"{deploy_repo}: docs/ has no astro.config.* (not a Starlight/Astro site)"
    )


@intent("INT-FOUNDRY-007")
def test_readme_documents_required_sections(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-007: README documents Run with Docker, Develop without Docker, and Layout."""
    repo = _repo_dir(repo_root, deploy_repo)
    readme = repo / "README.md"
    assert readme.is_file(), f"{deploy_repo}: missing README.md"
    text = readme.read_text(encoding="utf-8").lower()
    missing = [s for s in ("run with docker", "develop without docker", "layout") if s not in text]
    assert not missing, f"{deploy_repo}: README.md missing required section(s): {missing}"


@intent("INT-FOUNDRY-008")
def test_has_env_example(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-008: <repo>/.env.example exists."""
    repo = _repo_dir(repo_root, deploy_repo)
    assert (repo / ".env.example").is_file(), f"{deploy_repo}: missing .env.example"


def _doc_types(docs_dir: Path) -> set[str]:
    """The set of `type:` frontmatter values across a docs site's pages."""
    types: set[str] = set()
    content = docs_dir / "src" / "content" / "docs"
    if not content.is_dir():
        return types
    # Both .md and .mdx: Starlight pages are either, and an app's Overview/landing page is
    # often an .mdx splash page (e.g. SolveOS docs/src/content/docs/index.mdx, type: Overview).
    # Scanning only .md silently misses it and false-fails INT-FOUNDRY-010 (the platform's own
    # docs.test.ts already walks both).
    for md in (*content.rglob("*.md"), *content.rglob("*.mdx")):
        m = re.match(r"^---\n(.*?)\n---", md.read_text(encoding="utf-8"), re.DOTALL)
        if not m:
            continue
        for line in m.group(1).splitlines():
            if line.strip().startswith("type:"):
                types.add(line.split(":", 1)[1].strip().strip("'\""))
    return types


@intent("INT-FOUNDRY-009")
def test_has_root_intent_spec(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-009: <repo>/intent.yaml exists AND carries at least one
    canonical CSD-INTENT-01 claim.

    Existence alone is not the contract: csd-intent reads a file in a foreign
    schema as zero claims and reports CLEAN, so a repo can ship a
    kubernetes-styled intent.yaml with no actual intent surface and every audit
    stays green (task-api shipped exactly that). A canonical claim is a
    top-level `INT-*` key whose value carries a statement and a status; one is
    the floor, because zero attests nothing.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    path = repo / "intent.yaml"
    assert path.is_file(), (
        f"{deploy_repo}: missing root intent.yaml (CSD-INTENT-01 spec for the app's own claims)"
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    claims = {
        key: value
        for key, value in (data.items() if isinstance(data, dict) else [])
        if isinstance(key, str)
        and key.startswith("INT-")
        and isinstance(value, dict)
        and value.get("statement")
        and value.get("status")
    }
    assert claims, (
        f"{deploy_repo}: root intent.yaml declares no canonical CSD-INTENT-01 claim "
        "(a top-level INT-* key with a statement and a status); csd-intent reads a "
        "foreign schema as zero claims and passes it CLEAN, so the shape itself is "
        "the gate"
    )


@intent("INT-FOUNDRY-010")
def test_docs_has_required_pages(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-010: docs/ ships an Overview page and a Components/Architecture (Reference) page."""
    repo = _repo_dir(repo_root, deploy_repo)
    types = _doc_types(repo / "docs")
    missing = [t for t in ("Overview", "Reference") if t not in types]
    assert not missing, (
        f"{deploy_repo}: docs/ missing required page type(s) {missing}; found types: {sorted(types)}"
    )


@intent("INT-FOUNDRY-018")
def test_docs_honor_docs_base(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-018: docs honor DOCS_BASE so they mount under the portal prefix.

    astro.config.* must derive `base` from DOCS_BASE, and docs/Dockerfile must
    declare ARG DOCS_BASE, feed it into the build, and serve the built site under
    it. Astro's output is flat, so `base` (which rewrites URLs) is not enough on its
    own: the image must place files under the prefix or the no-rewrite portal route
    404s. ADR-018 Decision C/E, ADR-023.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    docs = repo / "docs"
    configs = list(docs.glob("astro.config.*"))
    assert configs, f"{deploy_repo}: docs/ has no astro.config.*"
    config = configs[0].read_text(encoding="utf-8")
    assert "DOCS_BASE" in config and re.search(r"base\s*:", config), (
        f"{deploy_repo}: docs/astro.config.* must set Astro `base` from DOCS_BASE "
        "(e.g. base: process.env.DOCS_BASE || '/')"
    )
    dockerfile = docs / "Dockerfile"
    assert dockerfile.is_file(), f"{deploy_repo}: docs/ has no Dockerfile"
    df = dockerfile.read_text(encoding="utf-8")
    assert "ARG DOCS_BASE" in df, (
        f"{deploy_repo}: docs/Dockerfile must declare ARG DOCS_BASE and pass it to the build"
    )
    assert re.search(r"html/?\$\{?DOCS_BASE\}?", df), (
        f"{deploy_repo}: docs/Dockerfile must serve the built site under DOCS_BASE, e.g. "
        "COPY --from=build /app/dist /usr/share/nginx/html${DOCS_BASE} (Astro output is "
        "flat; `base` rewrites URLs but does not relocate files, so a copy to the html "
        "root 404s under the portal's no-rewrite route)"
    )


@intent("INT-FOUNDRY-019")
def test_has_commit_message_hook_config(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-019: <repo>/.pre-commit-config.yaml ships a Conventional-Commit commit-msg hook.

    Asserts the committed config that *defines* the hook, not its installation: the
    .git/hooks wiring is per-clone (`pre-commit install`) and never committed, so it
    cannot be checked from the platform side. Versions are cut from Conventional Commits
    (ADR-024); this config is the local guard that the convention holds.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    config = repo / ".pre-commit-config.yaml"
    assert config.is_file(), (
        f"{deploy_repo}: missing .pre-commit-config.yaml (the committed Conventional-Commit "
        "commit-msg hook config; ADR-024, REF-Homelab section 4)"
    )
    text = config.read_text(encoding="utf-8").lower()
    assert "commit-msg" in text, (
        f"{deploy_repo}: .pre-commit-config.yaml declares no commit-msg-stage hook "
        "(a Conventional-Commit check must run at commit-msg, where the message exists)"
    )
    assert any(marker in text for marker in ("conventional", "commitlint")), (
        f"{deploy_repo}: .pre-commit-config.yaml has no Conventional-Commit/commitlint hook "
        "(the commit-msg hook must validate Conventional Commits; ADR-024)"
    )


@intent("INT-FOUNDRY-013")
def test_has_ci_caller(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-013: <repo>/.github/workflows/build.yml calls the shared ci reusable workflow."""
    repo = _repo_dir(repo_root, deploy_repo)
    caller = repo / ".github" / "workflows" / "build.yml"
    assert caller.is_file(), f"{deploy_repo}: missing .github/workflows/build.yml (the CI caller; ADR-019)"
    assert "PSA-Department-of-Engineering/ci/.github/workflows/build.yml" in caller.read_text(encoding="utf-8"), (
        f"{deploy_repo}: .github/workflows/build.yml must call the shared reusable workflow "
        "(uses: PSA-Department-of-Engineering/ci/.github/workflows/build.yml@...), not a copied build (ADR-019)"
    )


@intent("INT-FOUNDRY-063")
def test_has_app_owned_test_workflow(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-063: <repo>/.github/workflows/test.yml exists and runs on pull_request.

    build.yml is a thin caller so release and publish cannot drift; a test suite is
    genuinely per-app and has nothing to share, which is exactly why nothing ensured
    one existed. The file's existence and its PR trigger are the contract: without
    them a contributor's PR - a fork's especially - is merged with nothing having run
    against it, which only becomes visible once the repo is public.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    wf = repo / ".github" / "workflows" / "test.yml"
    assert wf.is_file(), (
        f"{deploy_repo}: missing .github/workflows/test.yml. The suite inside is the "
        "app's own, but the file is contract: without it a pull request meets no gate "
        "(REF-Foundry section 4)."
    )
    doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
    # PyYAML parses the bare key `on:` as the boolean True.
    on = doc.get("on", doc.get(True))
    keys = set(on) if isinstance(on, (dict, list)) else {on}
    assert "pull_request" in keys, (
        f"{deploy_repo}: .github/workflows/test.yml does not run on pull_request, so a "
        "contributor's PR meets no gate before merge (REF-Foundry section 4)."
    )


@intent("INT-FOUNDRY-020")
def test_has_deploy_branch(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-020: origin carries at least one deploy-<install> branch with
    dev/ + prod/ env config (ADR-025/032).

    Each install reconciles its own orphan `deploy-<install>` branch (ADR-032),
    so a repo that runs on N installs carries N deploy branches. Checked over
    git, not the working tree: enumerate origin's deploy-* heads, fetch each,
    and confirm every env folder ships a values.yaml and a Chart.yaml. The
    which-installs-declare-this-app direction (each declaring install's branch
    present) is the platform suite's per-install delivery check, which reads the
    manifests this suite cannot; here the app's own origin is the source.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    ls = subprocess.run(
        ["git", "-C", str(repo), "ls-remote", "--heads", "origin", "deploy-*"],
        capture_output=True,
        text=True,
    )
    assert ls.returncode == 0, (
        f"{deploy_repo}: cannot enumerate origin heads: {ls.stderr.strip()}"
    )
    branches = [
        line.split("refs/heads/", 1)[1]
        for line in ls.stdout.splitlines()
        if "refs/heads/" in line
    ]
    assert branches, (
        f"{deploy_repo}: origin carries no deploy-* branch "
        "(ADR-032, the orphan branch ArgoCD watches)"
    )
    for branch in branches:
        fetch = subprocess.run(
            ["git", "-C", str(repo), "fetch", "--quiet", "origin", branch],
            capture_output=True,
            text=True,
        )
        assert fetch.returncode == 0, (
            f"{deploy_repo}: no {branch!r} branch on origin "
            f"(ADR-032, the orphan branch ArgoCD watches): {fetch.stderr.strip()}"
        )
        missing = [
            rel
            for rel in ("dev/values.yaml", "dev/Chart.yaml", "prod/values.yaml", "prod/Chart.yaml")
            if subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"FETCH_HEAD:{rel}"],
                capture_output=True,
            ).returncode
            != 0
        ]
        assert not missing, (
            f"{deploy_repo}: {branch} branch missing {missing} "
            "(ADR-025: dev/ and prod/, each a values.yaml and a Chart.yaml pinning the published chart)"
        )


@intent("INT-FOUNDRY-022")
def test_docs_ships_starlight_bundle(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-022: <repo>/docs/intent.yaml ships the STARLIGHT intent bundle.

    The docs site is scaffolded by bootstrap-starlight, which auto-applies the
    STARLIGHT intent bundle so the page frontmatter is attested by vitest-intent
    meta-tests. A docs/ that drops the bundle (--no-intent) stops attesting its
    frontmatter; asserting docs/intent.yaml carries an INT-STARLIGHT- claim keeps the
    per-app docs attestation consistent rather than silently dropped (ADR-018 C/E).
    """
    repo = _repo_dir(repo_root, deploy_repo)
    docs_intent = repo / "docs" / "intent.yaml"
    assert docs_intent.is_file(), (
        f"{deploy_repo}: missing docs/intent.yaml (the STARLIGHT intent bundle; "
        "bootstrap-starlight ships it by default — do not scaffold with --no-intent)"
    )
    assert "INT-STARLIGHT-" in docs_intent.read_text(encoding="utf-8"), (
        f"{deploy_repo}: docs/intent.yaml declares no INT-STARLIGHT- claim "
        "(the STARLIGHT bundle attests docs frontmatter; an intent.yaml without it is "
        "not the bundle)"
    )


@intent("INT-FOUNDRY-029")
def test_configmap_consumers_carry_checksum_annotation(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-029: a Deployment consuming a ConfigMap via envFrom carries checksum/config.

    Env vars from envFrom are read only at container start and Kubernetes never
    restarts pods on a ConfigMap change, so without a checksum/config pod-template
    annotation a config-only change syncs as a silent no-op: ArgoCD applies the new
    ConfigMap while the running pods keep the old values until the next image roll
    (REF-Homelab section 4). Helm templates are not parseable YAML, so the check is
    textual per template document: any 'kind: Deployment' doc naming a configMapRef
    under envFrom must also carry checksum/config. Deployments with no envFrom
    ConfigMap reference (the static docs servers) hold vacuously.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    templates = repo / "k8s" / "templates"
    assert templates.is_dir(), f"{deploy_repo}: missing k8s/templates/ (INT-FOUNDRY-005)"
    offenders: list[str] = []
    for tpl in sorted((*templates.rglob("*.yaml"), *templates.rglob("*.yml"))):
        for doc in re.split(
            r"^---\s*$", _sans_comments(tpl.read_text(encoding="utf-8")), flags=re.MULTILINE
        ):
            if "kind: Deployment" not in doc:
                continue
            if "envFrom" not in doc or "configMapRef" not in doc:
                # Consumes no ConfigMap at container start: holds vacuously.
                continue
            if "checksum/config" not in doc:
                offenders.append(tpl.relative_to(repo).as_posix())
    assert not offenders, (
        f"{deploy_repo}: Deployment(s) consume a ConfigMap via envFrom without a "
        f"checksum/config pod-template annotation, so a config-only change never rolls "
        f"the pods that read it: {offenders} (REF-Homelab section 4)"
    )


@intent("INT-FOUNDRY-030")
def test_workloads_honor_paused(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-030: every non-docs workload renders its replicas from .Values.paused.

    A single node has finite capacity, so an onboarded app is switchable off
    without being offboarded: the platform scales it to zero by overriding
    .Values.paused on the app's ArgoCD Application (ADR-030). For that override
    to bite, the app chart must declare a top-level `paused` value (default
    false) and every non-docs workload must render its replica count from it.
    The docs Deployment is excepted: documentation stays served while an app is
    paused, so it holds vacuously (REF-Homelab section 4).
    """
    repo = _repo_dir(repo_root, deploy_repo)
    values = repo / "k8s" / "values.yaml"
    assert values.is_file(), f"{deploy_repo}: missing k8s/values.yaml (INT-FOUNDRY-005)"
    loaded = yaml.safe_load(values.read_text(encoding="utf-8")) or {}
    assert "paused" in loaded, (
        f"{deploy_repo}: k8s/values.yaml declares no top-level `paused` default (REF-Homelab section 4)"
    )
    templates = repo / "k8s" / "templates"
    assert templates.is_dir(), f"{deploy_repo}: missing k8s/templates/ (INT-FOUNDRY-005)"
    offenders: list[str] = []
    for tpl in sorted((*templates.rglob("*.yaml"), *templates.rglob("*.yml"))):
        for doc in re.split(
            r"^---\s*$", _sans_comments(tpl.read_text(encoding="utf-8")), flags=re.MULTILINE
        ):
            if "kind: Deployment" not in doc:
                continue
            if _is_docs_component(doc):
                # The docs server stays running while the app is paused: vacuous hold.
                continue
            if ".Values.paused" not in doc:
                offenders.append(tpl.relative_to(repo).as_posix())
    assert not offenders, (
        f"{deploy_repo}: non-docs Deployment(s) do not render replicas from .Values.paused, "
        f"so the platform cannot scale the app to zero: {offenders} (REF-Homelab section 4)"
    )


@intent("INT-FOUNDRY-023")
def test_auth_existing_secret_has_example(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-023: a non-empty auth.existingSecret ships k8s/secrets/<name>.env.example.

    A fail-closed app names its auth Secret via auth.existingSecret and serves nothing
    until that Secret exists; the Secret is provisioned out of band (ADR-017) from a
    git-ignored env file, so the committed k8s/secrets/<name>.env.example is the
    discoverable contract. The claim holds vacuously when auth.existingSecret is unset
    (an app gated elsewhere, or one keying its Secret differently, e.g. secretName).
    """
    repo = _repo_dir(repo_root, deploy_repo)
    values = repo / "k8s" / "values.yaml"
    assert values.is_file(), f"{deploy_repo}: missing k8s/values.yaml (INT-FOUNDRY-005)"
    data = yaml.safe_load(values.read_text(encoding="utf-8")) or {}
    secret_name = ((data.get("auth") or {}).get("existingSecret") or "").strip()
    if not secret_name:
        # No fail-closed auth.existingSecret reference: nothing to provision, claim holds vacuously.
        return
    example = repo / "k8s" / "secrets" / f"{secret_name}.env.example"
    assert example.is_file(), (
        f"{deploy_repo}: k8s/values.yaml sets auth.existingSecret={secret_name!r} but ships no "
        f"k8s/secrets/{secret_name}.env.example (the out-of-band credential template; ADR-017). "
        "A fail-closed app with a named-but-undeclared Secret serves nothing once deployed."
    )


@intent("INT-FOUNDRY-039")
def test_app_never_provides_packages_token(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-039: no app workflow provides an npm registry token itself.

    The token counterpart of the build thin-caller rule (INT-FOUNDRY-013). The
    @psa-department-of-engineering packages are on the public npm registry (csd-library
    ADR-0003), so installing them is anonymous and there is no credential to wire at all.
    Asserting the single invariant - the app does not set the token variable - rather than
    banning particular spellings catches every hand-wiring (a bare secrets.NODE_AUTH_TOKEN,
    a re-typed PACKAGES_READ_TOKEN || github.token fallback, or any future variant), the
    way a delivery once hand-wrote a NODE_AUTH_TOKEN nothing created and failed closed.
    Since the move off GitHub Packages it catches one more thing: the stale auth wiring a
    repo carried from that era, which now fails against a registry that never asked for it
    (REF-Foundry section 4).
    """
    repo = _repo_dir(repo_root, deploy_repo)
    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return  # no workflows: holds vacuously
    offenders = [
        wf.name
        for wf in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml")))
        # Comment-stripped, per the rule this file already learned twice
        # (_sans_comments): a heuristic must read code, never commentary. A workflow
        # documenting that it assigns no NODE_AUTH_TOKEN names the variable followed
        # by a colon, which is indistinguishable from an assignment to the regex.
        if re.search(r"NODE_AUTH_TOKEN\s*[:=]", _sans_comments(wf.read_text(encoding="utf-8")))
    ]
    assert not offenders, (
        f"{deploy_repo}: workflow(s) assign an npm registry token themselves "
        f"(NODE_AUTH_TOKEN): {offenders}. The @psa-department-of-engineering packages "
        "are on the public npm registry, so there is no credential to assign - delete "
        "the assignment rather than wiring a secret to it (REF-Foundry section 4)."
    )


# --- chart delivery-wiring guards (ADR-034 era; INT-FOUNDRY-045..047) -------
# Three defect classes shipped by autonomous deliveries and only discovered
# live (ImagePullBackOff, an unregistered hostname, an unpinned sidecar):
# each is now a repo-CI failure instead. Text-level checks in the style of the
# other chart tests: templates are Helm text (not YAML-parseable), values.yaml
# is parseable and carries the defaults the checks pin.


def _chart_dir(repo: Path) -> Path:
    return repo / "k8s"


def _template_texts(repo: Path) -> dict[str, str]:
    """Template texts with comments stripped at the read boundary (issue #7),
    so every downstream substring heuristic judges code alone."""
    tdir = _chart_dir(repo) / "templates"
    if not tdir.is_dir():
        return {}
    return {p.name: _sans_comments(p.read_text(encoding="utf-8")) for p in sorted(tdir.glob("*.yaml"))}


def _values(repo: Path) -> dict:
    path = _chart_dir(repo) / "values.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _flat_items(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flat_items(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _flat_items(value, f"{path}[{i}]")
    else:
        yield path, node


@intent("INT-FOUNDRY-045")
def test_chart_workloads_reference_the_pull_secret(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-045: every Deployment template mounts imagePullSecrets.

    The fleet's images are private GHCR; network/ghcr-pull auto-reflects into
    every namespace (INT-FOUNDRY-044), but a pod only presents it if the chart
    references it. A chart without imagePullSecrets pulls anonymously and lands
    in ImagePullBackOff on first deploy (token-racing-track, shattered-catacombs),
    a failure only visible live. Holds vacuously for repos without a chart.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    offenders = [
        name
        for name, text in _template_texts(repo).items()
        if "kind: Deployment" in text and "imagePullSecrets" not in text
    ]
    assert not offenders, (
        f"{deploy_repo}: Deployment templates without imagePullSecrets (pods pull "
        f"anonymously and ImagePullBackOff on private GHCR): {offenders}. Reference "
        "the values-driven list (default: [name: ghcr-pull])."
    )


@intent("INT-FOUNDRY-048")
def test_docs_deployment_labels_pod_for_homepage_tile(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-048: the docs Deployment labels its pod app.kubernetes.io/name: <chart>-docs.

    An app's homepage tile (the routes in its teams/<app>/ folder on the
    install's deploy branch) hangs off the app's <app>-docs docs HTTPRoute,
    and Homepage (gethomepage.dev) derives the tile's
    pod-status selector from that route NAME: it looks for a pod labelled
    app.kubernetes.io/name=<app>-docs in the app namespace. So the docs
    Deployment's pod template must carry that -docs-suffixed name label, or the
    pod is never found and the tile renders "NOT FOUND" though the docs are
    healthy. Three deliveries shipped the bug (falarsemdizernada and
    shattered-catacombs took the bare chart name from the shared `labels` helper,
    token-racing-track from `selectorLabels`); the fleet convention is inline
    `app.kubernetes.io/name: {{ .Chart.Name }}-docs`. Holds vacuously for repos
    with no docs Deployment.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    name_docs = re.compile(r"app\.kubernetes\.io/name:.*-docs")
    offenders: list[str] = []
    for fname, text in _template_texts(repo).items():
        for doc in re.split(r"^---\s*$", text, flags=re.MULTILINE):
            if "kind: Deployment" not in doc:
                continue
            # The docs server, by the same heuristic INT-FOUNDRY-030 uses to except it.
            if not _is_docs_component(doc):
                continue
            if not name_docs.search(doc):
                offenders.append(fname)
    assert not offenders, (
        f"{deploy_repo}: docs Deployment(s) do not label their pod "
        f"app.kubernetes.io/name: <chart>-docs, so the app's <app>-docs homepage "
        f"tile can't find the pod and renders NOT FOUND though the docs are healthy: "
        f"{offenders}. Set the name label inline to <chart>-docs (do not take the "
        "bare chart name from the shared labels/selectorLabels helper)."
    )


@intent("INT-FOUNDRY-056")
def test_docs_deployment_ships_the_route_derived_docs_service(
    repo_root: Path, deploy_repo: str
) -> None:
    """INT-FOUNDRY-056: a chart with a docs Deployment ships the docs Service the
    docs HTTPRoute's derived backendRef actually targets.

    The platform's teams-skeleton docs route (rendered at onboarding) DERIVES its
    backendRef rather than discovering it (platform-studio docs_service_name()):
    <chart>-docs, or svc-<chart>-docs when the chart name starts with a digit,
    because Service names are DNS-1035 labels (they must start with a letter)
    while a chart name may legally lead with a digit. The chart must create
    exactly that Service or the route forwards to nothing: 2ez4tv shipped a docs
    Deployment while its route pointed at "2ez4tv-docs" -- a Service that did not
    exist and whose name no Service could legally carry -- and no pre-flight row
    went red; the docs 404'd only live. Templates are Helm text, so the check is
    textual per template document: the metadata name may spell the chart name
    literally, as {{ .Chart.Name }}, or via the fleet's {{ include
    "<chart>.name" . }} helper (which resolves to the chart name); any other
    expression stays unresolved and fails loud. Holds vacuously for repos with
    no docs Deployment.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    templates = _template_texts(repo)

    def _docs_component_docs(kind: str) -> list[tuple[str, str]]:
        # The docs server's documents, by the same heuristic INT-FOUNDRY-030/048 use.
        found: list[tuple[str, str]] = []
        for fname, text in templates.items():
            for doc in re.split(r"^---\s*$", text, flags=re.MULTILINE):
                if f"kind: {kind}" not in doc:
                    continue
                if not _is_docs_component(doc):
                    continue
                found.append((fname, doc))
        return found

    if not _docs_component_docs("Deployment"):
        # No docs Deployment: no docs route backend to satisfy, holds vacuously.
        return

    chart_file = _chart_dir(repo) / "Chart.yaml"
    assert chart_file.is_file(), f"{deploy_repo}: missing k8s/Chart.yaml (INT-FOUNDRY-005)"
    chart = str((yaml.safe_load(chart_file.read_text(encoding="utf-8")) or {}).get("name") or "")
    assert chart, f"{deploy_repo}: k8s/Chart.yaml declares no chart name"
    expected = f"svc-{chart}-docs" if chart[0].isdigit() else f"{chart}-docs"

    services = _docs_component_docs("Service")
    assert services, (
        f"{deploy_repo}: the chart ships a docs Deployment but no docs Service; the "
        f"platform's docs HTTPRoute forwards to the derived backendRef {expected!r} "
        "and finds nothing, so the docs 404 though the pod is healthy (the 2ez4tv "
        "failure shape)."
    )

    dns1035 = re.compile(r"[a-z]([-a-z0-9]*[a-z0-9])?")
    offenders: list[str] = []
    for fname, doc in services:
        m = re.search(
            r"^metadata:\n(?:[ \t]+\S.*\n)*?[ \t]+name:[ \t]*(\S.*?)[ \t]*$",
            doc,
            flags=re.MULTILINE,
        )
        name = m.group(1) if m else "<no metadata.name>"
        name = re.sub(r"\{\{-?\s*\.Chart\.Name\s*-?\}\}", chart, name)
        name = re.sub(
            r"\{\{-?\s*include\s+\"" + re.escape(chart) + r"\.name\"\s+\.\s*-?\}\}", chart, name
        )
        name = name.strip("\"'")
        if name != expected or not dns1035.fullmatch(name) or len(name) > 63:
            offenders.append(f"{fname}: {name}")
    assert not offenders, (
        f"{deploy_repo}: docs Service name(s) do not match the derived backendRef "
        f"{expected!r} as a valid DNS-1035 label (starts with a letter; svc- prefix "
        f"exactly when the chart name leads with a digit): {offenders}. The docs "
        "HTTPRoute targets the derivation, not the chart's choice, so any other "
        "name 404s the docs live."
    )


def _parent_ref_names(text: str) -> list[str]:
    """Every parentRef `name:` in a Helm HTTPRoute template, by indentation.

    Helm templates are not parseable YAML, so the parentRefs block is taken as
    the lines indented deeper than the `parentRefs:` key; `name:` entries in it
    are the referenced Gateways (backendRefs live in their own block and are
    never collected, and `namespace:` does not match the `name:` prefix).
    """
    names: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([ \t]*)parentRefs:\s*$", line)
        if not m:
            continue
        indent = len(m.group(1))
        for follow in lines[i + 1 :]:
            if follow.strip() and (len(follow) - len(follow.lstrip())) <= indent:
                break
            stripped = follow.strip().lstrip("- ").strip()
            if stripped.startswith("name:"):
                names.append(stripped.split(":", 1)[1].strip().strip("\"'"))
    return names


@intent("INT-FOUNDRY-046")
def test_chart_routes_bind_only_the_apps_own_gateway(
    repo_root: Path, deploy_repo: str
) -> None:
    """INT-FOUNDRY-046: every chart HTTPRoute attaches to the app's own Gateway
    (parentRef name = the app, namespace = network) and the chart carries the
    app hostname.

    The app's own per-app Gateway is the ONLY valid parent for any route the
    chart ships, so the check demands name-equals-app instead of blocking known
    bad names: an invented gateway (token-racing-track's homelab-gateway) and a
    platform-owned parent are both rejected by the same rule. A route whose
    parent is a platform Gateway (the docs host's, say) belongs to the install's
    teams/<app>/ folder, never the chart: a chart copy makes two ArgoCD
    Applications claim one resource, and the shared-resource conflict wedges the
    install's whole tenants sync (task-api/alexandria-main shipped exactly
    that). external-dns registers DNS from HTTPRoutes (gateway-httproute
    source), so a route with no hostname registers nothing, and a parentRef
    that omits `namespace: network` (defaulting to the app namespace, where no
    Gateway lives) is never Accepted (shattered-catacombs). Holds vacuously
    without a chart or when no template declares an HTTPRoute.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    templates = _template_texts(repo)
    routes = {n: t for n, t in templates.items() if "kind: HTTPRoute" in t}
    if not routes:
        return

    values = _values(repo)

    def _resolves_to_app(ref: str) -> bool:
        """A parentRef names the app's own Gateway: literally, via the chart's
        own name (.Chart.Name/.Release.Name, which Helm renders as the app), or
        via a values path whose default value IS the app name."""
        if ref == deploy_repo or ".Chart.Name" in ref or ".Release.Name" in ref:
            return True
        m = re.search(r"\.Values\.([A-Za-z0-9_.]+)", ref)
        if not m:
            return False
        node = values
        for key in m.group(1).split("."):
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return node == deploy_repo

    offenders: list[str] = []
    for name, text in routes.items():
        refs = _parent_ref_names(text)
        if not refs:
            offenders.append(f"{name}: HTTPRoute with no parentRef name")
        for ref in refs:
            if not _resolves_to_app(ref):
                offenders.append(
                    f"{name}: parentRef {ref!r} does not resolve to the app's own Gateway "
                    f"(expected {deploy_repo!r}); a platform-owned route lives in "
                    "teams/<app>/ on the install branch, never in the chart"
                )
    assert not offenders, (
        f"{deploy_repo}: chart HTTPRoute(s) bind a Gateway that is not the app's own:\n  "
        + "\n  ".join(offenders)
    )

    all_text = "\n".join(routes.values())
    assert "hostnames:" in all_text, (
        f"{deploy_repo}: no HTTPRoute template carries hostnames; external-dns "
        "registers DNS from the route's hostname, so the app host stays NXDOMAIN."
    )

    flat = dict(_flat_items(_values(repo)))
    host_keys = {k for k in flat if "host" in k.lower()}
    # The app parameterizes its hostname through a `host` value so the route can
    # carry it. A zone is NOT hardcoded in this shared conformance check (that would
    # bake one install's zone into every app, anti-pattern 19): a legacy app may
    # default a zoned host, an install-agnostic app defaults it empty and sets the
    # per-install host on its deploy branch's prod/values.yaml. Either way the chart
    # must expose the key.
    assert host_keys, (
        f"{deploy_repo}: values.yaml declares no host key for the app route to carry "
        "(the chart must parameterize the hostname: a default for a single-install app, "
        "or empty and set per-install on the deploy branch for an install-agnostic one)."
    )
    gateway_ns = {
        v
        for k, v in flat.items()
        if isinstance(v, str) and "gateway" in k.lower() and k.lower().endswith("namespace")
    }
    has_literal_network = any(
        re.search(r"parentRefs:(?:\n.+)+?namespace:\s*network\b", t) for t in routes.values()
    )
    assert "network" in gateway_ns or has_literal_network, (
        f"{deploy_repo}: the app route's parentRef must pin namespace 'network' "
        "(values-driven gateway namespace or literal); without it the ref defaults "
        "to the app namespace, no Gateway accepts the route, and DNS never registers."
    )


@intent("INT-FOUNDRY-047")
def test_chart_pins_third_party_images(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-047: no third-party image reference floats on :latest.

    First-party images may default to :latest in the chart (the deploy branch
    pins the served tag and Image Updater maintains it: that split of
    repository + tag values is the fleet convention). A THIRD-PARTY sidecar,
    though, is referenced as one full image string the deploy values never pin,
    so :latest there (an oauth2-proxy) upgrades itself on its own schedule and
    cannot be rolled back to a known version; it shipped live on
    token-racing-track. Full image refs, in values or literal in templates,
    must carry a real tag.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    offenders: list[str] = []
    for key, value in _flat_items(_values(repo)):
        if isinstance(value, str) and "/" in value and value.endswith(":latest"):
            offenders.append(f"values.yaml: {key}={value}")
    for name, text in _template_texts(repo).items():
        for match in re.finditer(r"image:\s*\"?([^\s\"{]*/[^\s\"{]*:latest)\b", text):
            offenders.append(f"{name}: {match.group(1)}")
    assert not offenders, (
        f"{deploy_repo}: third-party image references floating on :latest "
        f"(self-upgrading, un-rollbackable; pin a version): {offenders}"
    )


@intent("INT-FOUNDRY-057")
def test_oauth2_proxy_provider_and_api_route_flag(
    repo_root: Path, deploy_repo: str
) -> None:
    """INT-FOUNDRY-057: an oauth2-proxy sidecar uses --provider=oidc and --api-route.

    keycloak-oidc audience-checks the access token, whose Keycloak default aud is
    'account' not the client, so every login callback 500s; the generic oidc
    provider validates the ID token (aud = client-id). The routes flag is the
    singular --api-route; the pluralized --api-routes is an unknown flag that
    crash-loops the proxy. Both shipped live (Alexandria, token-racing-track); a
    chart with no oauth2-proxy holds vacuously. Matched on the argument line, so a
    comment naming the wrong flag does not itself trip the check.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    offenders: list[str] = []
    for name, text in _template_texts(repo).items():
        if re.search(r"^\s*-\s*--provider=keycloak-oidc", text, re.MULTILINE):
            offenders.append(f"{name}: --provider=keycloak-oidc (use --provider=oidc)")
        if re.search(r"^\s*-\s*--api-routes", text, re.MULTILINE):
            offenders.append(f"{name}: --api-routes (use the singular --api-route)")
    assert not offenders, (
        f"{deploy_repo}: oauth2-proxy misconfiguration that fails only at login: "
        f"{offenders}"
    )


# --- identity binding guards (chart side of the realm-per-app contract) ------
# The install-side identity claims (realm named for the app, scoped admin,
# app-owned credential) live in tests/test_identity_isolation.py and read the
# deploy branches; these two read the app chart, so they live with the other
# sibling-clone checks. task-api shipped its chart bound to alexandria-main's
# realm: the values parsed, the sidecar ran, and the pod could never start,
# because a foreign realm's Secret never reflects into the app's namespace.
# The binding checks make that shape a repo-CI failure instead.


@intent("INT-FOUNDRY-034")
def test_app_oidc_gate_binds_the_apps_own_realm(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-034: every identity reference in an app chart is the app's OWN.

    Realm-per-app is enforced physically by the credential side: keycloak-<app>
    is minted in the app's own namespace by its grant, and a foreign app's
    Secret never reflects there, so a chart bound to another app's realm waits
    forever on a Secret that never arrives. Three layers, each engaging on the
    shape actually present in the chart: (1) no chart text names another app's
    keycloak-* artifact, whatever the values shape (task-api shipped bound to
    alexandria-main's realm; the old check asserted only that SOME secret was
    wired, so the borrow passed); (2) a values auth.oidc block that names its
    bindings names the app's own (existingSecret keycloak-<app>, clientId
    <app>, a literal issuer ending /realms/<app>); (3) a chart that ships an
    oauth2-proxy sidecar has a Service targeting it, or the gate is bypassed.
    Holds vacuously for charts with no identity surface.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    templates = _template_texts(repo)
    values = _values(repo)
    values_path = repo / "k8s" / "values.yaml"
    values_text = (
        _sans_comments(values_path.read_text(encoding="utf-8")) if values_path.is_file() else ""
    )
    chart_text = "\n".join([values_text, *templates.values()])

    own_secret = f"keycloak-{deploy_repo}"
    # keycloak-oidc is oauth2-proxy's provider identifier, an argument-line value
    # (the misconfiguration INT-FOUNDRY-057 flags), not an app artifact.
    allowed = {own_secret, f"keycloak-realm-{deploy_repo}", "keycloak-oidc"}
    foreign = sorted(
        {
            m
            for m in re.findall(r"keycloak-[a-z0-9][a-z0-9-]*", chart_text)
            if m not in allowed
        }
    )
    assert not foreign, (
        f"{deploy_repo}: chart names another app's identity artifact(s) {foreign}; "
        "every identity reference in an app chart is the app's own (realm-per-app): "
        "a foreign realm's Secret never reflects into this namespace, so the pod "
        "would wait on it forever"
    )

    oidc = (values.get("auth") or {}).get("oidc") or {}
    secret = str(oidc.get("existingSecret") or "").strip()
    if secret:
        assert secret == own_secret, (
            f"{deploy_repo}: auth.oidc.existingSecret is {secret!r}, not {own_secret!r} "
            "(the realm credential is app-owned, realm-per-app)"
        )
    client_id = str(oidc.get("clientId") or "").strip()
    if client_id:
        assert client_id == deploy_repo, (
            f"{deploy_repo}: auth.oidc.clientId is {client_id!r}, not {deploy_repo!r} "
            "(the realm's client is named for the app)"
        )
    issuer = str(oidc.get("issuer") or "").strip()
    if issuer and "{{" not in issuer:
        assert issuer.rstrip("/").endswith(f"/realms/{deploy_repo}"), (
            f"{deploy_repo}: auth.oidc.issuer {issuer!r} does not end in /realms/{deploy_repo} "
            "(realm-per-app: an app authenticates against its own realm)"
        )

    proxy_templates = {
        n: t for n, t in templates.items() if "oauth2-proxy" in t and "kind: Deployment" in t
    }
    if proxy_templates:
        service_texts = [t for t in templates.values() if "kind: Service" in t]
        assert any("oidc" in t or "oauth2Proxy" in t for t in service_texts), (
            f"{deploy_repo}: an oauth2-proxy sidecar ships but no Service targets it; "
            "traffic that bypasses the proxy bypasses the gate"
        )


def _secret_ref_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Every secretRef/secretKeyRef block, as (line number, block lines).

    A block is the reference line plus the more-indented lines under it, which
    is where `name:` and `optional:` both live. Helm templates are not
    parseable YAML, so this reads indentation rather than loading a document.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, list[str]]] = []
    for i, line in enumerate(lines):
        if "secretRef:" not in line and "secretKeyRef:" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        body = [line]
        for nxt in lines[i + 1 :]:
            if not nxt.strip():
                body.append(nxt)
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt)
        blocks.append((i + 1, body))
    return blocks


@intent("INT-FOUNDRY-062")
def test_platform_minted_credentials_are_not_optional(
    repo_root: Path, deploy_repo: str
) -> None:
    """INT-FOUNDRY-062: no `optional: true` on a platform-minted credential.

    Fail-closed must mean the pod does not start. `optional: true` inverts it -
    the container starts with the credential absent, so the proxy crash-loops
    and the API dials Postgres with an empty password, and the edge answers an
    opaque 503 instead of the CreateContainerConfigError that would name the
    missing Secret (knight-fight, sixteen hours).

    Scoped to the credentials only a grant can mint: an app-owned Secret an
    operator applies out of band may legitimately be optional. The reference is
    matched by the literal name or by the values path that holds it, since
    charts name it through `.Values`. Vacuous for a chart naming neither.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    values = _values(repo)
    minted = {f"keycloak-{deploy_repo}", f"pg-app-{deploy_repo}"}

    # The values paths whose leaf is one of the minted names, so a template
    # saying `{{ .Values.auth.oidc.existingSecret }}` is recognised as naming it.
    paths: set[str] = set()

    def walk(node: object, trail: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, (*trail, str(key)))
        elif isinstance(node, str) and node in minted:
            paths.add(".".join(trail))

    walk(values, ())
    templates = _template_texts(repo)
    hardcoded = any(m in text for m in minted for text in templates.values())
    if not paths and not hardcoded:
        pytest.skip(f"{deploy_repo}: chart names no platform-minted credential (vacuous)")

    offenders: list[str] = []
    for fname, text in templates.items():
        for lineno, body in _secret_ref_blocks(text):
            blob = "\n".join(body)
            names_minted = any(m in blob for m in minted) or any(p in blob for p in paths)
            if names_minted and re.search(r"optional:\s*true", blob):
                offenders.append(f"{fname}:{lineno}")

    assert not offenders, (
        f"{deploy_repo}: a platform-minted credential is consumed with "
        f"`optional: true` at {offenders}. Fail-closed means the pod does not "
        "start: drop the flag so a missing credential holds the pod at "
        "CreateContainerConfigError, where the cause is legible, instead of "
        "starting a workload that crash-loops behind an opaque 503."
    )


@intent("INT-FOUNDRY-037")
def test_mcp_app_keeps_basic_on_machine_door(repo_root: Path, deploy_repo: str) -> None:
    """INT-FOUNDRY-037: an OIDC app whose oauth2-proxy skips a machine path (/mcp)
    MUST disable header stripping on the skip (--skip-auth-strip-headers=false)
    and scope the app's own gate to the machine door (<APP>_AUTH_SCOPE=mcp).

    The contract binds MACHINE doors, not public-anonymous surfaces: an app may
    legitimately skip public routes (a home page, share cards) with no Basic
    gate anywhere. Only an mcp-looking skip engages the split. Checked across
    every deployment template (charts split their workloads across files).
    Vacuous without the OIDC gate or without a machine-door skip.
    """
    repo = _repo_dir(repo_root, deploy_repo)
    templates = _template_texts(repo)
    values = _values(repo)
    if not ((values.get("auth") or {}).get("oidc") or {}).get("enabled"):
        pytest.skip(f"{deploy_repo}: OIDC gate not enabled (vacuous)")

    proxy_templates = {n: t for n, t in templates.items() if "skip-auth-route" in t}
    if not proxy_templates:
        pytest.skip(f"{deploy_repo}: proxy skips no route (vacuous)")

    for name, text in proxy_templates.items():
        skips = re.findall(r"--skip-auth-route=([^\s\"']+)", text)
        if not any("mcp" in skip.lower() for skip in skips):
            continue
        assert "--skip-auth-strip-headers=false" in text, (
            f"{deploy_repo}: {name}: oauth2-proxy skips /mcp but would strip its auth "
            "header; add --skip-auth-strip-headers=false or the /mcp credential is dropped"
        )
        assert "_AUTH_SCOPE" in text and 'value: "mcp"' in text, (
            f"{deploy_repo}: {name}: the app must scope its own gate to /mcp "
            "(<APP>_AUTH_SCOPE=mcp) so the skipped machine path keeps its own "
            "credential rather than serving open"
        )


# --- the reconcile check (INT-FOUNDRY-061): the causer's-desk drift brake ----
# The Image Updater aliases on the install branch are a frozen declaration;
# what the repo releases is a moving truth. Their drift silently stops
# promotion (no crash, no red status: the alexandria shape), and it is caused
# by app-side changes, so the brake lives HERE, in the app's own CI, blocking
# the release until reconcile_app realigns the declaration. The platform's
# standing report never fails on it. This is the suite's one deliberate
# platform-facing read (see conftest): read-only, and unarmed it reports a
# named skip rather than a silent pass.

_IMAGE_LIST_PREFIX = "argocd-image-updater.argoproj.io/image-list"


def _platform_read(path: str) -> bytes | None:
    """One read-only GET against the platform repo's contents, or None."""
    import urllib.error
    import urllib.request

    token = os.environ.get("PLATFORM_READ_TOKEN", "").strip()
    repo = os.environ.get("PLATFORM_REPO", "").strip()
    if not token or not repo:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()
    except (urllib.error.URLError, OSError):
        return None


def _platform_deploy_branches() -> list[str] | None:
    """The platform repo's deploy-* branch names, or None when unreadable."""
    import json

    raw = _platform_read("branches?per_page=100")
    if raw is None:
        return None
    try:
        branches = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(branches, list):
        return None
    return sorted(
        str(b.get("name"))
        for b in branches
        if isinstance(b, dict) and str(b.get("name") or "").startswith("deploy-")
    )


def _local_released_set(repo: Path, app: str) -> set[str] | None:
    """The GHCR packages this checkout's CI publishes: the compose build:
    services (named by image: basename) plus <app>-docs when docs/Dockerfile
    exists. None when the compose cannot be read (INT-FOUNDRY-003 owns that
    failure; this check does not double-report it)."""
    compose_path = repo / "devops" / "docker-compose.yml"
    if not compose_path.is_file():
        return None
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(compose, dict):
        return None
    released: set[str] = set()
    services = compose.get("services")
    if isinstance(services, dict):
        for name, service in services.items():
            if not isinstance(service, dict) or not service.get("build"):
                continue
            image = str(service.get("image") or name)
            released.add(image.split("/")[-1].split(":")[0].lower())
    if (repo / "docs" / "Dockerfile").is_file():
        released.add(f"{app}-docs")
    return released


def _watched_packages(application_text: str) -> set[str] | None:
    """The package basenames the Application's image-list aliases watch."""
    watched: set[str] = set()
    try:
        documents = list(yaml.safe_load_all(application_text))
    except yaml.YAMLError:
        return None
    for doc in documents:
        if not isinstance(doc, dict) or doc.get("kind") != "Application":
            continue
        annotations = (doc.get("metadata") or {}).get("annotations") or {}
        image_list = str(annotations.get(_IMAGE_LIST_PREFIX) or "")
        for entry in image_list.split(","):
            entry = entry.strip()
            if "=" not in entry:
                continue
            image = entry.split("=", 1)[1]
            watched.add(image.split("/")[-1].split(":")[0].lower())
    return watched or None


@intent("INT-FOUNDRY-061")
def test_released_set_reconciled_with_the_platform(
    repo_root: Path, deploy_repo: str
) -> None:
    """INT-FOUNDRY-061: what this repo releases matches the promotion record
    every declaring install carries for it. Growing the released set past the
    record is an intended interim state exactly once: between this failure and
    the reconcile_app that realigns the declaration - reconcile FIRST, then
    merge the compose change, and this check is green on arrival. Not
    onboarded anywhere holds vacuously (the first release must ship before
    onboarding can pin it); an unreadable platform record is a named skip."""
    repo = _repo_dir(repo_root, deploy_repo)
    released = _local_released_set(repo, deploy_repo)
    if released is None:
        pytest.skip(
            f"{deploy_repo}: devops/docker-compose.yml unreadable; the released "
            "set has no source (INT-FOUNDRY-003 owns that failure)"
        )
    branches = _platform_deploy_branches()
    if branches is None:
        pytest.skip(
            f"{deploy_repo}: platform record unreadable (PLATFORM_REPO / "
            "PLATFORM_READ_TOKEN unset, or the host did not answer); the "
            "reconcile check is unarmed - a named skip, never a silent pass"
        )
    declared_anywhere = False
    problems: list[str] = []
    for branch in branches:
        manifest_raw = _platform_read(f"contents/foundry.yaml?ref={branch}")
        if manifest_raw is None:
            continue
        try:
            manifest = yaml.safe_load(manifest_raw) or {}
        except yaml.YAMLError:
            continue
        names = {
            str(entry.get("name"))
            for role in ("parts", "output")
            for entry in (manifest.get(role) or [])
            if isinstance(entry, dict)
        }
        if deploy_repo not in names:
            continue
        declared_anywhere = True
        application_raw = _platform_read(
            f"contents/teams/{deploy_repo}/application.yaml?ref={branch}"
        )
        if application_raw is None:
            problems.append(
                f"{branch}: declares {deploy_repo} but its "
                f"teams/{deploy_repo}/application.yaml is unreadable"
            )
            continue
        watched = _watched_packages(application_raw.decode("utf-8", "replace"))
        if watched is None:
            problems.append(
                f"{branch}: {deploy_repo}'s Application carries no readable "
                "image-list; nothing promotes it"
            )
            continue
        unwatched = sorted(released - watched)
        phantom = sorted(watched - released)
        if unwatched or phantom:
            problems.append(
                f"{branch}: released {sorted(released)} vs watched {sorted(watched)}"
                + (f"; released but unwatched (never promotes): {unwatched}" if unwatched else "")
                + (f"; watched but not released (phantom alias): {phantom}" if phantom else "")
            )
    if not declared_anywhere:
        return  # not onboarded: nothing to reconcile with, holds vacuously
    assert not problems, (
        f"{deploy_repo}: the released set is out of step with the platform's "
        "promotion record; a released image no alias watches pins its first "
        "tag forever with no crash and no red status. Align with a platform "
        "manager and run the studio's reconcile_app (fresh image_components), "
        "then re-run this build:\n  " + "\n  ".join(problems)
    )
