# ci

Shared, reusable GitHub Actions workflows for `PSA-Department-of-Engineering` repos. A repo calls one of these with a thin `.github/workflows/*.yml`, so the build and release logic lives here once and cannot drift across repos.

This repo is the **GitHub Actions distribution of the platform's CI contract**, named in the platform book: `/capabilities/delivery/#the-ci-contract` on the docs portal (`docs/src/content/docs/capabilities/delivery.md` in `foundry-platform`). A second CI is authored against that contract, never generated from these files, and proven by the same question: does a repo on this CI publish the three artifacts and a tag.

## Workflows

### `build.yml`: deployable services

For a repo that ships a deployable artifact (app image(s), Helm chart, docs site). On push to `main`, semantic-release cuts a single repo-wide SemVer `vX.Y.Z` from Conventional Commits automatically (no PR: promotion is the real gate, per the homelab platform's ADR-024), then builds and pushes whatever the repo ships to GHCR. Each step skips when its input is absent, so a docs-only repo and a full app share one workflow.

Before the release runs, a `conformance` job audits the repo against the REF-Foundry section 4 app contract (`devops/`, `k8s/`, a `docs/` site that honors `DOCS_BASE`, the README sections, `.env.example`, `intent.yaml`) and runs `csd-intent` on its `intent.yaml`. The release `needs` it, so a non-conformant app is never tagged or published: this is where the platform's per-app repo-contract tests run, in the app's own CI where its files are checked out. The docs-only platform portal is not an onboardable app and opts out with `app-contract: false`.

Caller:

```yaml
on:
  push:
    branches: [main]
jobs:
  build:
    uses: PSA-Department-of-Engineering/ci/.github/workflows/build.yml@main
    permissions:
      contents: write
      packages: write
    secrets: inherit
```

Inputs: `runner` (default `arc-dind`), `image-name` (default repo name), `docs-base` (default `/apps/<image-name>`; the platform portal passes `/`), `app-contract` (default `true`; the docs-only platform portal passes `false`).

### `library.yml`: library monorepos

For a repo that publishes one or more reusable packages (npm and/or Python) rather than a deployment. On push to `main`, release-please maintains a per-package release PR from Conventional Commits; merging it tags `<pkg>-vX.Y.Z`, creates the GitHub release + CHANGELOG, and publishes any released npm package to GitHub Packages. Python packages have no publish step: the tag is the release, and consumers `pip install` it. Used by `csd-library` (see its ADR-0002).

The calling repo must contain `release-please-config.json` and `.release-please-manifest.json` at its root (the package map and current versions).

Caller:

```yaml
on:
  push:
    branches: [main]
jobs:
  release:
    uses: PSA-Department-of-Engineering/ci/.github/workflows/library.yml@main
    permissions:
      contents: write
      pull-requests: write
      packages: write
    secrets: inherit
```

Inputs: `node-version` (default `22`), `npm-scope` (default `@psa-department-of-engineering`), `runner` (default `ubuntu-latest`).

One-time repo settings the caller needs: **Settings → Actions → General → Allow GitHub Actions to create and approve pull requests** (so release-please can open the release PR), and a branch-protection rule on `main` that marks the caller's `tests` checks required (so a release PR cannot merge red).

## Conventions

- Both workflows enforce the platform's commit convention before releasing, via the shared `check-commit-hygiene` composite over every commit a push introduced; the convention itself (the admitted types, what each releases, the no-trailer rule) is documented once, on the platform portal's GitOps delivery page under "Commit convention". The `types` list in the composite is its source.
- In a monorepo, scope the commit to the package (`fix(csd-intent): …`) so only that package's version moves.
- Both workflows also run a **secrets gate** before releasing, via the shared `secrets-scan` composite: [gitleaks](https://github.com/gitleaks/gitleaks) scans the commits a push introduced and fails the run when one adds a credential, so a leaked secret is never tagged, published, or built into an image. It needs no per-repo config; a repo with a false positive drops a root `.gitleaks.toml` (`[extend] useDefault = true` plus an `allowlists` entry) to suppress it. The gitleaks version is pinned in the composite and moves for every workflow at once.
- Pin callers to `@main`; these workflows are versioned by this repo's history.
