# -*- coding: utf-8 -*-
"""Conservative, city-specific Mazandaran news monitor for Neshani Shomal.

Uses public Telegram previews from independently configured city sources.
Only brief, attributed, link-backed civic updates are auto-published.
Newly discovered third-party media is NOT re-uploaded until its reuse rights
are verified. Existing full-mirror workflows remain unchanged.
"""
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from mirror_chalousonline import parse_posts, bot_call

SOURCES_FILE = Path(os.getenv("CITY_SOURCES_FILE", "city_sources.json"))
STATE_FILE = Path(os.getenv("CITY_STATE_FILE", "city_state.json"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("ALERT_CHAT_ID", "@chaloosradar").strip()
DRY_RUN = os.getenv("CITY_DRY_RUN", "0").strip() == "1"
MAX_PAGES = int(os.getenv("CITY_MAX_PAGES", "12"))
MAX_POSTS_PER_RUN = int(os.getenv("CITY_MAX_SEND_PER_RUN", "4"))
MAX_POSTS_PER_DAY = int(os.getenv("CITY_MAX_SEND_PER_DAY", "12"))
MAX_AGE_HOURS = int(os.getenv("CITY_MAX_AGE_HOURS", "36"))
TEHRAN = ZoneInfo("Asia/Tehran")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"}

CITIES = ("ساری", "بابل", "آمل", "نوشهر", "رامسر", "تنکابن")
TOWN_ALIASES = {
    "ساری": ("ساری", "ساروی"),
    "بابل": ("بابل", "بابلی", "امیرکلا", "بابلکنار", "بندپی"),
    "آمل": ("آمل", "آملی"),
    "نوشهر": ("نوشهر", "چالوس", "مرزن آباد"),
    "رامسر": ("رامسر", "کتالم", "سادات شهر"),
    "تنکابن": ("تنکابن", "شهسوار", "شیرود", "خرم آباد"),
}
TOPICS = {
    "خدمات شهری": (
        "خاموشی", "قطعی برق", "توزیع برق", "برق منطقه", "شبکه برق",
        "قطع آب", "آب شرب", "آب و فاضلاب", "فاضلاب", "شکستگی لوله",
        "پسماند", "جمع آوری زباله", "جمع آوری پسماند", "آتش نشانی",
        "آتش سوزی", "آب گرفتگی", "آبگرفتگی", "سیلاب", "سیل",
        "شهرداری", "حمل و نقل عمومی", "حمل ونقل عمومی",
        "تاکسی", "اتوبوس", "آسفالت", "حفاری", "تعطیلی مدارس",
        "تعطیل شدن مدارس", "بیمارستان", "اورژانس", "درمانگاه",
        "سرویس مدرسه", "ساعت کاری", "پارکینگ", "طرح ترافیک",
    ),
    "بازار و کسب‌وکار": (
        "بازارچه", "بازار محلی", "نمایشگاه فروش", "قیمت میوه",
        "قیمت برنج", "قیمت مرکبات", "تأمین کالا", "تامین کالا",
        "توزیع کالا", "اشتغال", "استخدام", "صنایع دستی",
        "کارآفرین", "کسب و کار", "کشاورزی", "شیلات",
    ),
    "فرهنگ و رویداد": (
        "جشنواره", "نمایشگاه هنری", "نمایشگاه کتاب", "نمایشگاه صنایع",
        "برنامه فرهنگی", "رویداد", "مسابقات", "نمایش تئاتر",
        "کنسرت", "موزه", "ساعت بازدید",
    ),
}
EXCLUDE = (
    "قرعه کشی", "قرعه‌کشی", "کد تخفیف", "تبلیغات", "تبلیغ شما",
    "رزرو تبلیغ", "ثبت سفارش", "با ما تبلیغ", "عضویت", "فالو کنید",
    "کانال ما", "اشتراک گذاری کنید", "سفارش تبلیغات",
    "واریز", "کارت به کارت", "کمک مالی", "نامزد انتخاباتی",
)
# Geographic negatives only when they appear without an explicit city target.
NONLOCAL = ("تهران", "اصفهان", "مشهد", "شیراز", "کرمان", "تبریز", "اهواز",
            "آمریکا", "اسرائیل", "اروپا", "روسیه", "عربستان", "ترکیه")


def normalize(s):
    s = html.unescape(s or "").replace("ي", "ی").replace("ك", "ک")
    s = s.replace("\u200c", " ").replace("ـ", "").replace("\u200f", "")
    return re.sub(r"\s+", " ", s).strip()


def first_news_line(text):
    lines = [
        re.sub(r"(?:https?://\S+|@\w+)", "", line).strip(" #▪▫🔸🔹📌✅⚡️•.-")
        for line in (text or "").splitlines()
    ]
    return next((line for line in lines if len(line) >= 12), "")[:280]


def choose_city(post, source):
    text = normalize(post.get("text", ""))
    mentions = [city for city, variants in TOWN_ALIASES.items()
                if any(alias in text for alias in variants)]
    if mentions:
        if "تنکابن" in mentions and source["handle"].lower() == "ramsar11":
            return "تنکابن"
        return mentions[0]
    return source.get("city")  # For city-specific channels, coverage is implicit.


def classify(post, source):
    """Return (city, subject) for a local, useful civic post, else None."""
    raw = post.get("text", "")
    text = normalize(raw)
    if len(text) < 25:
        return None
    title = normalize(first_news_line(raw))
    if not title:
        return None
    if any(term in text for term in EXCLUDE) and not any(term in title for term in ("خاموشی", "قطع", "سیلاب")):
        return None
    city = choose_city(post, source)
    if not city:
        return None
    # A single city source may forward distant news: reject obvious outsiders
    # if the headline does not explicitly name the source's own city.
    if any(term in title for term in NONLOCAL) and not any(
            alias in title for alias in TOWN_ALIASES.get(city, ())):
        return None
    for subject, terms in TOPICS.items():
        if any(term in text for term in terms):
            # Do not republish roads as definitive current status; existing
            # road-status workflows already handle road closures/openings.
            if ("جاده چالوس" in title or "محور کندوان" in title) and any(
                    term in title for term in ("بازگشایی", "مسدود", "بسته شد", "یک طرفه")):
                return None
            return city, subject
    return None


def read_published_time(post):
    raw = post.get("published", "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def useful_and_recent(post, source, now):
    published = read_published_time(post)
    if published is None or not (now - timedelta(hours=MAX_AGE_HOURS) <= published <= now + timedelta(minutes=5)):
        return None
    return classify(post, source)


def fetch_public_page(handle, before=None):
    query = "?" + urlencode({"before": before}) if before is not None else ""
    url = "https://t.me/s/" + handle + query
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    posts = parse_posts(res.text, source=handle.lower())
    if not posts:
        raise RuntimeError("No readable messages in Telegram public preview for " + handle)
    return posts


def collect_since(handle, cursor):
    """Collect every post since a cursor without silently skipping gaps."""
    seen = {}
    before = None
    oldest_seen = set()
    for _ in range(MAX_PAGES):
        page = fetch_public_page(handle, before=before)
        for item in page:
            if item["id"] > cursor:
                seen[item["id"]] = item
        ids = [item["id"] for item in page]
        if cursor == 0:
            # Bootstrap by saving the latest current source ID, not by
            # republishing potentially obsolete municipal notices.
            return [], max(ids)
        earliest = min(ids)
        if earliest <= cursor:
            return [seen[key] for key in sorted(seen)], cursor
        if earliest in oldest_seen:
            raise RuntimeError("Repeated Telegram pagination for " + handle)
        oldest_seen.add(earliest)
        before = earliest
    raise RuntimeError("Source backlog exceeded page limit for " + handle)


def fingerprint(post):
    # Stable for republications of the same item on multiple city channels.
    cleaned = normalize(post.get("text", ""))
    cleaned = re.sub(r"(?:https?://\S+|@\w+|#\S+)", "", cleaned)
    cleaned = normalize(cleaned)[:350]
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def format_post(post, source, city, subject):
    title = first_news_line(post.get("text", ""))
    dt = read_published_time(post).astimezone(TEHRAN)
    stamp = dt.strftime("%Y-%m-%d %H:%M")
    if len(title) > 230:
        title = title[:229] + "…"
    return (
        "📍 " + city + " | " + subject + "\n\n"
        + title + "\n\n"
        + "🕒 تاریخ گزارش: " + stamp + " (تهران)\n"
        + "🗞 منبع: " + source["name"] + "\n"
        + "🔗 " + post["source_url"]
    )


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_state(data):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(STATE_FILE)


def publish_text(text):
    if DRY_RUN:
        print("DRY RUN:", text[:220], flush=True)
        return
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing Telegram credentials for city news publishing")
    result = bot_call("sendMessage", {"text": text, "disable_web_page_preview": True})
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected city post")


def main():
    sources = load_json(SOURCES_FILE, {}).get("channels", [])
    if not sources:
        raise RuntimeError("No city sources configured")
    state = load_json(STATE_FILE, {"sources": {}, "published": {}})
    old_state = json.dumps(state, sort_keys=True, ensure_ascii=False)
    state.setdefault("sources", {})
    state.setdefault("published", {})
    now = datetime.now(timezone.utc)
    today = now.astimezone(TEHRAN).date().isoformat()
    daily = state.get("daily", {})
    if daily.get("date") != today:
        daily = {"date": today, "count": 0}
    state["daily"] = daily

    # Remove obsolete cross-source signatures to keep the state file small.
    cutoff = (now - timedelta(days=10)).timestamp()
    state["published"] = {
        key: value for key, value in state["published"].items()
        if isinstance(value, (float, int)) and value >= cutoff
    }

    sent = 0
    bootstrapped = 0
    fetch_errors = []
    try:
        for source in sources:
            if source.get("status") != "active":
                continue
            handle = source["handle"].lower().lstrip("@")
            cursor = int(state["sources"].get(handle, 0))
            try:
                posts, baseline = collect_since(handle, cursor)
            except Exception as exc:
                print("CITY FETCH FAILED", handle, type(exc).__name__, str(exc)[:200], flush=True)
                fetch_errors.append(handle)
                continue
            if not cursor:
                state["sources"][handle] = baseline
                bootstrapped += 1
                print("CITY BASELINE", handle, baseline, flush=True)
                continue

            for post in posts:
                if sent >= MAX_POSTS_PER_RUN or daily["count"] >= MAX_POSTS_PER_DAY:
                    break
                decision = useful_and_recent(post, source, now)
                fp = fingerprint(post) if decision else None
                if decision and fp and fp not in state["published"]:
                    city, subject = decision
                    publish_text(format_post(post, source, city, subject))
                    state["published"][fp] = now.timestamp()
                    daily["count"] += 1
                    sent += 1
                    print("CITY SENT", handle, post["id"], city, subject, flush=True)
                else:
                    print("CITY SKIP", handle, post["id"], flush=True)
                # Advance only after each successful send or deliberate skip.
                state["sources"][handle] = post["id"]
            if sent >= MAX_POSTS_PER_RUN or daily["count"] >= MAX_POSTS_PER_DAY:
                break
    finally:
        if json.dumps(state, sort_keys=True, ensure_ascii=False) != old_state:
            state["last_run"] = now.isoformat()
            save_state(state)
    print("CITY DONE", "sent=", sent, "new_source_baselines=", bootstrapped,
          "source_errors=", ",".join(fetch_errors), "daily_count=", daily["count"], flush=True)
    # Individual sources may temporarily be inaccessible. Keep other sources
    # running; a failure has not advanced their cursor.
    if fetch_errors and len(fetch_errors) >= len([s for s in sources if s.get("status") == "active"]):
        raise RuntimeError("All configured city sources unavailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
