"""Conservative, read-only discovery of first-party CLI/agent-skill documentation.

This is an experimental evidence collector, not an installer, router or proof of
capability. It fetches a bounded set of HTTPS HTML pages, without credentials.
"""
from __future__ import annotations

import argparse
from collections import deque
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 500_000
USER_AGENT = "Mozilla/5.0 (compatible; InterfaceDiscoveryResearch/0.1)"
CLI = re.compile(r"(?i)(?:\bcli\b|command[- ]line|命令行)")
SKILL = re.compile(r"(?i)(?:\bskills?\b|智能体技能|agent[- ]skill)")
HELP = re.compile(r"(?i)(?:help|support|docs?|documentation|developers?|integrations?|帮助|开发|文档|拓展|扩展)")


def safe_url(url, root_host):
    p = urlsplit(url)
    host = (p.hostname or "").lower().rstrip(".")
    try:
        port = p.port
    except ValueError:
        return None
    if (p.scheme.lower() != "https" or not host or p.username or p.password
            or port not in (None, 443) or (host != root_host and not host.endswith("." + root_host))):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    return urlunsplit(("https", host, p.path or "/", p.query, ""))


def public_dns(host):
    addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("host did not resolve exclusively to public addresses")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, root_host, timeout):
    """Fetch one public first-party HTML page; redirects are not followed."""
    if safe_url(url, root_host) != url:
        raise ValueError("URL outside HTTPS first-party boundary")
    public_dns(urlsplit(url).hostname)
    opener = build_opener(NoRedirect())
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with opener.open(req, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("not HTML")
            return response.status, response.read(MAX_BYTES).decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}; redirects are not followed") from exc


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.heading_depth = 0
        self.heading_text = []
        self.text = []
        self.links = []
        self.link = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if not self.skip and tag in ("title", "h1"):
            self.heading_depth += 1
        if not self.skip and tag == "a":
            self.link = [dict(attrs).get("href", ""), []]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        if not self.skip and tag in ("title", "h1") and self.heading_depth:
            self.heading_depth -= 1
        if tag == "a" and self.link:
            self.links.append((self.link[0], " ".join(self.link[1])))
            self.link = None

    def handle_data(self, data):
        if self.skip:
            return
        clean = " ".join(data.split())
        if clean:
            self.text.append(clean)
            if self.heading_depth:
                self.heading_text.append(clean)
            if self.link:
                self.link[1].append(clean)


def discover(site, max_pages=12, timeout=12, fetcher=fetch):
    p = urlsplit(site)
    host = (p.hostname or "").lower().rstrip(".")
    entry = safe_url(site, host)
    if not entry or max_pages < 1 or max_pages > 30 or timeout < 1 or timeout > 30:
        raise ValueError("expected public HTTPS site and bounded limits")
    # Keep precisely the supplied host and its subdomains; do not infer an
    # organizational domain from a public suffix or trust outside search hits.
    queue = deque([entry])
    root = safe_url("https://" + host + "/", host)
    if root != entry:
        queue.append(root)
    queued = set(queue)
    visited, candidates = [], []
    while queue and len(visited) < max_pages:
        url = queue.popleft()
        record = {"url": url}
        try:
            status, raw = fetcher(url, host, timeout)
            page = Page()
            page.feed(raw)
            heading = " ".join(page.heading_text)[:500]
            record.update(status=status, heading=heading)
            # A menu link alone isn't a page documenting the feature.
            for kind, pattern in (("cli", CLI), ("agent_skill", SKILL)):
                if pattern.search(heading):
                    candidates.append({"kind": kind, "url": url, "heading": heading,
                        "status": "same_site_unverified_candidate"})
            ranked = []
            for href, label in page.links:
                child = safe_url(urljoin(url, href), host)
                if not child or child in queued:
                    continue
                evidence = label + " " + urlsplit(child).path
                priority = 0 if CLI.search(evidence) or SKILL.search(evidence) else (1 if HELP.search(evidence) else 2)
                # Only follow promising documentation/navigation links, not
                # arbitrary account pages or external sites.
                if priority < 2:
                    ranked.append((priority, child))
            for _, child in sorted(ranked):
                if child not in queued:
                    queue.append(child)
                    queued.add(child)
        except (ValueError, OSError, URLError) as exc:
            record.update(status="fetch_failed", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        visited.append(record)
    return {"site": entry, "status": "candidates_found" if candidates else "not_found_or_inconclusive",
        "pages_visited": visited, "candidates": candidates, "max_pages": max_pages,
        "limitations": ["Candidates are same-site pages whose titles/headings mention CLI or skills; same-site pages may be user-generated. Official ownership, semantic support and safety are not verified.",
          "No candidate does not establish that an interface does not exist; external official documentation and redirects are not followed.",
          "No authentication, command execution, installation, capability routing or model calls were performed.",
          "DNS is checked before requests, but the resolver/network layer is not a hardened anti-rebinding boundary."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site")
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--output", type=Path, help="New JSON report path under /shared/hans; refuses overwrite")
    args = parser.parse_args()
    result = json.dumps(discover(args.site, args.max_pages, args.timeout), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        path = args.output.resolve()
        if not path.is_relative_to(Path('/shared/hans')):
            raise ValueError('output must be under /shared/hans')
        with path.open('x', encoding='utf-8') as stream:
            stream.write(result)
    print(result, end='')


if __name__ == "__main__":
    main()
