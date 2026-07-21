# 1. All CI runs on the in-cluster ARC runners, never GitHub-hosted

Date: 2026-07-21
Status: Accepted

## Context

Every repo's `test`/`ci` workflow targeted `runs-on: ubuntu-latest`, deliberately: the
recurring header comment was "runs on GitHub-hosted runners so it does not depend on
homelab uptime", and `library.yml`'s runner input defaulted GitHub-hosted for the same
reason (the release-uptime corollary of csd-library's ADR-0001). Builds, by contrast,
already ran in-cluster on the ARC scale sets (homelab-platform ADR-019).

The org's GitHub Actions billing then ran out, and every GitHub-hosted job was refused
at startup ("recent account payments have failed or your spending limit needs to be
increased"). The uptime-independence bought with GitHub-hosted minutes turned into a
total CI outage — while the ARC runners sat idle, free, and unaffected. Tests that CI
had silently stopped running also accumulated latent debt (lint errors landed on main
unchecked).

## Decision

All CI — tests, audits, e2e, and library releases alike — runs on the self-hosted ARC
runners. Nothing targets a GitHub-hosted runner.

- Org repos (`PSA-Department-of-Engineering/*`) use `runs-on: arc-dind`.
- Personal repos (`rafaelgpires/*`, e.g. homelab-platform) use `runs-on: platform-dind`
  — a separate scale set registered to that account; the labels are not interchangeable.
- `library.yml`'s `runner` input now defaults to `arc-dind`, overriding the
  release-uptime corollary of csd-library ADR-0001 (the package-distribution decision
  itself stands). A caller may still pass a GitHub-hosted runner explicitly.

## Consequences

- No CI consumes GitHub-hosted minutes; the org billing state cannot outage CI again.
- CI now depends on homelab uptime: when k3s is down, runs queue until a runner returns.
  The pools are small (arc-dind maxRunners 4, platform-dind 2), so large matrices queue.
- The runner image is the stock non-root `gha-runner-scale-set` — no `gh`, no `jq`, no
  `python3`, no sudo. Migration rules proven across the org sweep:
  - `actions/setup-python`, `actions/setup-node`, and the `setup-node-auth` composite
    (private `@psa-department-of-engineering` npm included) work on the bare runner —
    ordinary jobs need only the label change.
  - Playwright browser jobs run in `container: mcr.microsoft.com/playwright:v<X>-jammy`
    with `<X>` exactly matching the project's resolved `@playwright/test` (exact-pin the
    dependency where no lockfile is committed), and drop `--with-deps`. A version
    mismatch triggers a browser download that times out through cluster egress.
  - Jobs built on the `gh` CLI must be rewritten to curl + python (see
    homelab-platform's auto-merge.yml and deploy-notify.yml) or run in a container that
    provides their tools.
  - Postgres `services:` blocks work unchanged in dind mode.

## Supersedes

- The "GitHub-hosted so it does not depend on homelab uptime" convention formerly
  stated in each repo's test workflow header.
- The GitHub-hosted default of `library.yml`'s `runner` input (the release-uptime
  corollary of csd-library ADR-0001).
