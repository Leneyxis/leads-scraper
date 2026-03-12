"""
LinkedIn post search scraper using Selenium.

Strategy (v6):
- Find ALL [role='listitem'] elements on the page (post containers).
- Grab all text from each listitem — includes user name and post content.
- Extract post links from <a> tags within the listitem.
- Optionally expand [data-testid='expandable-text-button'] inside listitem if present.
- Scroll-until-stable to load new posts, harvest, then paginate.
- Stops as soon as max_quantity posts have been collected (no page limit).
"""

import os
import re
import time
import random
import logging
from datetime import datetime
from typing import List, Dict, Any, Set
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
)

logging.basicConfig(level=logging.INFO, format="[linkedin] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------

LISTITEM_SELECTOR               = "[role='listitem']"
EXPANDABLE_TEXT_BUTTON_SELECTOR = "[data-testid='expandable-text-button']"

LOAD_MORE_SELECTORS = [
    "button.scaffold-finite-scroll__load-button",
    "button[aria-label*='Load more results' i]",
    "button[aria-label*='See more results' i]",
    "div.scaffold-finite-scroll__load-button",
    "span[role='button'][aria-label*='more' i]",
]

POST_LINK_SELECTORS = [
    "a[href*='/posts/']",
    "a[href*='feed/update']",
    "a[href*='urn:li:share']",
    "a[href*='urn:li:activity']",
    "a[href*='urn:li:ugcPost']",
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
# Driver setup
# ---------------------------------------------------------------------------

def _setup_driver() -> webdriver.Chrome:
    chrome_options = Options()

    if os.getenv("CHROME_BIN"):
        chrome_options.binary_location = os.getenv("CHROME_BIN")

    headless = os.getenv("LINKEDIN_HEADLESS", "true").lower() in ("1", "true", "yes")

    if headless:
        import tempfile
        profile_dir = tempfile.mkdtemp(prefix="linkedin_chrome_")
        chrome_options.add_argument("--headless=new")
    else:
        profile_dir = os.getenv("CHROME_PROFILE_DIR") or os.path.join(os.getcwd(), "chrome_profile")
        os.makedirs(profile_dir, exist_ok=True)

    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jitter(lo: float = 0.5, hi: float = 1.5) -> None:
    time.sleep(random.uniform(lo, hi))


def _safe_click(driver: webdriver.Chrome, element) -> bool:
    try:
        element.click()
        return True
    except (ElementClickInterceptedException, StaleElementReferenceException):
        pass
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        _jitter(0.2, 0.5)
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        return False


def _build_search_url(keywords: str) -> str:
    return "https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(keywords.strip())


def _parse_content_for_display(content: str) -> tuple[str, str]:
    """
    Separate username from post content. Strips "Feed post", header (name, headline, time).
    Returns (username, post_content).
    """
    raw = content.strip()
    # Strip "Feed post" prefix
    raw = re.sub(r"^Feed post\s*", "", raw, flags=re.IGNORECASE)

    # Extract username: first name-like pattern (e.g. "Mandar Hodage-Patil")
    username = "linkedin"
    name_match = _NAME_PATTERN.search(raw)
    if name_match:
        username = name_match.group(1).strip()

    # Extract post content: split by " • " — the last part is usually the actual post body
    # (header is: name • headline+time • content)
    parts = [p.strip() for p in raw.split(" • ") if p.strip()]
    post_content = raw
    if len(parts) >= 2:
        # Filter out header parts: contain "|" (headline), connection degree, or time
        header_indicators = ("|", "1st", "2nd", "3rd", "reactions", "comment", "repost")
        for part in reversed(parts):
            if not any(ind in part for ind in header_indicators) and not re.match(r"^\d+[hHdDwWmMyY]$", part):
                if len(part) >= 15:  # Skip tiny fragments
                    post_content = part
                    break

    # Fallback: strip repeated name and connection degree from start
    if post_content == raw and username != "linkedin":
        post_content = re.sub(rf"^{re.escape(username)}\s*\d*(?:st|nd|rd|th)\+?\s*", "", raw, flags=re.IGNORECASE)
        post_content = re.sub(rf"^{re.escape(username)}\s*[•·]\s*", "", post_content)
        post_content = post_content.strip()

    return (username, post_content)


# ---------------------------------------------------------------------------
# Core: expand the "…more" button inside a listitem (if present)
# ---------------------------------------------------------------------------

def _expand_listitem(driver: webdriver.Chrome, item) -> None:
    try:
        btn = item.find_element(By.CSS_SELECTOR, EXPANDABLE_TEXT_BUTTON_SELECTOR)
        if btn.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            _jitter(0.2, 0.4)
            _safe_click(driver, btn)
            _jitter(0.8, 1.5)
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core: read all text from a listitem (user name, post content, etc.)
# ---------------------------------------------------------------------------

def _read_listitem(driver: webdriver.Chrome, item) -> str:
    try:
        raw = driver.execute_script("""
            const el = arguments[0];
            const clone = el.cloneNode(true);
            clone.querySelectorAll('button').forEach(b => b.remove());
            return clone.innerText || clone.textContent || '';
        """, item) or ""
    except Exception:
        return ""

    clean_lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(re.match(pat, line, re.IGNORECASE) for pat in _NOISE_PATTERNS):
            continue
        clean_lines.append(line)

    return "\n".join(clean_lines)


# ---------------------------------------------------------------------------
# Core: find post URL and hashtags from within the listitem
# ---------------------------------------------------------------------------

def _find_post_url(driver: webdriver.Chrome, item) -> str:
    """
    Find post link from <a> tags within the listitem (and its descendants).
    """
    def _find_in(element) -> str:
        try:
            candidates = []
            for a in element.find_elements(By.TAG_NAME, "a"):
                href = (a.get_attribute("href") or "").strip()
                if not href or "login" in href or "linkedin.com" not in href:
                    continue
                if any(k in href for k in ("/posts/", "feed/update", "urn:li:share", "urn:li:activity")):
                    base = href.split("?")[0]
                    if not any(b.split("?")[0] == base for b in candidates):
                        candidates.append(href)
            if candidates:
                return candidates[0]
            for a in element.find_elements(By.CSS_SELECTOR, "a[href*='urn:li']"):
                h = (a.get_attribute("href") or "").strip()
                if h and "login" not in h:
                    return h
        except Exception:
            pass
        return ""

    # First try within the listitem itself
    url = _find_in(item)
    if url:
        return url.split("?")[0]

    # Fallback: walk up ancestors
    current = item
    for _ in range(10):
        try:
            url = _find_in(current)
            if url:
                return url.split("?")[0]
            current = current.find_element(By.XPATH, "..")
        except Exception:
            break
    return ""


def _find_hashtags(driver: webdriver.Chrome, item) -> List[str]:
    try:
        return driver.execute_script("""
            const el = arguments[0];
            return Array.from(el.querySelectorAll("a[href*='%23'], a[href*='hashtag']"))
                .map(a => a.textContent.trim())
                .filter(t => t.startsWith('#'));
        """, item) or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core: scroll until no new listitems appear
# ---------------------------------------------------------------------------

def _count_listitems(driver: webdriver.Chrome) -> int:
    try:
        return len(driver.find_elements(By.CSS_SELECTOR, LISTITEM_SELECTOR))
    except Exception:
        return 0


def _scroll_until_stable(
    driver: webdriver.Chrome,
    max_scrolls: int = 60,
    stable_threshold: int = 3,
    scroll_pause: float = 1.8,
) -> None:
    stable_count = 0
    last_count = _count_listitems(driver)

    for i in range(max_scrolls):
        for _ in range(4):
            driver.execute_script("window.scrollBy(0, window.innerHeight * 0.8);")
            time.sleep(scroll_pause / 4)

        current = _count_listitems(driver)
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


# ---------------------------------------------------------------------------
# Core: click "load more results" pagination
# ---------------------------------------------------------------------------

def _click_load_more(driver: webdriver.Chrome) -> bool:
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    _jitter(1.0, 2.0)

    for selector in LOAD_MORE_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if el.is_displayed() and el.is_enabled():
                        log.info(f"Clicking load-more: {selector}")
                        if _safe_click(driver, el):
                            _jitter(2.5, 4.0)
                            return True
                except StaleElementReferenceException:
                    continue
        except Exception:
            continue

    try:
        for btn in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            try:
                label = (
                    btn.text
                    or btn.get_attribute("aria-label")
                    or btn.get_attribute("title")
                    or ""
                ).strip().lower()
                if any(k in label for k in ("load more", "see more result", "show more result", "next page")):
                    if btn.is_displayed() and btn.is_enabled():
                        log.info(f"Clicking load-more via text: '{label}'")
                        if _safe_click(driver, btn):
                            _jitter(2.5, 4.0)
                            return True
            except StaleElementReferenceException:
                continue
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Core: harvest all listitems currently in DOM (user name, content, links)
# ---------------------------------------------------------------------------

def _harvest_posts(
    driver: webdriver.Chrome,
    results: List[Dict[str, Any]],
    seen: Set[int],
    max_quantity: int,
) -> int:
    """
    Find every [role='listitem'] on the page. Grab all text (user name, post content)
    and extract post links from within each listitem.
    Stops as soon as results reaches max_quantity.
    Returns number of new posts added.
    """
    time.sleep(1.5)

    try:
        items = driver.find_elements(By.CSS_SELECTOR, LISTITEM_SELECTOR)
    except Exception:
        return 0

    log.info(f"Found {len(items)} listitem elements")
    added = 0

    for item in items:
        if len(results) >= max_quantity:
            break
        try:
            _expand_listitem(driver, item)

            content = _read_listitem(driver, item)
            if not content or len(content) < 30:
                continue

            key = hash(content[:400])
            if key in seen:
                continue
            seen.add(key)

            post_url = _find_post_url(driver, item)
            hashtags = _find_hashtags(driver, item)

            results.append({"content": content, "post_url": post_url, "hashtags": hashtags})
            added += 1
            log.info(f"Collected post {len(results)}/{max_quantity}")

        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    return added


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _login_if_needed(driver: webdriver.Chrome, search_url: str) -> bool:
    url = driver.current_url
    if not any(p in url for p in ("/login", "/uas/", "/checkpoint/", "/authwall", "/signup")):
        return True

    email = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    if not email or not password:
        log.warning("Login required but LINKEDIN_EMAIL/LINKEDIN_PASSWORD not set.")
        return False

    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "username")))
        driver.find_element(By.ID, "username").send_keys(email)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "password").submit()
        _jitter(4, 6)
        driver.get(search_url)
        _jitter(3, 5)
        url = driver.current_url
        return not any(p in url for p in ("/login", "/uas/", "/checkpoint/", "/authwall"))
    except Exception as e:
        log.error(f"Login failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main scrape entry point
# ---------------------------------------------------------------------------

def _scrape_posts_raw(search_url: str, max_quantity: int = 50) -> List[Dict[str, Any]]:
    """
    Scrape until max_quantity posts are collected or results are exhausted.
    Paginates automatically as needed.
    """
    driver = _setup_driver()
    results: List[Dict[str, Any]] = []
    seen: Set[int] = set()

    try:
        driver.get(search_url)
        _jitter(3, 5)

        if not _login_if_needed(driver, search_url):
            return []

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, LISTITEM_SELECTOR))
            )
            log.info("listitem elements detected — starting scrape")
        except TimeoutException:
            log.warning("No listitem found after 15s — page may not have loaded correctly")
            _jitter(5, 8)

        page = 0
        while len(results) < max_quantity:
            page += 1
            log.info(f"=== Page {page} | collected {len(results)}/{max_quantity} ===")

            _scroll_until_stable(driver)
            _jitter(1.5, 2.5)

            n = _harvest_posts(driver, results, seen, max_quantity)
            log.info(f"Harvested {n} new posts (total: {len(results)}/{max_quantity})")

            if len(results) >= max_quantity:
                log.info("Reached max_quantity — stopping")
                break

            if not _click_load_more(driver):
                log.info("No 'load more' button — reached end of results")
                break

            _jitter(2, 3)

        log.info(f"Scrape complete: {len(results)} posts collected")
        return results

    finally:
        driver.quit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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