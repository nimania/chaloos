# -*- coding: utf-8 -*-
"""Publish road-related Telegram posts with their available media."""
import os
import sys
import requests

from chalous_status import fetch_channel_posts, get_road_news, format_news

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "").strip()
NEWS_LIMIT = int(os.environ.get("NEWS_LIMIT", "5"))


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
            except Exception as exc:
                print("Post could not be published:", type(exc).__name__)
        return 0

    # When source channels fail, preserve the original news digest fallback.
    items = get_road_news(NEWS_LIMIT)
    if items:
        api("sendMessage", {"text": format_news(items), "parse_mode": "Markdown",
                            "disable_web_page_preview": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
