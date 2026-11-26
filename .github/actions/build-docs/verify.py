"""Check the canonical links and sitemap locations in a published documentation tree."""
import argparse
import ipaddress
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


def public_origin(value):
    url = urlsplit(value)
    host = (url.hostname or "").rstrip(".").lower()
    if url.scheme != "https" or not host or url.username or url.password:
        raise ValueError("documentation requires an absolute public HTTPS URL")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("documentation references localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if ip.is_loopback or ip.is_unspecified:
            raise ValueError("documentation references a loopback or unspecified address")
    return url.scheme, host, url.port or 443


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "link" and "canonical" in (fields.get("rel") or "").lower().split():
            self.links.append(fields.get("href") or "")


def verify(root, site):
    expected = public_origin(site)
    parsed = urlsplit(site)
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("the site origin must not include the book path, a query or a fragment")
    if not (root / "index.html").is_file():
        raise ValueError("documentation has no index.html at its configured mount")
    checked = 0

    def check(url, path):
        try:
            if public_origin(url) != expected:
                raise ValueError("documentation references a different publication origin")
        except ValueError as exc:
            raise ValueError(f"{path.relative_to(root)}: {exc}") from exc

    for path in root.rglob("*.html"):
        parser = PageLinks()
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
        for href in parser.links:
            check(href, path)
            checked += 1
    for path in root.rglob("sitemap*.xml"):
        for loc in ET.parse(path).iter():
            if loc.tag.rsplit("}", 1)[-1] == "loc":
                check((loc.text or "").strip(), path)
                checked += 1
    return checked


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("root", type=Path)
    cli.add_argument("site")
    args = cli.parse_args()
    print(f"Verified {verify(args.root, args.site)} canonical and sitemap URLs.")
