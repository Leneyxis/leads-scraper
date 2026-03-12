"""
Test script for linkedin.py
Usage: python test_linkedin.py
Tests scraping with keywords "web developer" and quantity 5.
"""

import os
import sys

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from linkedin import scrape_linkedin


def main():
    keywords = ["web developer"]
    quantity = 1

    print(f"Testing LinkedIn scraper: keywords={keywords!r}, quantity={quantity}")
    print("-" * 60)

    try:
        results = scrape_linkedin(keywords=keywords, quantity=quantity)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Got {len(results)} posts\n")
    for i, post in enumerate(results, 1):
        print(f"--- Post {i} ---")
        print(f"  Platform: {post.get('platform', 'N/A')}")
        print(f"  Username: {post.get('username', 'N/A')}")
        print(f"  URL: {post.get('post_url', 'N/A')}")
        content = post.get("post_content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        print(f"  Content: {preview}")
        print()

    print("-" * 60)
    print(f"Test complete: {len(results)} posts scraped")


if __name__ == "__main__":
    main()
