import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "docs_origin", Path(__file__).resolve().parents[1] / ".github/actions/build-docs/verify.py"
)
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)
SITE = "https://docs.example.invalid"


def test_generated_links_are_checked_without_matching_an_example(tmp_path):
    (tmp_path / "index.html").write_text(
        f"<code>&lt;link href='http://localhost:4321/'&gt;</code><link href='{SITE}/apps/a/' rel='canonical'>"
    )
    (tmp_path / "sitemap-index.xml").write_text(
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>{SITE}/apps/a/sitemap-0.xml</loc></sitemap></sitemapindex>'
    )
    assert docs.verify(tmp_path, SITE) == 2


@pytest.mark.parametrize("url", ["http://localhost:4321/a/", "https://127.0.0.1/a/", "https://elsewhere.invalid/a/"])
@pytest.mark.parametrize("file", ["index.html", "sitemap-0.xml"])
def test_invalid_published_address_is_rejected(tmp_path, url, file):
    (tmp_path / "index.html").write_text("<p>Book</p>")
    content = f"<link rel='canonical' href='{url}'>" if file.endswith(".html") else f"<urlset><url><loc>{url}</loc></url></urlset>"
    (tmp_path / file).write_text(content)
    with pytest.raises(ValueError):
        docs.verify(tmp_path, SITE)
