#!/usr/bin/env python3
"""
Standalone CLI to run LinkedIn and Reddit scrapers.
Outputs JSON to stdout. Loads .env from current directory.

Usage:
  python main.py linkedin "keyword1 keyword2"
  python main.py reddit "keyword1 keyword2"
  python main.py linkedin "designer" --since 48
  python main.py reddit "web developer" --since 24
"""
import argparse
import json
import os
import sys

# Load .env before importing scrapers (they use os.getenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main():
    parser = argparse.ArgumentParser(description="Run LinkedIn or Reddit scraper")
    parser.add_argument("platform", choices=["linkedin", "reddit"], help="Platform to scrape")
    parser.add_argument("keywords", type=str, help='Keywords (space-separated, e.g. "web developer designer")')
    parser.add_argument("--since", type=int, default=24, help="Time window in hours (default: 24)")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split() if k.strip()]
    if not keywords:
        print('{"posts": [], "error": "No keywords provided"}', file=sys.stderr)
        sys.exit(1)

    try:
        if args.platform == "linkedin":
            from linkedin import scrape_linkedin
            posts = scrape_linkedin(keywords, quantity=50, since_hours=args.since)
        else:
            from reddit import scrape_reddit
            tf = "hour" if args.since <= 1 else "day" if args.since <= 24 else "week" if args.since <= 168 else "month" if args.since <= 720 else "year"
            posts = scrape_reddit(keywords, quantity=50, time_filter=tf)

        output = {"posts": posts}
        print(json.dumps(output, indent=2))
    except Exception as e:
        print(json.dumps({"posts": [], "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
