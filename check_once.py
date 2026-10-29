# -*- coding: utf-8 -*-
"""
check_once.py — نسخهٔ «یک‌بار اجرا» برای GitHub Actions (مثل پندار)
------------------------------------------------------------------
یک‌بار وضعیت چالوس را می‌گیرد، با وضعیت ذخیره‌شده مقایسه می‌کند و
فقط اگر «تغییر» کرده بود، در کانال تلگرام پست می‌گذارد و state.json را به‌روز می‌کند.

این فایل را GitHub Actions هر N دقیقه صدا می‌زند. پراسس دائمی لازم نیست.
(برای حالت دائمی/سرور و پاسخ به دستور، از bot.py استفاده کن.)
"""

import os
import json
import sys

import requests

from chalous_status import get_chalous_status, format_message

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
# ALWAYS_POST=1 → حتی بدون تغییر هم پست بگذار (برای تست اولیه مفید است)
ALWAYS_POST = os.environ.get("ALWAYS_POST", "").strip() in ("1", "true", "yes")


def send(text):
    if not (BOT_TOKEN and ALERT_CHAT_ID):
        print("⚠️ TELEGRAM_BOT_TOKEN یا ALERT_CHAT_ID ست نشده — پیام ارسال نشد.")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": ALERT_CHAT_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=30,
    )
    print("ارسال تلگرام:", r.status_code)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    s = get_chalous_status()
    print("وضعیت:", s.get("status_fa"), "|", s.get("raw_title", "")[:80])

    if s["status"] == "error":
        print("خطا در دریافت؛ بدون تغییر خارج می‌شوم:", s.get("error"))
        return 0  # خطا نباید باعث fail شدن اکشن شود

    key = f"{s['status']}|{s['direction']}"
    state = load_state()
    old = state.get("last_key")

    changed = (old is not None and key != old)
    first_run = (old is None)

    if first_run and not ALWAYS_POST:
        print("اولین اجرا: وضعیت ثبت شد، هشدار نمی‌دهم.")
    elif changed:
        print(f"تغییر وضعیت: {old} → {key} — پست می‌گذارم.")
        send("🔔 *تغییر وضعیت جادهٔ چالوس!*\n\n" + format_message(s))
    elif ALWAYS_POST:
        print("ALWAYS_POST روشن است — پست می‌گذارم.")
        send(format_message(s))
    else:
        print("بدون تغییر.")

    # ذخیرهٔ وضعیت جدید (فقط وقتی چیزی عوض شده، تا commit بی‌خود نخورد)
    if first_run or changed:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_key": key, "last_status": s}, f, ensure_ascii=False, indent=2)
        print("state.json به‌روز شد.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
