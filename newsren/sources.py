"""Source discovery and evidence retrieval, restricted to the approved list in config/sources.json.

Every item carries provenance: publisher, domain, source_type, published time and retrieval time.
"""
import gzip
import html
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .store import CONFIG, iso, load_json, now, short_hash

UA = "Mozilla/5.0 (compatible; NewsrenStudentPrototype/0.1)"
ATOM = "{http://www.w3.org/2005/Atom}"
_config = None


def config():
    global _config
    if _config is None:
        _config = load_json(CONFIG)
    return _config


def domain_of(url):
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def classify(url):
    """Return (approved, source_type) for a URL by its domain or any parent domain."""
    host = domain_of(url)
    domains = config()["domains"]
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in domains:
            return True, domains[".".join(parts[i:])]
    return False, "unknown"


def _ssl_context():
    # python.org builds on macOS ship without CA certs; fall back to certifi or the system bundle
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        bundle = "/etc/ssl/cert.pem"
        return ssl.create_default_context(cafile=bundle if os.path.exists(bundle) else None)


SSL = _ssl_context()


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL) as r:
        body = r.read()
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body  # some feeds gzip unasked


def _item(url, title, published, publisher, source_type, summary, via):
    return {
        "id": short_hash(url), "url": url, "title": _clean(title),
        "published": iso(published), "retrieved_at": iso(now()),
        "publisher": publisher, "domain": domain_of(url), "source_type": source_type,
        "summary": _clean(summary)[:600], "via": via,
    }


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None


# ---- discovery -------------------------------------------------------------

def read_feed(feed, since):
    root = ET.fromstring(_get(feed["url"]))
    out = []
    for el in root.iter():
        if el.tag == "item":  # RSS 2.0
            url = (el.findtext("link") or "").strip()
            pub = _date(el.findtext("pubDate"))
            title, summary = el.findtext("title"), el.findtext("description")
        elif el.tag == ATOM + "entry":
            link = el.find(ATOM + "link[@rel='alternate']")
            if link is None:  # Elements without children are falsy, so no `or` here
                link = el.find(ATOM + "link")
            url = link.get("href") if link is not None else ""
            pub = _date(el.findtext(ATOM + "published") or el.findtext(ATOM + "updated"))
            title = el.findtext(ATOM + "title")
            summary = el.findtext(ATOM + "summary") or el.findtext(ATOM + "content")
        else:
            continue
        if url and pub and pub >= since:
            out.append(_item(url, title, pub, feed["name"], feed["source_type"], summary, f"feed:{feed['id']}"))
    return out


def search_arxiv(query, since=None, limit=8):
    q = urllib.parse.urlencode({"search_query": query, "sortBy": "submittedDate",
                                "sortOrder": "descending", "max_results": limit})
    root = ET.fromstring(_get(f"http://export.arxiv.org/api/query?{q}", timeout=30))
    out = []
    for e in root.iter(ATOM + "entry"):
        pub = _date(e.findtext(ATOM + "published"))
        if since and pub and pub < since:
            continue
        url = (e.findtext(ATOM + "id") or "").strip()
        authors = ", ".join(a.findtext(ATOM + "name") for a in e.findall(ATOM + "author")[:3])
        out.append(_item(url, e.findtext(ATOM + "title"), pub, f"arXiv preprint ({authors})",
                         "paper", e.findtext(ATOM + "summary"), "search:arxiv"))
    return out


def search_hackernews(query, since=None, limit=8, min_points=10):
    """Hacker News is used to discover links; the linked page is the source, HN itself is only discussion."""
    params = {"query": query, "tags": "story", "hitsPerPage": limit,
              "numericFilters": f"points>{min_points}" + (f",created_at_i>{int(since.timestamp())}" if since else "")}
    data = json.loads(_get("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(params)))
    out = []
    for h in data.get("hits", []):
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        approved, stype = classify(url)
        pub = datetime.fromtimestamp(h["created_at_i"], timezone.utc)
        out.append(_item(url, h.get("title"), pub, domain_of(url), stype,
                         f"Linked from Hacker News ({h.get('points')} points). {h.get('story_text') or ''}",
                         "search:hackernews") | {"approved_domain": approved})
    return out


def discover(topic, since, log):
    items = []
    kw = [k.lower() for k in topic["keywords"]]
    for feed in config()["feeds"]:
        try:
            for it in read_feed(feed, since):
                text = (it["title"] + " " + it["summary"]).lower()
                if any(k in text for k in kw):
                    items.append(it)
        except Exception as e:  # one broken feed should not stop the run
            log(f"feed {feed['id']} failed: {e}")
    for q in topic.get("arxiv_queries", []):
        try:
            items += search_arxiv(q, since)
        except Exception as e:
            log(f"arxiv '{q}' failed: {e}")
    for q in topic.get("hn_queries", []):
        try:
            items += search_hackernews(q, since)
        except Exception as e:
            log(f"hackernews '{q}' failed: {e}")
    unique = {}
    for it in items:
        unique.setdefault(it["url"], it)
    return list(unique.values())


# ---- evidence retrieval ------------------------------------------------------

def search_evidence(query, log):
    """Search the approved tools for evidence about a claim (no date limit: evidence can be older)."""
    out = []
    words = query.split()
    try:
        # HN search requires every word to match, so back off to shorter queries until something is found
        for n in range(len(words), 1, -1):
            out += search_hackernews(" ".join(words[:n]), limit=6, min_points=5)
            if out:
                break
    except Exception as e:
        log(f"evidence hackernews '{query}' failed: {e}")
    try:
        words = [w for w in re.findall(r"[\w.-]+", query) if len(w) > 2][:5]
        if words:
            out += search_arxiv(" AND ".join(f"all:{w}" for w in words), limit=4)
    except Exception as e:
        log(f"evidence arxiv '{query}' failed: {e}")
    return out


def fetch_text(url, max_chars=5000):
    """Fetch page text, but only from approved domains (no unrestricted browsing)."""
    approved, _ = classify(url)
    if not approved:
        return None
    try:
        raw = _get(url, timeout=20).decode("utf-8", "ignore")
    except Exception:
        return None
    raw = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    return _clean(raw)[:max_chars]
