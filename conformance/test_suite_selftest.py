"""The suite testing its own textual heuristics (issue #7).

The chart checks are deliberately textual, which makes them fakeable by
comments in both directions: a comment carrying a trigger substring can
misclassify a document (the loud failure: wttg3-helper's sites Service failed
INT-FOUNDRY-056 on a comment naming the derived docs backendRef pattern), or
falsely satisfy a content assertion (the silent one: a Deployment passed
INT-FOUNDRY-030 on a comment quoting `.Values.paused` it never rendered).
These are string-level regression cases against the shared helpers, hermetic
by design: they read no checkout, so they run identically in this repo's own
gate and in every app pipeline that executes the suite. Plain pytest, no
intent markers: the INT-FOUNDRY claims are per-app contract claims, and what
is attested here is the suite's own reading of a chart, not any app's
conformance.
"""

from __future__ import annotations

from test_repo_conformance import (
    _generated_trees,
    _is_docs_component,
    _proxy_trust_offenders,
    _sans_comments,
)

# The exact failure shape of 2026-08-01 (wttg3-helper run 30684103353): a
# non-docs Service whose comment mentions the derived "<chart>-docs" pattern.
SITES_SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  # The documentation Service's literal-name rule derives a "<chart>-docs"
  # backendRef; nothing derives a name for this component.
  name: {{ include "myapp.fullname" . }}-sites
  labels:
    app.kubernetes.io/component: sites
"""

# The silent inverse: a Deployment whose only ".Values.paused" is commentary.
UNPAUSED_DEPLOYMENT = """\
kind: Deployment
metadata:
  name: {{ include "myapp.fullname" . }}-sites
spec:
  # This Deployment never reads .Values.paused.
  replicas: 1
"""

DOCS_DEPLOYMENT = """\
kind: Deployment
metadata:
  name: {{ include "myapp.fullname" . }}-docs
  labels:
    app.kubernetes.io/component: docs
spec:
  replicas: 1
"""

PAUSED_DEPLOYMENT = """\
kind: Deployment
spec:
  replicas: {{ if .Values.paused }}0{{ else }}1{{ end }}
"""


def test_sans_comments_drops_full_line_comments_only() -> None:
    text = "kind: Service\n  # a comment\n  name: real\nvalue: 'kept # not a comment'\n"
    stripped = _sans_comments(text)
    assert "# a comment" not in stripped
    assert "name: real" in stripped
    # Trailing-# forms are deliberately out of scope: naive stripping would
    # mangle legitimate values containing '#'.
    assert "kept # not a comment" in stripped


def test_a_comment_never_classifies_a_document_as_docs() -> None:
    # The loud regression: the sites Service reads as NOT docs once comments drop.
    assert not _is_docs_component(_sans_comments(SITES_SERVICE))
    # The real docs component still classifies, by its code.
    assert _is_docs_component(_sans_comments(DOCS_DEPLOYMENT))


def test_a_comment_never_satisfies_a_content_assertion() -> None:
    # The silent regression: commentary quoting the substring is not conformance.
    assert ".Values.paused" not in _sans_comments(UNPAUSED_DEPLOYMENT)
    # The genuinely paused Deployment keeps its evidence.
    assert ".Values.paused" in _sans_comments(PAUSED_DEPLOYMENT)


def test_a_comment_never_exempts_a_workload_from_the_paused_rule() -> None:
    # A comment mentioning "-docs" must not smuggle a workload into the docs
    # exemption of INT-FOUNDRY-030 (the third way this class of defect bites).
    commented = 'kind: Deployment\n# named like the "<chart>-docs" pattern\nspec:\n  replicas: 1\n'
    assert not _is_docs_component(_sans_comments(commented))


# The suite's own checkout, landed inside the app workspace by the shared
# workflow as .ci-suite/, is not the app's evidence. Counted as such, its
# Python demanded __pycache__/ of a Python-free repo and 2ez4tv went red on a
# tree it cannot produce (run 31879419828) - invisible locally, where no
# .ci-suite exists. The other direction is asserted too: the app's OWN Python
# must still be seen, or the fix would simply blind the rule.
def test_the_suites_own_checkout_is_not_the_apps_evidence(tmp_path) -> None:
    suite = tmp_path / ".ci-suite" / "conformance"
    suite.mkdir(parents=True)
    (suite / "test_repo_conformance.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    patterns = {pattern for _, pattern in _generated_trees(tmp_path)}
    assert "__pycache__/" not in patterns, "the suite's own Python is not the app's"
    assert "node_modules/" in patterns, "the app's own package.json is still seen"


def test_the_apps_own_python_is_still_demanded(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("x = 1\n", encoding="utf-8")
    assert "__pycache__/" in {pattern for _, pattern in _generated_trees(tmp_path)}


# INT-FOUNDRY-082 (GHSA-7x63-xv5r-3p2x): the proxy-trust reader, against the
# fleet's own sidecar shapes. A chart engages only when one template both reads
# forwarded headers and skips a route; then the flag, its guard and the release
# all have to hold.
_SKIPPING_PROXY = """\
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: oauth2-proxy
          image: "{{ .Values.auth.proxyImage }}"
          args:
            - --reverse-proxy=true
            - --skip-auth-route=^/mcp(/|$)
"""

_GUARDED_TRUST = """\
            {{- if not .Values.auth.oidc.trustedProxyIPs }}
            {{- fail "set auth.oidc.trustedProxyIPs" }}
            {{- end }}
            {{- range .Values.auth.oidc.trustedProxyIPs }}
            - --trusted-proxy-ip={{ . }}
            {{- end }}
"""


def _trust(template: str, image: str = "quay.io/oauth2-proxy/oauth2-proxy:v7.15.6"):
    return _proxy_trust_offenders({"auth": {"proxyImage": image}}, {"deployment.yaml": template})


def test_the_unfixed_shape_is_refused() -> None:
    offenders = _trust(_SKIPPING_PROXY, "quay.io/oauth2-proxy/oauth2-proxy:v7.7.1")
    assert any("no --trusted-proxy-ip" in o for o in offenders)
    assert any("predates 7.15.2" in o for o in offenders)


def test_the_fixed_shape_passes() -> None:
    assert _trust(_SKIPPING_PROXY + _GUARDED_TRUST) == []


def test_a_new_release_without_the_flag_still_fails() -> None:
    assert any("no --trusted-proxy-ip" in o for o in _trust(_SKIPPING_PROXY))


def test_an_unguarded_templated_list_fails() -> None:
    unguarded = _SKIPPING_PROXY + "            - --trusted-proxy-ip={{ .Values.cidr }}\n"
    assert any("nothing refuses an unset list" in o for o in _trust(unguarded))


def test_trusting_every_address_fails() -> None:
    assert any("trusts every address" in o for o in _trust(_SKIPPING_PROXY + "            - --trusted-proxy-ip=0.0.0.0/0\n"))


def test_a_repository_and_tag_pair_is_judged() -> None:
    values = {"auth": {"oidc": {"image": {"repository": "quay.io/oauth2-proxy/oauth2-proxy", "tag": "v7.15.5"}}}}
    offenders = _proxy_trust_offenders(values, {"deployment.yaml": _SKIPPING_PROXY + _GUARDED_TRUST})
    assert offenders == []
    values["auth"]["oidc"]["image"]["tag"] = "v7.7.1"
    assert any("auth.oidc.image.tag" in o for o in _proxy_trust_offenders(values, {"deployment.yaml": _SKIPPING_PROXY + _GUARDED_TRUST}))


def test_a_proxy_that_skips_nothing_is_vacuous() -> None:
    no_skip = _SKIPPING_PROXY.replace("            - --skip-auth-route=^/mcp(/|$)\n", "")
    assert _trust(no_skip, "quay.io/oauth2-proxy/oauth2-proxy:v7.7.1") is None


def test_a_comment_never_satisfies_the_trust_flag() -> None:
    commented = _sans_comments(_SKIPPING_PROXY + "            # - --trusted-proxy-ip={{ . }}  fail trustedProxyIPs\n")
    assert any("no --trusted-proxy-ip" in o for o in _trust(commented))
