#!/usr/bin/env python3
"""
投资评论自动抓取 + 飞书推送脚本
- BlackRock 每周投资评论: requests + BeautifulSoup
- HSBC 最新市场动态: Playwright headless browser
- J.P. Morgan 财富洞察: Playwright headless browser
- Goldman Sachs Insights: Playwright headless browser
"""

import json
import os
import re
import sys
import hashlib
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============================================================
# Configuration
# ============================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
SEEN_FILE = SCRIPT_DIR / "seen.json"

def load_config():
    if not CONFIG_FILE.exists():
        print(f"[ERROR] Config file not found: {CONFIG_FILE}")
        print("Please create config.json with your Feishu webhook URL.")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)

# ============================================================
# Article key generation (for dedup)
# ============================================================

def article_key(source, title, date_str):
    """Generate a unique key for an article."""
    raw = f"{source}:{title}:{date_str}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

# ============================================================
# Translation helper (MyMemory free API)
# ============================================================

# Simple in-memory translation cache to avoid hitting the API repeatedly
_translation_cache = {}

def translate_en_to_zh(text):
    """Translate English text to Chinese using MyMemory free API.
    Returns the translated text, or the original text on failure.
    Text that already contains Chinese characters is returned as-is."""
    if not text:
        return text

    # If text already contains Chinese characters, skip translation
    if re.search(r'[一-鿿]', text):
        return text

    # Check cache
    cache_key = text[:200]  # Use first 200 chars as key (titles are short)
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|zh"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        if translated and translated != text:
            _translation_cache[cache_key] = translated
            print(f"  [Translate] {text[:50]}... -> {translated[:50]}...")
            return translated
    except Exception as e:
        print(f"  [Translate] Failed for '{text[:40]}...': {e}")

    # On failure, return original text
    _translation_cache[cache_key] = text
    return text


# ============================================================
# BlackRock fetcher
# ============================================================

def fetch_blackrock(config):
    """Fetch the latest BlackRock weekly commentary."""
    url = config["blackrock"]["url"]
    name = config["blackrock"]["name"]
    timeout = config.get("request_timeout_seconds", 30)

    print(f"[BlackRock] Fetching {url} ...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Extract from meta tags
    title_meta = soup.find("meta", attrs={"name": "articleTitle"})
    date_meta = soup.find("meta", attrs={"name": "publicationDate"})
    summary_meta = soup.find("meta", attrs={"name": "pageSummary"})

    title = title_meta["content"].strip() if title_meta else ""
    date_str = date_meta["content"].strip() if date_meta else ""
    summary = summary_meta["content"].strip() if summary_meta else ""

    if not title:
        # Fallback: try og:title
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title["content"].strip()

    print(f"  Title: {title}")
    print(f"  Date: {date_str}")
    print(f"  Summary: {summary[:100]}...")

    if not title:
        print("[BlackRock] WARNING: Could not extract article title!")
        return []

    return [{
        "title": title,
        "date": date_str,
        "summary": summary,
        "url": url,
        "source": name,
    }]


# ============================================================
# HSBC fetcher (Playwright)
# ============================================================

def fetch_hsbc(config):
    """Fetch HSBC latest market views using Playwright."""
    url = config["hsbc"]["url"]
    name = config["hsbc"]["name"]
    timeout = config.get("request_timeout_seconds", 30) * 1000  # ms

    print(f"[HSBC] Launching headless browser for {url} ...")

    # Import here so the script can at least start without playwright installed
    from playwright.sync_api import sync_playwright

    articles = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(timeout)

        try:
            page.goto(url, wait_until="domcontentloaded")
            # Wait for the main content to render (SPA hydration)
            page.wait_for_timeout(5000)

            html = page.content()
        finally:
            browser.close()

    # Parse the rendered HTML
    soup = BeautifulSoup(html, "lxml")

    # Find the "Latest-views" section anchor
    lv_anchor = soup.find(id="Latest-views")
    if not lv_anchor:
        print("[HSBC] WARNING: Could not find Latest-views section!")
        return []

    # Navigate up to find the parent M-CNT-ART-DEV that wraps the entire section
    section_wrapper = lv_anchor.find_parent("div", class_="M-CNT-ART-DEV")
    if not section_wrapper:
        print("[HSBC] WARNING: Could not find section wrapper!")
        return []

    # ---- Part 1: Extract the featured article (large hero) ----
    # It's inside the first "container-content" div within the section wrapper
    hero_container = section_wrapper.find("div", class_="container-content")
    if hero_container:
        hero_items = hero_container.find_all("div", class_="M-CNT-ITEM-ART-DEV")
        for item in hero_items:
            art = _parse_hsbc_article(item, name)
            if art:
                articles.append(art)
                print(f"  [Hero] {art['title'][:60]} -> {art['url'][:80]}")

    # ---- Part 2: Extract the 4-column grid articles ----
    # The grid is in the NEXT M-CNT-ART-DEV sibling (container-layout__25-25-25-25)
    grid_wrapper = section_wrapper.find_next("div", class_="M-CNT-ART-DEV")
    if grid_wrapper and grid_wrapper != section_wrapper:
        grid_list = grid_wrapper.find("ul", class_="container-content")
        if grid_list:
            grid_items = grid_list.find_all("li", class_="M-CNT-ITEM-ART-DEV")
            for item in grid_items:
                art = _parse_hsbc_article(item, name)
                if art:
                    articles.append(art)
                    print(f"  [Grid] {art['title'][:60]} -> {art['url'][:80]}")

    print(f"[HSBC] Extracted {len(articles)} articles from Latest-views")

    # Fallback if nothing found
    if not articles:
        print("[HSBC] Falling back to full-page extraction...")
        articles = _extract_all_hsbc_articles(html, name)

    return articles


def _parse_hsbc_article(div, source_name):
    """Parse a single HSBC article element into a dict, or None."""
    link_tag = div.find("a", href=re.compile(r"/wealth/insights/"))
    if not link_tag:
        return None

    href = link_tag.get("href", "")
    if not href:
        return None

    full_url = "https://www.hsbc.com.cn" + href if href.startswith("/") else href

    # Title from the link's span
    title_span = link_tag.find("span", class_="link")
    title = title_span.get_text(strip=True) if title_span else link_tag.get_text(strip=True)

    if not title or len(title) < 5:
        return None

    # Date / summary from nearby text div
    date_text = ""
    text_div = div.find("div", class_=re.compile(r"A-TYPS5R-RW-DEV|text-container text"))
    if text_div:
        date_text = text_div.get_text(strip=True)

    return {
        "title": title,
        "date": date_text,
        "summary": date_text,
        "url": full_url,
        "source": source_name,
    }


def _extract_all_hsbc_articles(html, name):
    """Fallback: extract all article links from the page."""
    soup = BeautifulSoup(html, "lxml")

    # Find all article links with the specific class pattern
    links = soup.find_all("a", class_=re.compile(r"A-LNKC16R-RW-ALL"), href=re.compile(r"/wealth/insights/"))
    articles = []
    seen_urls = set()

    for link in links:
        href = link.get("href", "")
        if href in seen_urls:
            continue
        seen_urls.add(href)

        if href.startswith("/"):
            full_url = "https://www.hsbc.com.cn" + href
        else:
            full_url = href

        title_span = link.find("span", class_="link")
        title = title_span.get_text(strip=True) if title_span else link.get_text(strip=True)

        if not title or len(title) < 5:
            continue

        # Try to find date near the link
        parent = link.find_parent("div", class_=re.compile("item-content"))
        date_text = ""
        if parent:
            text_div = parent.find("div", class_=re.compile(r"A-TYPS5R-RW-DEV|text-container text"))
            if text_div:
                date_text = text_div.get_text(strip=True)

        articles.append({
            "title": title,
            "date": date_text,
            "summary": date_text,
            "url": full_url,
            "source": name,
        })

    print(f"[HSBC] Fallback: found {len(articles)} articles total")
    # Limit to first 6 articles (most recent) as a reasonable number
    return articles[:6]


# ============================================================
# J.P. Morgan fetcher (Playwright)
# ============================================================

def fetch_jpmorgan(config):
    """Fetch J.P. Morgan Wealth Management insights using Playwright."""
    url = config["jpmorgan"]["url"]
    name = config["jpmorgan"]["name"]
    timeout = config.get("request_timeout_seconds", 30) * 1000  # ms

    print(f"[JPM] Launching headless browser for {url} ...")

    from playwright.sync_api import sync_playwright

    articles = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(timeout)

        try:
            page.goto(url, wait_until="domcontentloaded")
            # Wait for JS content to render
            page.wait_for_timeout(7000)

            html = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(html, "lxml")

    # JPM articles are in li.jpma-article-card elements
    cards = soup.find_all("li", class_="jpma-article-card")

    for card in cards:
        # Extract link and title
        link = card.find("a", href=re.compile(r"/insights/"))
        if not link:
            continue

        href = link.get("href", "")
        full_url = "https://www.jpmorgan.com" + href if href.startswith("/") else href

        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue

        # Extract date (in dynamic-grid__date)
        date_el = card.find(class_="dynamic-grid__date")
        date_str = date_el.get_text(strip=True) if date_el else ""

        # Extract summary (in dynamic-grid__desc)
        desc_el = card.find(class_="dynamic-grid__desc")
        summary = desc_el.get_text(strip=True) if desc_el else ""

        articles.append({
            "title": title,
            "date": date_str,
            "summary": summary,
            "url": full_url,
            "source": name,
        })
        print(f"  [JPM] {title[:60]} -> {date_str}")

    print(f"[JPM] Extracted {len(articles)} articles")

    return articles


# ============================================================
# Goldman Sachs fetcher (Playwright)
# ============================================================

def fetch_goldmansachs(config):
    """Fetch Goldman Sachs Insights using Playwright."""
    url = config["goldmansachs"]["url"]
    name = config["goldmansachs"]["name"]
    timeout = config.get("request_timeout_seconds", 30) * 1000  # ms

    print(f"[GS] Launching headless browser for {url} ...")

    from playwright.sync_api import sync_playwright

    articles = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(timeout)

        try:
            page.goto(url, wait_until="domcontentloaded")
            # Wait for JS content to render
            page.wait_for_timeout(7000)

            html = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(html, "lxml")

    # Find the article grid container
    container = soup.find(class_=lambda c: c and "tout-card-grid-article-container" in c)
    if not container:
        print("[GS] WARNING: Could not find article grid container!")
        return []

    # Find all article cards (links with /insights/ in href)
    cards = container.find_all("a", href=re.compile(r"/insights/"))

    for card in cards:
        href = card.get("href", "")
        full_url = "https://www.goldmansachs.com" + href if href.startswith("/") else href

        # Extract category (eyebrow)
        eyebrow = card.find("h3", class_="gs-card-eyebrow")
        category = eyebrow.get_text(strip=True) if eyebrow else ""

        # Extract title
        title_el = card.find("h4", class_="gs-card-title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title or len(title) < 5:
            continue

        # Extract date from card-meta area
        # The date is the span with pattern "Mon DD, YYYY" inside card-meta
        date_str = ""
        meta = card.find(class_=lambda c: c and "card-meta" in c)
        if meta:
            date_spans = meta.find_all("span", class_=lambda c: c and "text-root" in str(c))
            for span in reversed(date_spans):
                text = span.get_text(strip=True)
                if re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b", text):
                    date_str = text
                    break

        # Build summary from category
        summary = category if category else ""

        articles.append({
            "title": title,
            "date": date_str,
            "summary": summary,
            "url": full_url,
            "source": name,
        })
        print(f"  [GS] [{category}] {title[:60]} -> {date_str}")

    print(f"[GS] Extracted {len(articles)} articles")

    return articles


# ============================================================
# Feishu webhook sender
# ============================================================

def send_to_feishu(webhook_url, article, is_new=True):
    """Send an article to Feishu via webhook."""
    source = article["source"]
    title = article["title"]
    date_str = article.get("date", "")
    summary = article.get("summary", "")
    url = article["url"]

    # Translate English titles/summaries to Chinese for display
    display_title = translate_en_to_zh(title)
    display_summary = translate_en_to_zh(summary) if summary else ""

    # Truncate summary for card display
    if len(display_summary) > 300:
        display_summary = display_summary[:300] + "..."

    # Build Feishu interactive card
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{'🆕' if is_new else '📌'} [{source}] {display_title[:80]}"
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**日期**: {date_str}\n\n{display_summary}" if display_summary else f"**日期**: {date_str}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "🔗 阅读原文"
                            },
                            "url": url,
                            "type": "primary"
                        }
                    ]
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"推送时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 来源: {source}"
                        }
                    ]
                }
            ]
        }
    }

    try:
        resp = requests.post(webhook_url, json=card, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            print(f"  [Feishu] Sent OK: {title[:50]}")
            return True
        else:
            print(f"  [Feishu] API Error: {result}")
            return False
    except Exception as e:
        print(f"  [Feishu] Send failed: {e}")
        return False


# ============================================================
# Main
# ============================================================

def send_daily_summary(webhook_url, new_count, total_seen):
    """Send a daily check summary card to Feishu."""
    if new_count > 0:
        content = f"✅ 本次抓取到 **{new_count}** 篇新文章，已全部推送到群聊。\n累计已追踪 **{total_seen}** 篇文章。"
    else:
        content = f"📭 本次检查无新文章，所有内容均已在之前推送。\n累计已追踪 **{total_seen}** 篇文章。"

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"投资评论每日推送 — {datetime.now().strftime('%m月%d日')}"
                },
                "template": "green" if new_count > 0 else "grey"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content
                    }
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 来源: 贝莱德/汇丰/摩根大通/高盛"
                        }
                    ]
                }
            ]
        }
    }

    try:
        resp = requests.post(webhook_url, json=card, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            print(f"  [Feishu] Daily summary sent OK ({new_count} new)")
            return True
        else:
            print(f"  [Feishu] Daily summary API Error: {result}")
            return False
    except Exception as e:
        print(f"  [Feishu] Daily summary send failed: {e}")
        return False


def main():
    config = load_config()
    webhook_url = config.get("feishu_webhook_url", "")

    if not webhook_url or "在此填入" in webhook_url:
        print("[ERROR] Please set your Feishu webhook URL in config.json")
        sys.exit(1)

    seen = load_seen()
    all_new_articles = []

    print("=" * 60)
    print(f"Starting fetch at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ---- BlackRock ----
    try:
        br_articles = fetch_blackrock(config)
    except Exception as e:
        print(f"[BlackRock] ERROR: {e}")
        br_articles = []

    # ---- HSBC ----
    try:
        hsbc_articles = fetch_hsbc(config)
    except Exception as e:
        print(f"[HSBC] ERROR: {e}")
        hsbc_articles = []

    # ---- J.P. Morgan ----
    try:
        jpm_articles = fetch_jpmorgan(config)
    except Exception as e:
        print(f"[JPM] ERROR: {e}")
        jpm_articles = []

    # ---- Goldman Sachs ----
    try:
        gs_articles = fetch_goldmansachs(config)
    except Exception as e:
        print(f"[GS] ERROR: {e}")
        gs_articles = []

    # ---- Dedup ----
    all_articles = br_articles + hsbc_articles + jpm_articles + gs_articles
    new_articles = []
    for art in all_articles:
        key = article_key(art["source"], art["title"], art.get("date", ""))
        if key not in seen:
            new_articles.append(art)
            seen[key] = {
                "title": art["title"],
                "date": art.get("date", ""),
                "source": art["source"],
                "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    # ---- Send to Feishu ----
    if new_articles:
        print(f"\n{'='*60}")
        print(f"Sending {len(new_articles)} new article(s) to Feishu...")
        print(f"{'='*60}")
        for art in new_articles:
            send_to_feishu(webhook_url, art, is_new=True)
            time.sleep(1)  # Rate limiting
    else:
        print("\nNo new articles to send.")

    # ---- Save state ----
    save_seen(seen)

    # ---- Send daily summary to Feishu (always, even if zero new) ----
    print(f"\n{'='*60}")
    print("Sending daily summary to Feishu...")
    send_daily_summary(webhook_url, len(new_articles), len(seen))

    print(f"\nDone. Seen articles: {len(seen)}")
    print(f"New this run: {len(new_articles)}")


if __name__ == "__main__":
    main()
