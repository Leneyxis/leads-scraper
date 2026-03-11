"""
LinkedIn post search scraper using Selenium.
Searches posts by keywords, extracts content and post URLs.
"""
import os
import time
from datetime import datetime
from typing import List, Dict, Any
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
    

def _setup_driver() -> webdriver.Chrome:
    chrome_options = Options()
    # Use system Chromium in Docker (Railway/Render)
    if os.getenv("CHROME_BIN"):
        chrome_options.binary_location = os.getenv("CHROME_BIN")
    headless = os.getenv("LINKEDIN_HEADLESS", "true").lower() in ("1", "true", "yes")
    if headless:
        # Headless in Docker: use temp profile (no session persistence)
        import tempfile
        profile_dir = tempfile.mkdtemp(prefix="linkedin_chrome_")
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
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
    return webdriver.Chrome(options=chrome_options)


def _build_search_url(keywords: str) -> str:
    base = "https://www.linkedin.com/search/results/content/?keywords="
    return base + quote_plus(keywords.strip())


def _extract_username_from_content(content: str) -> str:
    """Extract author name from post content (first line often has it)."""
    lines = content.strip().split("\n")
    for line in lines[:3]:
        line = line.strip()
        if line and len(line) < 80 and "•" not in line and "reactions" not in line.lower():
            return line
    return "linkedin"


def _scrape_posts_raw(search_url: str) -> List[Dict[str, Any]]:
    """Scrape posts from LinkedIn search URL. Returns raw format: content, post_url, hashtags."""
    driver = _setup_driver()
    try:
        driver.get(search_url)
        time.sleep(3)

        if "login" in driver.current_url or "uas/login" in driver.current_url:
            email = os.getenv("LINKEDIN_EMAIL", "").strip()
            password = os.getenv("LINKEDIN_PASSWORD", "").strip()
            if not email or not password:
                print("[linkedin] ⚠️  Login required but LINKEDIN_EMAIL/LINKEDIN_PASSWORD not set. Returning empty results.")
                return []
            email_el = driver.find_element(By.ID, "username")
            pass_el = driver.find_element(By.ID, "password")
            email_el.clear()
            email_el.send_keys(email)
            pass_el.clear()
            pass_el.send_keys(password)
            pass_el.submit()
            time.sleep(5)
            driver.get(search_url)
            time.sleep(3)

        for _ in range(3):
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "li.reusable-search__result-container, button[data-testid='expandable-text-button']")
                    )
                )
                break
            except Exception:
                time.sleep(5)
        else:
            time.sleep(10)

        for round_num in range(3):
            for _ in range(25):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                driver.execute_script("window.scrollBy(0, 600);")
                driver.execute_script("""
                    var el = document.querySelector('.scaffold-layout__main, [role="main"]');
                    if (el) el.scrollTop = el.scrollHeight;
                """)
                time.sleep(1.2)
            for _ in range(3):
                try:
                    for btn in driver.find_elements(By.CSS_SELECTOR, 'button[data-testid="expandable-text-button"]'):
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                            time.sleep(0.2)
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(0.3)
                        except Exception:
                            continue
                    break
                except Exception:
                    time.sleep(1)
            time.sleep(2)

        results: List[Dict[str, Any]] = []
        seen_hashes: set = set()

        def _extract_post_link(container) -> str:
            def _find_in(el):
                candidates = []
                try:
                    for a in el.find_elements(By.TAG_NAME, "a"):
                        href = (a.get_attribute("href") or "").strip()
                        if not href or "login" in href or "linkedin.com" not in href:
                            continue
                        if "/posts/" in href or "feed/update" in href or "urn:li:share" in href or "urn:li:activity" in href:
                            base = href.split("?")[0]
                            if not any(b.split("?")[0] == base for b in candidates):
                                candidates.append(href)
                    if candidates:
                        return candidates[0]
                    for a in el.find_elements(By.CSS_SELECTOR, "a[href*='urn:li']"):
                        h = (a.get_attribute("href") or "").strip()
                        if h and "login" not in h:
                            return h
                except Exception:
                    pass
                return ""
            current = container
            for _ in range(10):
                try:
                    url = _find_in(current)
                    if url:
                        return url
                    current = current.find_element(By.XPATH, "..")
                except Exception:
                    break
            return ""

        def _extract_hashtags(container) -> List[str]:
            tags = []
            try:
                for a in container.find_elements(By.CSS_SELECTOR, "a[href*='%23']"):
                    t = a.text.strip()
                    if t.startswith("#"):
                        tags.append(t)
            except Exception:
                pass
            return tags

        def _add_post(container, results_list, seen_set) -> bool:
            try:
                content = container.text.strip()
                if not content or len(content) < 15:
                    return False
                h = hash(content[:500])
                if h in seen_set:
                    return False
                seen_set.add(h)
                post_url = _extract_post_link(container)
                hashtags = _extract_hashtags(container)
                results_list.append({"content": content, "post_url": post_url, "hashtags": hashtags})
                return True
            except Exception:
                return False

        def _extract_all() -> int:
            n = 0
            for btn in driver.find_elements(By.CSS_SELECTOR, 'button[data-testid="expandable-text-button"]'):
                container = None
                for xpath in [
                    "./ancestor::div[contains(@class, 'update-components-text')][1]",
                    "./ancestor::div[contains(@class, 'feed-shared') or contains(@class, 'update-components')][1]",
                    "./ancestor::li[contains(@class, 'reusable-search') or contains(@class, 'result')][1]",
                    "./ancestor::div[contains(@class, 'feed') or contains(@class, 'update')][1]",
                    "./..", "./../..", "./../../..", "./../../../..", "./../../../../..",
                ]:
                    try:
                        c = btn.find_element(By.XPATH, xpath)
                        if len(c.text.strip()) >= 50:
                            container = c
                            break
                    except Exception:
                        continue
                if container and _add_post(container, results, seen_hashes):
                    n += 1
            for li in driver.find_elements(By.CSS_SELECTOR, "li.reusable-search__result-container"):
                if _add_post(li, results, seen_hashes):
                    n += 1
            for div in driver.find_elements(By.CSS_SELECTOR, "div.feed-shared-update-v2"):
                if _add_post(div, results, seen_hashes):
                    n += 1
            return n

        _extract_all()

        for attempt in range(10):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            clicked = driver.execute_script("""
                var btns = document.querySelectorAll('button, [role="button"]');
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].textContent || btns[i].getAttribute('aria-label') || '').toLowerCase();
                    if (t.indexOf('load more') >= 0) {
                        btns[i].scrollIntoView({block: 'center'});
                        btns[i].click();
                        return true;
                    }
                }
                return false;
            """)
            if not clicked:
                break
            time.sleep(4)
            for btn in driver.find_elements(By.CSS_SELECTOR, 'button[data-testid="expandable-text-button"]'):
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.3)
                except Exception:
                    pass
            _extract_all()

        if len(results) == 0:
            for div in driver.find_elements(By.CSS_SELECTOR, "div[class*='feed'], div[class*='update'], div[class*='result']"):
                try:
                    txt = div.text.strip()
                    if 100 < len(txt) < 8000 and ("reactions" in txt.lower() or "Follow" in txt or "comments" in txt.lower()):
                        _add_post(div, results, seen_hashes)
                except Exception:
                    continue

        return results
    finally:
        driver.quit()


def scrape_linkedin_posts(keywords: str) -> List[Dict[str, Any]]:
    """Scrape posts by keyword string. Returns raw: content, post_url, hashtags."""
    return _scrape_posts_raw(_build_search_url(keywords))


def scrape_linkedin(keywords: List[str], quantity: int = 50, since_hours: int = 24) -> List[Dict[str, Any]]:
    """
    Scrape LinkedIn posts by keywords. Returns format expected by backend:
    platform, username, post_url, post_content, scraped_at
    """
    query = " ".join(kw.strip() for kw in keywords if kw.strip())
    if not query:
        return []

    search_url = _build_search_url(query)
    raw_posts = _scrape_posts_raw(search_url)
    scraped_at = datetime.utcnow().isoformat()

    results = [
        {
            "platform": "linkedin",
            "username": _extract_username_from_content(p["content"]),
            "post_url": p.get("post_url", ""),
            "post_content": p["content"],
            "scraped_at": scraped_at,
        }
        for p in raw_posts
    ]
    return results[:quantity]
