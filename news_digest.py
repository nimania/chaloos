# -*- coding: utf-8 -*-
"""
news_digest.py — ارسال دایجست «خبرهای محور چالوس» به کانال (یک‌بار اجرا)
-----------------------------------------------------------------------
این فایل را یک workflow زمان‌بندی‌شده (مثلاً صبح و عصر) صدا می‌زند و
آخرین خبرهای جاده را در کانال پست می‌کند. حالت/state ندارد — هر بار پست می‌گذارد،
پس زمان‌بندی را کم بگذار (نه هر ۳۰ دقیقه).
"""

import os
import sys
import requests

from chalous_status import get_road_news, format_news

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "").strip()
NEWS_LIMIT = int(os.environ.get("NEWS_LIMIT", "5"))


def main():
    items = get_road_news(NEWS_LIMIT)
    if not items:
        print("خبری پیدا نشد؛ چیزی پست نشد.")
        return 0
    text = format_news(items)
    if not (BOT_TOKEN and ALERT_CHAT_ID):
        print("⚠️ توکن/آیدی ست نشده — فقط چاپ می‌کنم:\n")
        print(text)
        return 0
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": ALERT_CHAT_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=30,
    )
    print("ارسال دایجست خبری:", r.status_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
