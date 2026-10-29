# -*- coding: utf-8 -*-
"""
bot.py — ربات تلگرام وضعیت جادهٔ چالوس
--------------------------------------
دو کار می‌کند، همزمان و در یک پراسس:

  ۱) پاسخ به کاربر: هر کس /start یا /status یا «چالوس» یا هر پیامی بفرستد،
     وضعیت لحظه‌ای باز/بسته/یک‌طرفه را جواب می‌دهد.

  ۲) هشدار خودکار: هر CHECK_INTERVAL_MIN دقیقه وضعیت را چک می‌کند و
     فقط وقتی وضعیت «تغییر» کرد، به ALERT_CHAT_ID (کانال/چت شما) خبر می‌دهد.

فقط به requests نیاز دارد. بدون وابستگی سنگین.
اجرا: python bot.py
"""

import os
import time
import json
import traceback

import requests

from chalous_status import (
    get_chalous_status, format_message, status_emoji,
    get_road_news, format_news,
)

# ---------------------------------------------------------------------------
# تنظیمات (از متغیرهای محیطی / فایل .env خوانده می‌شود)
# ---------------------------------------------------------------------------

def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALERT_CHAT_ID = os.environ.get("ALERT_CHAT_ID", "").strip()     # خالی = هشدار خاموش
CHECK_INTERVAL_MIN = int(os.environ.get("CHECK_INTERVAL_MIN", "30"))
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

if not BOT_TOKEN:
    raise SystemExit("خطا: TELEGRAM_BOT_TOKEN ست نشده. فایل .env را بساز (نمونه: .env.example).")


# ---------------------------------------------------------------------------
# ابزار تلگرام
# ---------------------------------------------------------------------------

def tg(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=40)
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return {"ok": False, "error": str(e)}


def send(chat_id, text):
    return tg("sendMessage", chat_id=chat_id, text=text,
              parse_mode="Markdown", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# حافظهٔ وضعیت (برای تشخیص تغییر)
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def status_key(s):
    """امضای وضعیت برای مقایسه: فقط باز/بسته/یک‌طرفه + جهت."""
    return f"{s.get('status')}|{s.get('direction')}"


# ---------------------------------------------------------------------------
# بخش ۱: پاسخ به کاربر
# ---------------------------------------------------------------------------

HELP = (
    "سلام! 👋\n"
    "من فقط یک کار می‌کنم: می‌گویم جادهٔ چالوس *باز* است یا *بسته* یا *یک‌طرفه*.\n\n"
    "• /status  یا بنویس «چالوس» → وضعیت همین الان (باز/بسته/یک‌طرفه)\n"
    "• /news  یا بنویس «خبر» → آخرین خبرهای محور چالوس\n"
    "• /help → همین راهنما\n\n"
    "اگر هشدار خودکار روشن باشد، موقع تغییر وضعیت در کانال خبر می‌دهم."
)

TRIGGER_WORDS = ("چالوس", "وضعیت", "جاده", "status", "باز", "بسته")


def handle_update(upd):
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        send(chat_id, HELP)
        return

    if text.startswith("/news") or "خبر" in text:
        send(chat_id, "در حال گرفتن خبرها…")
        send(chat_id, format_news(get_road_news(6)))
        return

    if text.startswith("/status") or any(w in text for w in TRIGGER_WORDS):
        send(chat_id, "در حال بررسی…")
        s = get_chalous_status()
        send(chat_id, format_message(s))
        return

    # هر پیام دیگر
    send(chat_id, "بنویس /status برای وضعیت، یا /news برای خبرها.")


# ---------------------------------------------------------------------------
# بخش ۲: چک خودکار و هشدار تغییر
# ---------------------------------------------------------------------------

def check_and_alert():
    state = load_state()
    s = get_chalous_status()

    if s["status"] == "error":
        print("[check] خطا در دریافت، رد می‌شوم:", s.get("error"))
        return

    new_key = status_key(s)
    old_key = state.get("last_key")

    # بار اول فقط ذخیره می‌کنیم (هشدار نمی‌دهیم)
    if old_key is None:
        state["last_key"] = new_key
        state["last_status"] = s
        save_state(state)
        print("[check] وضعیت اولیه ثبت شد:", s["status_fa"])
        return

    if new_key != old_key and ALERT_CHAT_ID:
        emoji = status_emoji(s["status"])
        alert = f"🔔 *تغییر وضعیت جادهٔ چالوس!*\n\n" + format_message(s)
        send(ALERT_CHAT_ID, alert)
        print(f"[check] تغییر: {old_key} -> {new_key} (هشدار ارسال شد)")
    else:
        print(f"[check] بدون تغییر: {s['status_fa']}")

    state["last_key"] = new_key
    state["last_status"] = s
    save_state(state)


# ---------------------------------------------------------------------------
# حلقهٔ اصلی: پولینگ پیام‌ها + چک دوره‌ای
# ---------------------------------------------------------------------------

def main():
    print("ربات چالوس روشن شد.")
    print(f"هشدار خودکار: {'روشن → ' + ALERT_CHAT_ID if ALERT_CHAT_ID else 'خاموش'}")
    print(f"فاصلهٔ چک: هر {CHECK_INTERVAL_MIN} دقیقه")

    offset = None
    last_check = 0.0
    interval = CHECK_INTERVAL_MIN * 60

    # یک چک اولیه هنگام روشن شدن
    try:
        check_and_alert()
    except Exception:
        traceback.print_exc()
    last_check = time.time()

    while True:
        # گرفتن پیام‌های جدید (long-polling با timeout کوتاه)
        try:
            resp = tg("getUpdates", offset=offset, timeout=25)
            if resp.get("ok"):
                for upd in resp["result"]:
                    offset = upd["update_id"] + 1
                    try:
                        handle_update(upd)
                    except Exception:
                        traceback.print_exc()
        except Exception:
            traceback.print_exc()
            time.sleep(5)

        # چک دوره‌ای
        if time.time() - last_check >= interval:
            try:
                check_and_alert()
            except Exception:
                traceback.print_exc()
            last_check = time.time()


if __name__ == "__main__":
    main()
