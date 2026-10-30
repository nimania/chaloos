# -*- coding: utf-8 -*-
"""Publish road-related Telegram posts with their available media."""
import os
import sys
import json
import requests

from chalous_status import fetch_channel_posts, get_road_news, format_news

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
    # Preserve the full text (Telegram captions are limited to 1024 chars).
    # Public Telegram previews are not guaranteed to expose original media.
    body = post.get("text", "").strip()
    media = ([("photo", url) for url in post.get("photos", [])]
             + [("video", url) for url in post.get("videos", [])])
    if not media:
        if body:
            api("sendMessage", {"text": body[:4096], "disable_web_page_preview": True})
        return
    # Send each photo/video, retaining visual material if Telegram can fetch it.
    sent = False
    for kind, url in media[:10]:
        try:
            api("sendPhoto" if kind == "photo" else "sendVideo",
                {"photo" if kind == "photo" else "video": url})
            sent = True
        except Exception as exc:
            print("Media unavailable:", type(exc).__name__)
    if body:
        api("sendMessage", {"text": body[:4096], "disable_web_page_preview": True})
    elif not sent:
        print("Post has no downloadable media; skipped:", post.get("source_url"))


def main():
    posts = fetch_channel_posts(NEWS_LIMIT)
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
                seen.add(post["source_url"])
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"published": sorted(seen)[-1000:]}, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                print("Post could not be published:", type(exc).__name__)
        return 0

    print("No unpublished Telegram posts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
