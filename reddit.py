"""
Reddit hiring post scraper.
No API credentials required — uses Reddit's public search.json endpoint.
Paginates via `after` token (equivalent to scrolling) until quantity is met.

Search mirrors: https://www.reddit.com/search/?q=[Hiring]+<keywords>&type=posts&t=day
"""
import os
import time
import random
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    from curl_cffi import requests as http_requests
    _HTTP = http_requests
    _CURL_CFFI = True
except ImportError:
    import requests as http_requests
    _HTTP = http_requests
    _CURL_CFFI = False

# Chrome TLS fingerprint (Reddit often 403s plain urllib3/requests from VPS)
_TLS_IMPERSONATE = os.getenv("REDDIT_TLS_IMPERSONATE", "chrome124").strip()

# ── Config ─────────────────────────────────────────────────────────────────────
RATE_LIMIT_DELAY = float(os.getenv("REDDIT_RATE_LIMIT_DELAY", "2.0"))  # seconds between pages
MIN_POST_LENGTH  = 60
USER_AGENT       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

BACKOFF_BASE     = 10   # seconds — initial wait on 429 / 5xx
BACKOFF_MAX      = 180  # seconds — ceiling
BACKOFF_RETRIES  = 8
# Reddit often blocks datacenter IPs; use REDDIT_PROXY if you see connect timeouts
CONNECT_TIMEOUT  = float(os.getenv("REDDIT_CONNECT_TIMEOUT", "20"))
READ_TIMEOUT     = float(os.getenv("REDDIT_READ_TIMEOUT", "60"))

SEARCH_URL       = "https://www.reddit.com/search.json"

# Reddit search time filters: hour, day, week, month, year, all
VALID_TIME_FILTERS = ("hour", "day", "week", "month", "year", "all")

log = logging.getLogger(__name__)

# ── Signal phrases (for quality filtering) ─────────────────────────────────────
FOR_HIRE_SIGNALS = [
    "[for hire]", "for hire -", "for hire:", "i am available", "i'm available",
    "i offer", "my portfolio", "hire me", "dm me for rates", "open to work",
    "taking clients", "fiverr.com", "upwork.com", "available for hire",
    "looking for work", "rates start at", "portfolio in bio",
]
NEGATIVE_PHRASES = [
    "unpaid", "for free", "free work", "volunteer", "no budget", "no pay",
    "equity only", "exposure only", "student project", "not paid",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_query(keywords: List[str]) -> str:
    """
    Builds the search query string.
    Always anchors on [Hiring] so only hiring posts come back,
    then ORs the keywords so any match qualifies.

    e.g. keywords=["web developer","designer"]
    →  '[Hiring] (web developer OR designer)'
    """
    kw_part = " OR ".join(keywords) if keywords else ""
    return f"[Hiring] ({kw_part})" if kw_part else "[Hiring]"


def _is_for_hire(title: str, body: str) -> bool:
    c = (title + " " + body).lower()
    return any(s in c for s in FOR_HIRE_SIGNALS)


def _is_negative(title: str, body: str) -> bool:
    c = (title + " " + body).lower()
    return any(p in c for p in NEGATIVE_PHRASES)


def _too_short(title: str, body: str) -> bool:
    return len((title + body).strip()) < MIN_POST_LENGTH


def _make_record(post: Dict) -> Dict[str, Any]:
    author = post.get("author") or "[deleted]"
    return {
        "platform":     "reddit",
        "username":     f"u/{author}",
        "post_url":     f"https://reddit.com{post.get('permalink', '')}",
        "post_content": post.get("selftext") or post.get("title") or "",
        "title":        post.get("title") or "",
        "subreddit":    post.get("subreddit") or "",
        "created_utc":  post.get("created_utc"),
        "scraped_at":   datetime.utcnow().isoformat() + "Z",
    }


# ── HTTP session with backoff ──────────────────────────────────────────────────

class _Session:
    def __init__(self, proxies: List[str]):
        self._proxies = proxies
        self._idx     = 0
        self._sess    = _HTTP.Session()
        self._sess.headers.update({
            "User-Agent":      USER_AGENT,
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.reddit.com/search/?q=%5BHiring%5D&type=posts",
            "Sec-Fetch-Dest":  "empty",
            "Sec-Fetch-Mode":  "cors",
            "Sec-Fetch-Site":  "same-origin",
        })
        if _CURL_CFFI and _TLS_IMPERSONATE:
            log.info(f"Reddit HTTP: curl_cffi TLS impersonate={_TLS_IMPERSONATE!r}")
        elif not _CURL_CFFI:
            log.warning(
                "curl-cffi not installed — Reddit may 403 from VPS; "
                "pip install curl-cffi or set REDDIT_PROXY"
            )

    def _next_proxy(self) -> Optional[Dict[str, str]]:
        if not self._proxies:
            return None
        p = self._proxies[self._idx]
        self._idx = (self._idx + 1) % len(self._proxies)
        return {"http": p, "https": p}

    def get(self, params: Dict) -> Optional[Dict]:
        proxy = self._next_proxy()
        delay = BACKOFF_BASE
        p     = {"raw_json": "1", **params}

        for attempt in range(BACKOFF_RETRIES):
            try:
                req_kw: Dict[str, Any] = {
                    "params": p,
                    "proxies": proxy,
                    "timeout": (CONNECT_TIMEOUT, READ_TIMEOUT),
                }
                if _CURL_CFFI and _TLS_IMPERSONATE:
                    req_kw["impersonate"] = _TLS_IMPERSONATE

                resp = self._sess.get(SEARCH_URL, **req_kw)
                try:
                    code = int(resp.status_code)
                except (TypeError, ValueError):
                    code = 0

                if code == 200:
                    return resp.json()

                if code == 403:
                    wait = min(delay + random.uniform(0, 2), BACKOFF_MAX)
                    hint = ""
                    if not self._proxies:
                        hint = (
                            " — install curl-cffi + rebuild image, or set REDDIT_PROXY "
                            "(residential proxy) if this persists"
                        )
                    log.warning(
                        f"403 Forbidden from Reddit (datacenter/WAF){hint} — "
                        f"waiting {wait:.1f}s, rotating proxy if any"
                    )
                    time.sleep(wait)
                    delay = min(delay * 2, BACKOFF_MAX)
                    proxy = self._next_proxy()
                    continue

                if code == 429:
                    wait = min(
                        float(resp.headers.get("Retry-After", delay)) + random.uniform(0, 3),
                        BACKOFF_MAX,
                    )
                    log.warning(f"429 rate-limited — waiting {wait:.1f}s (attempt {attempt+1}/{BACKOFF_RETRIES})")
                    time.sleep(wait)
                    delay = min(delay * 2, BACKOFF_MAX)
                    proxy = self._next_proxy()   # rotate IP on retry
                    continue

                if code in (500, 502, 503, 504):
                    wait = min(delay + random.uniform(0, 2), BACKOFF_MAX)
                    log.warning(f"HTTP {code} — waiting {wait:.1f}s")
                    time.sleep(wait)
                    delay = min(delay * 2, BACKOFF_MAX)
                    continue

                log.warning(f"HTTP {code or resp.status_code} — stopping pagination")
                return None

            except Exception as e:
                wait = min(delay + random.uniform(0, 2), BACKOFF_MAX)
                hint = ""
                if not self._proxies and (
                    "timeout" in str(e).lower() or "Timeout" in type(e).__name__
                ):
                    hint = " (Reddit often blocks datacenter IPs — set REDDIT_PROXY or REDDIT_PROXY_LIST)"
                log.warning(f"Request error: {e}{hint} — waiting {wait:.1f}s")
                time.sleep(wait)
                delay = min(delay * 2, BACKOFF_MAX)

        log.error(
            f"Gave up after {BACKOFF_RETRIES} retries — "
            "if Reddit was 403, set REDDIT_PROXY (residential) or upgrade image (curl-cffi TLS)"
        )
        return None

    def sleep(self):
        """Jittered sleep between page fetches."""
        time.sleep(RATE_LIMIT_DELAY + random.uniform(0, RATE_LIMIT_DELAY * 0.4))


def _parse_proxy_list() -> List[str]:
    raw = os.getenv("REDDIT_PROXY_LIST", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    single = os.getenv("REDDIT_PROXY") or os.getenv("HTTP_PROXY", "").strip()
    return [single] if single else []


# ── Core paginator ─────────────────────────────────────────────────────────────

def _fetch_pages(
    session: _Session,
    query: str,
    time_filter: str,
    quantity: int,
    seen_urls: set,
) -> List[Dict[str, Any]]:
    """
    Fetches search result pages for `query` until `quantity` passing posts
    are collected or Reddit has no more results.

    Each page = 100 posts (Reddit's max per request).
    Pages are chained via the `after` token — same as scrolling the page.
    """
    results: List[Dict[str, Any]] = []
    after:   Optional[str]        = None
    page                          = 0

    base_params = {
        "q":      query,
        "sort":   "new",
        "t":      time_filter,
        "type":   "link",
        "limit":  100,
    }

    log.info(f"  query='{query}'  t={time_filter}")

    while len(results) < quantity:
        params = dict(base_params)
        if after:
            params["after"] = after

        data = session.get(params)
        if not data:
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            log.info(f"  page {page+1}: no more results")
            break

        added_this_page = 0
        for child in children:
            post  = child.get("data", {})
            title = (post.get("title") or "").lower()
            body  = post.get("selftext") or ""
            url   = f"https://reddit.com{post.get('permalink', '')}"

            # Dedup
            if url in seen_urls:
                continue

            # Quality filters
            if _is_for_hire(title, body):
                continue
            if _is_negative(title, body):
                continue
            if _too_short(title, body):
                continue

            seen_urls.add(url)
            results.append(_make_record(post))
            added_this_page += 1

            if len(results) >= quantity:
                break

        after = data.get("data", {}).get("after")
        page += 1
        log.info(f"  page {page}: +{added_this_page} posts  (total {len(results)}/{quantity})")

        if not after:
            log.info(f"  no more pages from Reddit")
            break

        session.sleep()

    return results


# ── Public entry point ─────────────────────────────────────────────────────────

def scrape_reddit(
    keywords: List[str],
    quantity: int = 50,
    time_filter: str = "day",
) -> List[Dict[str, Any]]:
    """
    Scrape Reddit for [Hiring] posts matching `keywords`.

    Args:
        keywords:     Role/skill keywords, e.g. ["web developer", "designer"].
                      Combined into a single OR query anchored on [Hiring].
        quantity:     How many posts to return. Paginates automatically until met.
        time_filter:  Reddit time filter: hour, day, week, month, year, or all.

    Returns:
        List of post dicts, newest first, capped at `quantity`.

    Example:
        posts = scrape_reddit(["web developer", "react"], quantity=100, time_filter="week")
    """
    t = time_filter.lower() if time_filter else "day"
    if t not in VALID_TIME_FILTERS:
        t = "day"

    proxies = _parse_proxy_list()
    session = _Session(proxies)

    if proxies:
        log.info(f"Using {len(proxies)} proxy(s)")
    log.info(f"Target: {quantity} posts | t={t} | Delay: {RATE_LIMIT_DELAY}s/page")

    query     = _build_query(keywords)
    seen_urls = set()
    results   = []

    for time_filter in [t]:
        needed = quantity - len(results)
        if needed <= 0:
            break

        log.info(f"━━ Time filter: t={time_filter} | Need {needed} more posts ━━")
        batch = _fetch_pages(session, query, time_filter, needed, seen_urls)
        results.extend(batch)
        log.info(f"t={time_filter} done: collected {len(results)}/{quantity}")

        if len(results) >= quantity:
            break
        log.warning(f"Time window t={time_filter} exhausted. Returning {len(results)}/{quantity}.")

    log.info(f"✅ {len(results)} unique hiring leads returned")
    return results[: quantity]