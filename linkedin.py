"""
LinkedIn post search scraper using Playwright.

Strategy (v7):
- Find ALL [role='listitem'] elements on the page (post containers).
- Grab all text from each listitem — includes user name and post content.
- Extract post links from <a> tags within the listitem.
- Optionally expand [data-testid='expandable-text-button'] inside listitem if present.
- Scroll-until-stable to load new posts, harvest, then paginate.
- Stops as soon as max_quantity posts have been collected (no page limit).

Uses Playwright + playwright-stealth for better anti-bot evasion in headless mode.
"""

import asyncio
import json
import os
import re
import time
import random
import logging
from datetime import datetime
from typing import List, Dict, Any, Set
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------

LISTITEM_SELECTOR = "[role='listitem']"
EXPANDABLE_TEXT_BUTTON_SELECTOR = "[data-testid='expandable-text-button']"

LOAD_MORE_SELECTORS = [
    "button.scaffold-finite-scroll__load-button",
    "button[aria-label*='Load more results' i]",
    "button[aria-label*='See more results' i]",
    "div.scaffold-finite-scroll__load-button",
    "span[role='button'][aria-label*='more' i]",
]

_NOISE_PATTERNS = [
    r"^\d[\d,]* (reaction|comment|repost|view)s?.*$",
    r"^(Like|Comment|Repost|Send|Share)$",
    r"^Follow$",
    r"^\d+(st|nd|rd|th)\s*•",
    r"^\d+[hHdDwWmMyY]\s*•",
    r"^(Promoted|Sponsored)$",
    r"^[…\.]{1,3}more$",
    r"^Feed post\s*",
]

# Name pattern: "FirstName LastName" or "FirstName Last-Name" (2–4 words)
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Za-z\-]+){1,3})\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jitter(lo: float = 0.5, hi: float = 1.5) -> None:
    time.sleep(random.uniform(lo, hi))


def _build_search_url(keywords: str) -> str:
    return "https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(keywords.strip())


def _parse_content_for_display(content: str) -> tuple[str, str]:
    """
    Separate username from post content. Strips "Feed post", header (name, headline, time).
    Returns (username, post_content).
    """
    raw = content.strip()
    raw = re.sub(r"^Feed post\s*", "", raw, flags=re.IGNORECASE)

    username = "linkedin"
    name_match = _NAME_PATTERN.search(raw)
    if name_match:
        username = name_match.group(1).strip()

    parts = [p.strip() for p in raw.split(" • ") if p.strip()]
    post_content = raw
    if len(parts) >= 2:
        header_indicators = ("|", "1st", "2nd", "3rd", "reactions", "comment", "repost")
        for part in reversed(parts):
            if not any(ind in part for ind in header_indicators) and not re.match(r"^\d+[hHdDwWmMyY]$", part):
                if len(part) >= 15:
                    post_content = part
                    break

    if post_content == raw and username != "linkedin":
        post_content = re.sub(rf"^{re.escape(username)}\s*\d*(?:st|nd|rd|th)\+?\s*", "", raw, flags=re.IGNORECASE)
        post_content = re.sub(rf"^{re.escape(username)}\s*[•·]\s*", "", post_content)
        post_content = post_content.strip()

    return (username, post_content)


# ---------------------------------------------------------------------------
# Core: expand "…more" button, read listitem, find post URL/hashtags
# ---------------------------------------------------------------------------

async def _expand_listitem(page, item) -> None:
    try:
        btn = item.locator(EXPANDABLE_TEXT_BUTTON_SELECTOR).first
        if await btn.is_visible():
            await btn.scroll_into_view_if_needed()
            _jitter(0.2, 0.4)
            await btn.click(force=True)
            _jitter(0.8, 1.5)
    except Exception:
        pass


async def _read_listitem(page, item) -> str:
    try:
        raw = await item.evaluate("""
            (el) => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('button').forEach(b => b.remove());
                return clone.innerText || clone.textContent || '';
            }
        """)
    except Exception:
        return ""

    raw = (raw or "").strip()
    clean_lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(re.match(pat, line, re.IGNORECASE) for pat in _NOISE_PATTERNS):
            continue
        clean_lines.append(line)

    result = "\n".join(clean_lines)
    # Fallback: if noise filter removed too much but raw has content, use first substantial lines
    if len(result) < 20 and len(raw) > 40:
        skip_words = {"like", "comment", "repost", "send", "share", "follow", "promoted", "sponsored"}
        lines = []
        for line in raw.splitlines():
            s = line.strip()
            if len(s) < 4:
                continue
            if s.lower() in skip_words:
                continue
            lines.append(s)
            if len("\n".join(lines)) >= 30:
                break
        if lines:
            result = "\n".join(lines[:6])
    return result


async def _find_post_url(page, item) -> str:
    try:
        for sel in ("a[href*='/posts/']", "a[href*='feed/update']", "a[href*='urn:li:share']", "a[href*='urn:li:activity']"):
            loc = item.locator(sel)
            if await loc.count() > 0:
                href = await loc.first.get_attribute("href")
                if href and "login" not in href and "linkedin.com" in href:
                    return href.split("?")[0]
        loc = item.locator("a[href*='urn:li']")
        if await loc.count() > 0:
            href = await loc.first.get_attribute("href")
            if href and "login" not in href:
                return href.split("?")[0]
    except Exception:
        pass
    return ""


async def _find_hashtags(page, item) -> List[str]:
    try:
        tags = await item.evaluate("""
            (el) => Array.from(el.querySelectorAll("a[href*='%23'], a[href*='hashtag']"))
                .map(a => a.textContent.trim())
                .filter(t => t && t.startsWith('#'))
        """)
        return tags or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core: scroll, load more, harvest
# ---------------------------------------------------------------------------

async def _count_listitems(page) -> int:
    try:
        return await page.locator(LISTITEM_SELECTOR).count()
    except Exception:
        return 0


async def _scroll_until_stable(
    page,
    max_scrolls: int = 60,
    stable_threshold: int = 3,
    scroll_pause: float = 1.8,
) -> None:
    stable_count = 0
    last_count = await _count_listitems(page)

    for i in range(max_scrolls):
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            time.sleep(scroll_pause / 4)

        current = await _count_listitems(page)
        log.info(f"Scroll {i+1}: {current} listitem elements found")

        if current > last_count:
            last_count = current
            stable_count = 0
        else:
            stable_count += 1
            if stable_count >= stable_threshold:
                log.info("Listitem count stable — stopping scroll")
                break

        _jitter(0.3, 0.8)


async def _click_load_more(page) -> bool:
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _jitter(1.0, 2.0)

    for selector in LOAD_MORE_SELECTORS:
        try:
            loc = page.locator(selector)
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible() and await el.is_enabled():
                    log.info(f"Clicking load-more: {selector}")
                    await el.scroll_into_view_if_needed()
                    _jitter(0.2, 0.5)
                    await el.click(force=True)
                    _jitter(2.5, 4.0)
                    return True
        except Exception:
            continue

    try:
        for btn in await page.locator("button, [role='button']").all():
            try:
                label = (await btn.text_content() or await btn.get_attribute("aria-label") or await btn.get_attribute("title") or "").strip().lower()
                if any(k in label for k in ("load more", "see more result", "show more result", "next page")):
                    if await btn.is_visible() and await btn.is_enabled():
                        log.info(f"Clicking load-more via text: '{label}'")
                        await btn.scroll_into_view_if_needed()
                        await btn.click(force=True)
                        _jitter(2.5, 4.0)
                        return True
            except Exception:
                continue
    except Exception:
        pass

    return False


async def _scroll_more(page, num_scrolls: int = 15, pause: float = 1.2) -> None:
    """Scroll down to trigger infinite scroll loading (no stable check)."""
    for i in range(num_scrolls):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
        time.sleep(pause)
        _jitter(0.2, 0.5)
    log.info(f"Scrolled {num_scrolls} more times to load additional posts")


async def _harvest_posts(
    page,
    results: List[Dict[str, Any]],
    seen: Set[int],
    max_quantity: int,
) -> int:
    time.sleep(1.5)

    try:
        items = await page.locator(LISTITEM_SELECTOR).all()
    except Exception:
        return 0

    log.info(f"Found {len(items)} listitem elements")
    if len(items) == 0 and os.getenv("LINKEDIN_DEBUG", "").strip() in ("1", "true", "yes"):
        log.warning(f"Debug: current URL = {page.url}")

    MIN_CONTENT_LEN = 15  # Minimum chars to count as valid post (was 30 — too strict)
    debug = os.getenv("LINKEDIN_DEBUG", "").strip().lower() in ("1", "true", "yes")
    added = 0

    for idx, item in enumerate(items):
        if len(results) >= max_quantity:
            break
        try:
            await _expand_listitem(page, item)

            content = await _read_listitem(page, item)
            if not content or len(content) < MIN_CONTENT_LEN:
                if debug:
                    log.debug(f"Skip item {idx+1}: content too short ({len(content or '')} chars)")
                continue

            key = hash(content[:400])
            if key in seen:
                if debug:
                    log.debug(f"Skip item {idx+1}: duplicate content")
                continue
            seen.add(key)

            post_url = await _find_post_url(page, item)
            hashtags = await _find_hashtags(page, item)

            results.append({"content": content, "post_url": post_url, "hashtags": hashtags})
            added += 1
            log.info(f"Collected post {len(results)}/{max_quantity}")

        except Exception as e:
            if debug:
                log.debug(f"Skip item {idx+1}: {e}")
            continue

    return added


# ---------------------------------------------------------------------------
# Cookies (direct session — easier than email/password)
# ---------------------------------------------------------------------------

LINKEDIN_DOMAIN = ".linkedin.com"


def _parse_cookies_from_env() -> List[Dict[str, Any]]:
    """
    Parse cookies from env. Supports:
    - LINKEDIN_LI_AT: just the li_at session cookie value (simplest)
    - LINKEDIN_COOKIES: JSON array of {name, value, domain?, path?}
    """
    cookies: List[Dict[str, Any]] = []

    # Simple: just li_at value
    li_at = os.getenv("LINKEDIN_LI_AT", "").strip()
    if li_at:
        cookies.append({
            "name": "li_at",
            "value": li_at,
            "domain": LINKEDIN_DOMAIN,
            "path": "/",
        })

    # Full: JSON array
    raw = os.getenv("LINKEDIN_COOKIES", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for c in parsed:
                    if isinstance(c, dict) and c.get("name") and c.get("value"):
                        c = dict(c)
                        c.setdefault("domain", LINKEDIN_DOMAIN)
                        c.setdefault("path", "/")
                        cookies.append(c)
        except json.JSONDecodeError:
            log.warning("LINKEDIN_COOKIES invalid JSON — ignoring")

    return cookies


async def _apply_cookies(context, page) -> bool:
    """Add cookies to context and verify we're logged in. Returns True if cookies were applied."""
    cookies = _parse_cookies_from_env()
    if not cookies:
        return False

    # Playwright requires visiting the domain before add_cookies
    await page.goto("https://www.linkedin.com", wait_until="domcontentloaded")
    _jitter(1, 2)
    await context.add_cookies(cookies)
    log.info(f"Applied {len(cookies)} cookie(s) from env")
    return True


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _login_if_needed(page, search_url: str) -> bool:
    url = page.url
    if not any(p in url for p in ("/login", "/uas/", "/checkpoint/", "/authwall", "/signup")):
        return True

    email = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    if not email or not password:
        log.warning("Login required but LINKEDIN_EMAIL/LINKEDIN_PASSWORD not set.")
        return False

    try:
        await page.wait_for_selector("#username", timeout=10000)
        await page.fill("#username", email)
        await page.fill("#password", password)
        await page.click("#password")
        await page.keyboard.press("Enter")
        _jitter(4, 6)
        await page.goto(search_url, wait_until="domcontentloaded")
        _jitter(3, 5)
        url = page.url
        return not any(p in url for p in ("/login", "/uas/", "/checkpoint/", "/authwall"))
    except Exception as e:
        log.error(f"Login failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main scrape (async)
# ---------------------------------------------------------------------------

def _is_headless_env() -> bool:
    """Force headless when no display (Render, Railway, Docker, CI)."""
    if os.environ.get("RENDER"):
        return True
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    if not os.environ.get("DISPLAY") and os.name != "nt":
        return True
    return False


async def _scrape_posts_raw_async(search_url: str, max_quantity: int = 50) -> List[Dict[str, Any]]:
    headless = os.getenv("LINKEDIN_HEADLESS", "true").lower() in ("1", "true", "yes")
    if _is_headless_env():
        headless = True  # Render/Docker: no X server, must use headless

    playwright = None
    browser = None

    try:
        from playwright.async_api import async_playwright

        try:
            from playwright_stealth import Stealth
            stealth = Stealth()
            use_stealth = True
        except ImportError:
            use_stealth = False
            log.warning("playwright-stealth not installed; running without stealth (may be detected)")

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"] if headless else [],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        if use_stealth:
            await stealth.apply_stealth_async(context)
        page = await context.new_page()

        results: List[Dict[str, Any]] = []
        seen: Set[int] = set()

        # Prefer cookies (direct session) over email/password
        cookies_applied = await _apply_cookies(context, page)
        if cookies_applied:
            _jitter(1, 2)
            await page.goto(search_url, wait_until="domcontentloaded")
            _jitter(3, 5)
            # Check if we hit login wall despite cookies (expired, etc.)
            if any(p in page.url for p in ("/login", "/uas/", "/checkpoint/", "/authwall")):
                log.warning("Cookies applied but still on login/checkpoint — falling back to email/password")
                cookies_applied = False
        if not cookies_applied:
            await page.goto(search_url, wait_until="domcontentloaded")
            _jitter(3, 5)

        if not await _login_if_needed(page, search_url):
            return []

        try:
            await page.wait_for_selector(LISTITEM_SELECTOR, timeout=15000)
            log.info("listitem elements detected — starting scrape")
        except Exception:
            log.warning("No listitem found after 15s — page may not have loaded correctly")
            log.warning(f"Current URL: {page.url}")
            _jitter(5, 8)

        page_num = 0
        rounds_without_new = 0
        while len(results) < max_quantity:
            page_num += 1
            log.info(f"=== Page {page_num} | collected {len(results)}/{max_quantity} ===")

            await _scroll_until_stable(page)
            _jitter(1.5, 2.5)

            n = await _harvest_posts(page, results, seen, max_quantity)
            log.info(f"Harvested {n} new posts (total: {len(results)}/{max_quantity})")

            if len(results) >= max_quantity:
                log.info("Reached max_quantity — stopping")
                break

            if await _click_load_more(page):
                _jitter(2, 3)
                continue

            # No load more button — keep scrolling to trigger infinite scroll
            if n == 0:
                rounds_without_new += 1
                if rounds_without_new >= 2:
                    log.info("No new posts after 2 scroll rounds — reached end of results")
                    break

            log.info("No 'load more' button — scrolling more to load additional posts")
            await _scroll_more(page, num_scrolls=15, pause=1.2)
            _jitter(2, 3)

        if len(results) == 0 and os.getenv("LINKEDIN_DEBUG", "").strip() in ("1", "true", "yes"):
            try:
                path = os.path.join(os.getcwd(), "linkedin_debug_screenshot.png")
                await page.screenshot(path=path)
                log.info(f"Debug: saved screenshot to {path} (0 posts — check for login/auth wall)")
            except Exception as e:
                log.warning(f"Debug screenshot failed: {e}")

        log.info(f"Scrape complete: {len(results)} posts collected")
        return results

    finally:
        if browser:
            await browser.close()
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API (sync)
# ---------------------------------------------------------------------------

def _scrape_posts_raw(search_url: str, max_quantity: int = 50) -> List[Dict[str, Any]]:
    return asyncio.run(_scrape_posts_raw_async(search_url, max_quantity))


def scrape_linkedin_posts(keywords: str, max_quantity: int = 50) -> List[Dict[str, Any]]:
    """Scrape posts by keyword string. Returns raw: content, post_url, hashtags."""
    return _scrape_posts_raw(_build_search_url(keywords), max_quantity)


def scrape_linkedin(keywords: List[str], quantity: int = 50, since_hours: int = 24) -> List[Dict[str, Any]]:
    """
    Scrape LinkedIn posts by keywords.
    Returns: list of {platform, username, post_url, post_content, scraped_at}
    """
    query = " ".join(kw.strip() for kw in keywords if kw.strip())
    if not query:
        return []

    raw_posts = _scrape_posts_raw(_build_search_url(query), max_quantity=quantity)
    scraped_at = datetime.utcnow().isoformat()

    return [
        {
            "platform": "linkedin",
            "username": username,
            "post_url": p.get("post_url", ""),
            "post_content": post_content,
            "scraped_at": scraped_at,
        }
        for p in raw_posts
        for username, post_content in [_parse_content_for_display(p["content"])]
    ]
