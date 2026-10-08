#!/usr/bin/env python3
"""Search PanSou and public web indexes for movie source leads; never fetch media."""

import argparse
import concurrent.futures
import html
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspect_link import decode_thunder

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
DEFAULT_PROXY = os.environ.get("XUNLEI_CINEMA_PROXY", "http://127.0.0.1:7897")
LINK_RE = re.compile(
    r"(?i)(?:thunder://[A-Za-z0-9+/=_-]+|magnet:\?[^\s\"'<>]+|"
    r"https?://pan\.xunlei\.com/s/[A-Za-z0-9_-]+(?:\?[^\s\"'<>]*)?|"
    r"(?:https?|ftp)://[^\s\"'<>]+?\.(?:torrent)(?:\?[^\s\"'<>]*)?|"
    r"https?://[^\s\"'<>]+?\.(?:mkv|mp4|avi|mov|m2ts|ts|webm)(?:\?[^\s\"'<>]*)?|"
    r"ftp://[^\s\"'<>]+)"
)


def is_public_web_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".localhost", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_global
    except ValueError:
        return True


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_web_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "blocked non-public redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=12, max_bytes=1_500_000):
    if not is_public_web_url(url):
        raise ValueError("blocked non-public or credential-bearing URL")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if DEFAULT_PROXY:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": DEFAULT_PROXY,
                                                                          "https": DEFAULT_PROXY}),
                                             PublicRedirectHandler())
        response = opener.open(req, timeout=timeout)
    else:
        opener = urllib.request.build_opener(PublicRedirectHandler())
        response = opener.open(req, timeout=timeout)
    with response as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if not any(x in content_type for x in ("text/html", "text/plain", "xml", "json")):
            raise ValueError("non-document response; media and binary files are never fetched")
        return resp.read(max_bytes).decode("utf-8", "replace"), resp.geturl()


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href, self._text = None, []


def query_variants(title, year, aliases):
    names = list(dict.fromkeys([title, *aliases]))
    by_name = []
    for name in names:
        base = f'"{name}" {year}' if year else f'"{name}"'
        by_name.append([f"{base} {tail}" for tail in
                        ("迅雷下载", "磁力 OR 种子", "4K OR 2160p OR 1080p", "thunder:// OR magnet:")])
    # Round-robin titles so aliases are not starved by the query limit.
    return [q for column in zip(*by_name) for q in column]


def search_bing(query):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"format": "rss", "q": query})
    text, final = fetch(url)
    root = ET.fromstring(text)
    out = []
    for item in root.findall(".//item")[:8]:
        out.append({"title": item.findtext("title", ""), "url": item.findtext("link", ""),
                    "snippet": item.findtext("description", ""), "source": "Bing RSS"})
    return out


def search_baidu(query):
    url = "https://www.baidu.com/s?" + urllib.parse.urlencode({"wd": query})
    text, final = fetch(url)
    parser = Anchors()
    parser.feed(text)
    out = []
    for href, title in parser.links:
        if not href or href.startswith(("#", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(final, html.unescape(href))
        if len(title) > 3 and absolute.startswith("http"):
            out.append({"title": title, "url": absolute, "snippet": "", "source": "Baidu"})
    return out[:12]


def unwrap_pansou(raw):
    data = raw.get("data", raw)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else {}


def search_pansou(query, endpoint):
    payload = json.dumps({"kw": query, "res": "merge", "cloud_types": ["xunlei", "magnet"]}).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/api/search", data=payload,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=28) as resp:
        raw = json.load(resp)
    data = unwrap_pansou(raw)
    merged = data.get("merged_by_type", {})
    context_by_url = {}
    for result in data.get("results", []):
        content = str(result.get("content", ""))
        for link in result.get("links", []):
            link_url = link.get("url", "")
            pos = content.find(link_url) if link_url else -1
            context = content[max(0, pos - 180):pos + len(link_url) + 180] if pos >= 0 else content[:500]
            context_by_url[link_url] = {"title": link.get("work_title") or result.get("title", ""),
                                        "context": context, "result_title": result.get("title", "")}
    out = []
    for kind in ("xunlei", "magnet"):
        merged_items = merged.get(kind, []) if isinstance(merged, dict) else []
        if not merged_items:
            merged_items = [link for result in data.get("results", [])
                            for link in result.get("links", []) if link.get("type") == kind]
        for item in merged_items:
            url = item.get("url", "")
            actual = context_by_url.get(url, {})
            out.append({"title": actual.get("title") or item.get("note", ""), "url": url,
                        "snippet": actual.get("context", item.get("content", "")),
                        "reported_title": item.get("note", ""),
                        "source": item.get("source", "PanSou"),
                        "password": item.get("password", ""), "type": kind,
                        "result_title": actual.get("result_title", "")})
    return out


def classify(url):
    low = url.lower()
    if low.startswith("thunder://"):
        return "thunder"
    if low.startswith("magnet:"):
        return "magnet"
    if "pan.xunlei.com/s/" in low:
        return "xunlei_share"
    if low.startswith("ftp://"):
        return "ftp"
    if low.split("?", 1)[0].endswith(".torrent"):
        return "torrent"
    if re.search(r"\.(?:mkv|mp4|avi|mov|m2ts|ts|webm)(?:\?|$)", low):
        return "direct_url"
    return "web_page"


def check_xunlei_shares(candidates, endpoint):
    shares = [x for x in candidates if x["type"] == "xunlei_share"]
    for i in range(0, len(shares), 50):
        batch = shares[i:i + 50]
        payload = {"items": [{"disk_type": "xunlei", "url": x["url"],
                              "password": x.get("password") or ""} for x in batch]}
        req = urllib.request.Request(endpoint.rstrip("/") + "/api/check/links",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                raw = json.load(resp)
            results = raw.get("data", raw).get("results", [])
            by_url = {x.get("url"): x.get("state", "uncertain") for x in results}
            for item in batch:
                item["link_state"] = by_url.get(item["url"], "uncertain")
        except Exception as exc:
            for item in batch:
                item["link_state"] = "uncertain"
                item["check_error"] = str(exc)[:180]


def normalize_candidate(url, title="", snippet="", source="", page="", password=""):
    url = html.unescape(urllib.parse.unquote(url.strip().rstrip(".,;)]}，。；）")))
    if not url.startswith(("http://", "https://", "ftp://", "magnet:", "thunder://")):
        return None
    kind = classify(url)
    if kind == "web_page" and not re.search(r"pan\.xunlei\.com/s/|\.torrent(?:\?|$)|magnet:|thunder://|ftp://", url, re.I):
        return None
    result = {"type": kind, "url": url, "title": title, "context": snippet,
            "source": source, "source_page": page or None, "password": password or None,
            "link_state": "unchecked", "quality": {"resolution": None, "confidence": "unverified"}}
    if kind == "thunder":
        try:
            result["decoded_metadata"] = decode_thunder(url)
        except Exception as exc:
            result["decode_error"] = str(exc)
    elif kind == "magnet":
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        result["decoded_metadata"] = {"info_hash": (query.get("xt") or [None])[0],
                                       "display_name": (query.get("dn") or [None])[0],
                                       "metadata_available": False}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("title")
    ap.add_argument("--year")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--endpoint", default="http://127.0.0.1:18888")
    ap.add_argument("--queries", type=int, default=12, help="并行搜索的查询数上限")
    ap.add_argument("--pages", type=int, default=10, help="打开页面提取链接的上限")
    ap.add_argument("--no-check-shares", action="store_true", help="跳过 PanSou 分享有效性检测")
    ap.add_argument("--output", help="保存 JSON 候选结果")
    args = ap.parse_args()
    queries = query_variants(args.title, args.year, args.alias)[:max(1, args.queries)]
    # PanSou's indexed titles are sensitive to extra tokens; search the clean
    # title/aliases there, while public web engines also receive year terms.
    pan_queries = list(dict.fromkeys([args.title, *args.alias]))
    jobs = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for q in queries:
            jobs.extend([(pool.submit(search_bing, q), q, "Bing RSS"),
                         (pool.submit(search_baidu, q), q, "Baidu")])
        for q in pan_queries:
            jobs.append((pool.submit(search_pansou, q, args.endpoint), q, "PanSou"))
        pages = []
        for fut, q, engine in jobs:
            try:
                rows = fut.result()
                pages.extend((r, q) for r in rows if r.get("url"))
            except Exception as exc:
                errors.append({"channel": engine, "query": q, "error": str(exc)[:240]})
    candidates = []
    visited = set()
    for row, q in pages:
        source_page = None if row.get("source", "").startswith("plugin:") or row.get("source") == "PanSou" else row.get("url", "")
        for raw_link in LINK_RE.findall(row.get("url", "") + " " + row.get("snippet", "")):
            item = normalize_candidate(raw_link, row.get("title", ""), row.get("snippet", ""),
                                       row.get("source", ""), source_page, row.get("password", ""))
            if item and row.get("reported_title"):
                item["reported_title"] = row["reported_title"]
            if item and item["url"] not in visited:
                visited.add(item["url"])
                candidates.append(item)
    # Read only a bounded number of result pages to find links absent from snippets.
    pages_read = 0
    pages_attempted = 0
    for row, q in pages:
        page_url = row.get("url", "")
        if pages_attempted >= args.pages or not page_url.startswith("http") or page_url in visited:
            continue
        if classify(page_url) != "web_page":
            continue
        if any(domain in page_url.lower() for domain in ("bing.com/search", "baidu.com/s?", "pan.xunlei.com")):
            continue
        visited.add(page_url)
        pages_attempted += 1
        try:
            body, final_url = fetch(page_url, timeout=8, max_bytes=1_000_000)
            pages_read += 1
            decoded_body = html.unescape(body)
            raw_links = list(LINK_RE.findall(decoded_body))
            raw_links.extend(LINK_RE.findall(urllib.parse.unquote(decoded_body)))
            for raw_link in dict.fromkeys(raw_links):
                decoded_link = urllib.parse.unquote(raw_link)
                pos = decoded_body.find(raw_link)
                if pos < 0:
                    pos = decoded_body.find(decoded_link)
                context = decoded_body[max(0, pos - 350):pos + len(raw_link) + 350] if pos >= 0 else decoded_body[:700]
                item = normalize_candidate(raw_link, row.get("title", ""), context,
                                           row.get("source", ""), final_url)
                if item and item["url"] not in visited:
                    if row.get("reported_title"):
                        item["reported_title"] = row["reported_title"]
                    visited.add(item["url"])
                    candidates.append(item)
        except Exception as exc:
            errors.append({"channel": "page_extract", "url": page_url, "error": str(exc)[:200]})
    if not args.no_check_shares and candidates:
        check_xunlei_shares(candidates, args.endpoint)
    result = {"query": {"title": args.title, "year": args.year, "aliases": args.alias, "variants": queries},
              "candidate_count": len(candidates), "candidates": candidates, "pages_read": pages_read,
              "pages_attempted": pages_attempted,
              "web_results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                               "snippet": r.get("snippet", ""), "source": r.get("source", "")}
                              for r, _ in pages[:40]],
              "search_errors": errors}
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(encoded + "\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
