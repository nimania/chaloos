# -*- coding: utf-8 -*-
"""Publish road-related Telegram posts with their available media."""
import os
import sys
import json
import requests

from chalous_status import fetch_channel_posts
from mirror_chalousonline import send_media, send_text

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "").strip()
NEWS_LIMIT = int(os.environ.get("NEWS_LIMIT", "5"))
STATE_FILE = os.environ.get("NEWS_STATE_FILE", "news_state.json")


def api(method, payload):
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json={"chat_id": ALERT_CHAT_ID, **payload}, timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise ValueError(data.get("description", "Telegram API error"))
    return data


def publish_post(post):
    """Publish every text segment and all publicly accessible media."""
    body = post.get("text", "").strip()
    media = ([("photo", url) for url in post.get("photos", [])]
             + [("video", url) for url in post.get("videos", [])])
    sent = False
    missing = False
    for kind, url in media:
        if send_media(kind, url, post.get("source_url", "")):
            sent = True
        else:
            missing = True
    if missing:
        body += ("\n\n" if body else "") + (
            "📎 بعضی فایل‌های رسانه‌ای از پیش‌نمایش عمومی قابل دریافت نبودند. "
            "نسخه اصلی: " + post.get("source_url", "")
        )
    if body:
        send_text(body)
    elif not sent:
        print("Public media unavailable:", post.get("source_url"), flush=True)


def main():
    # @jadeh_chalus has its own complete no-filter mirror. Avoid duplicates
    # while preserving the independent road-news digest for other sources.
    posts = [p for p in fetch_channel_posts(max(40, NEWS_LIMIT * 8))
             if p.get("source") != "jadeh_chalus"][:NEWS_LIMIT]
    print(f"Media test: found {len(posts)} matching public posts", flush=True)
    for p in posts:
        print(f"Media: photos={len(p.get('photos', []))}, videos={len(p.get('videos', []))}, source={p.get('source_url', '')}", flush=True)
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"published": []}
    seen = set(state.get("published", []))
    posts = [p for p in posts if p.get("source_url") not in seen]
    if not (BOT_TOKEN and ALERT_CHAT_ID):
        print("Telegram credentials not set. Matching posts:", len(posts))
        for post in posts:
            print(post["text"][:200], "media:", len(post["photos"]), len(post["videos"]))
        return 0

    if posts:
        # Chronological order in destination
        for post in reversed(posts):
            try:
                publish_post(post)
                print("Published post:", post.get("source_url"), flush=True)
                seen.add(post["source_url"])
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"published": sorted(seen)[-1000:]}, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                print("Post could not be published:", type(exc).__name__, str(exc)[:350], flush=True)
        return 0

    print("No unpublished Telegram posts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
