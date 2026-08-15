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

from test_repo_conformance import _generated_trees, _is_docs_component, _sans_comments

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
