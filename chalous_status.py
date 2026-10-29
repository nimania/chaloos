# -*- coding: utf-8 -*-
"""
chalous_status.py
-----------------
هستهٔ سیستم: وضعیت «باز / بسته / یک‌طرفه» بودن جادهٔ چالوس را
از منابع خبری عمومی (که سامانهٔ ۱۴۱ / مرکز مدیریت راه‌ها را بازتاب می‌دهند)
می‌خواند و به یک خروجی تمیز و ماشین‌خوان تبدیل می‌کند.

چون API رسمی و باز وجود ندارد، منطق کار این است:
  ۱) یک متن تازه که دربارهٔ «محور چالوس» حرف زده را از منبع می‌گیریم.
  ۲) جمله‌های مربوط به چالوس را جدا می‌کنیم.
  ۳) با واژگان رسمی (بازگشایی/مسدود/یک‌طرفه/...) وضعیت را دسته‌بندی می‌کنیم.
  ۴) اگر مبهم بود و کلید LLM ست شده باشد، از مدل زبانی کمک می‌گیریم.

فقط به کتابخانهٔ requests نیاز دارد.
"""

import os
import re
import html
import json
import datetime
import xml.etree.ElementTree as ET

import requests

# ---------------------------------------------------------------------------
# تنظیمات منبع
# ---------------------------------------------------------------------------

# منبع اصلی: جست‌وجوی خبری گوگل (RSS). تیتر خبرها خودشان وضعیت را می‌گویند:
# «جاده چالوس بازگشایی شد» / «محور چالوس مسدود شد» / «جاده چالوس یک‌طرفه شد».
# اگر سرور شما به news.google.com دسترسی ندارد، NEWS_RSS_URL را در .env عوض کنید
# یا از منبع دوم (بلد) استفاده می‌شود.
DEFAULT_NEWS_RSS = (
    "https://news.google.com/rss/search?"
    "q=%D8%AC%D8%A7%D8%AF%D9%87%20%D8%86%D8%A7%D9%84%D9%88%D8%B3%20"  # «جاده چالوس»
    "%D9%88%D8%B6%D8%B9%DB%8C%D8%AA&hl=fa&gl=IR&ceid=IR:fa"
)
# نکته: کوئری دقیق پایین در _build_news_url ساخته می‌شود؛ این فقط پیش‌فرض است.

# کانال تلگرام منبع (بدون @)، مثلاً chaloscity — از صفحهٔ عمومی t.me/s/ خوانده می‌شود.
# این منبع اصلی است؛ اگر خالی باشد مستقیم می‌رود سراغ خبر.
TG_CHANNEL = os.environ.get("TG_CHANNEL", "chaluscity1").strip().lstrip("@")

NEWS_RSS_URL = os.environ.get("NEWS_RSS_URL", "").strip()
BALAD_FALLBACK_URL = os.environ.get(
    "BALAD_FALLBACK_URL",
    "https://balad.ir/blog/?s=%D8%AC%D8%A7%D8%AF%D9%87+%DA%86%D8%A7%D9%84%D9%88%D8%B3",
)

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "20"))
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# LLM اختیاری برای خبرهای مبهم.
# گزینهٔ ۱: Gemini (همان کلید پندار). AI_API_KEY می‌تواند چند کلید با کاما باشد؛ اولی برداشته می‌شود.
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY", "") or os.environ.get("AI_API_KEY", "")).split(",")[0].strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
# گزینهٔ ۲: هر endpoint سازگار با OpenAI.
LLM_API_BASE = os.environ.get("LLM_API_BASE", "").strip()   # مثل https://api.openai.com/v1
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()


# ---------------------------------------------------------------------------
# نرمال‌سازی متن فارسی
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    # یکسان‌سازی حروف عربی/فارسی
    text = text.replace("ي", "ی").replace("ى", "ی")  # ي ، ى -> ی
    text = text.replace("ك", "ک")                              # ك -> ک
    text = text.replace("‌", " ")   # نیم‌فاصله -> فاصله (برای تطبیق ساده‌تر)
    text = text.replace("‏", " ").replace("‎", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# واژگان کلیدی (بعد از نرمال‌سازی، همه بدون نیم‌فاصله)
KW_OPEN_RESOLVE = [        # نشانهٔ «الان باز است» با بالاترین اولویت
    "بازگشایی", "باز شد", "باز است", "رفع انسداد", "رفع محدودیت",
    "بازگشایی شد", "دوطرفه بازگشایی", "برقرار شد", "تردد برقرار",
]
KW_OPEN_SOFT = [           # نشانهٔ نرم‌ترِ باز بودن
    "دوطرفه", "دو طرفه", "روان", "باز",
]
KW_ONEWAY = ["یکطرفه", "یک طرفه"]
KW_CLOSED = ["مسدود", "انسداد", "بسته شد", "بسته است", "بسته شده", "بسته"]

DIR_S2N = "جنوب به شمال"
DIR_N2S = "شمال به جنوب"


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def _chalous_sentences(text: str):
    """جمله‌هایی که مشخصاً دربارهٔ محور چالوس هستند را برمی‌گرداند."""
    norm = normalize(text)
    parts = re.split(r"[.؛;!\n]| اما | و همچنین ", norm)
    hits = []
    for p in parts:
        if "چالوس" in p:
            hits.append(p.strip())
    # اگر هیچ جمله‌ای جدا نشد ولی کل متن چالوس دارد، کل متن را بده
    if not hits and "چالوس" in norm:
        hits = [norm]
    return hits


# ---------------------------------------------------------------------------
# دسته‌بندی وضعیت از روی متن
# ---------------------------------------------------------------------------

def classify_text(text: str) -> dict:
    """
    خروجی:
      status: open | closed | one_way | unknown
      status_fa, direction, direction_fa, evidence, confidence
    """
    sentences = _chalous_sentences(text)
    blob = " ".join(sentences) if sentences else normalize(text)

    direction = None
    direction_fa = None
    if DIR_S2N in blob:
        direction, direction_fa = "south_to_north", DIR_S2N
    if DIR_N2S in blob:
        # اگر هر دو بودند، آخری‌ها را نگه می‌داریم؛ معمولاً جهتِ یک‌طرفه کنار «یکطرفه» می‌آید
        direction, direction_fa = "north_to_south", DIR_N2S

    evidence = sentences[0] if sentences else ""

    # اولویت ۱: نشانهٔ صریح «الان باز شد / بازگشایی»
    if _has(blob, KW_OPEN_RESOLVE):
        return _result("open", "باز (دوطرفه)", None, None, evidence, 0.9)

    # اولویت ۲: یک‌طرفه
    if _has(blob, KW_ONEWAY):
        # جهتِ بازِ یک‌طرفه را حدس بزن: دنبال «یکطرفه ... <جهت>» بگرد
        oneway_dir, oneway_dir_fa = _oneway_direction(blob)
        return _result("one_way", "یک‌طرفه", oneway_dir or direction,
                       oneway_dir_fa or direction_fa, evidence, 0.85)

    # اولویت ۳: بسته / مسدود
    if _has(blob, KW_CLOSED):
        return _result("closed", "بسته (مسدود)", direction, direction_fa, evidence, 0.85)

    # اولویت ۴: نشانهٔ نرمِ باز بودن (روان/دوطرفه)
    if _has(blob, KW_OPEN_SOFT):
        return _result("open", "باز (دوطرفه)", None, None, evidence, 0.6)

    return _result("unknown", "نامشخص", direction, direction_fa, evidence, 0.0)


def _oneway_direction(blob: str):
    m = re.search(r"یک\s?طرفه[^.؛]{0,40}?(جنوب به شمال|شمال به جنوب)", blob)
    if not m:
        m = re.search(r"(جنوب به شمال|شمال به جنوب)[^.؛]{0,20}?یک\s?طرفه", blob)
    if m:
        d = m.group(1)
        return ("south_to_north" if d == DIR_S2N else "north_to_south"), d
    return None, None


def _result(status, status_fa, direction, direction_fa, evidence, confidence):
    return {
        "status": status,
        "status_fa": status_fa,
        "direction": direction,
        "direction_fa": direction_fa,
        "evidence": evidence,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# گرفتن متن تازه از منابع
# ---------------------------------------------------------------------------

def _build_news_url() -> str:
    if NEWS_RSS_URL:
        return NEWS_RSS_URL
    from urllib.parse import quote
    q = quote("جاده چالوس وضعیت باز بسته یک‌طرفه")
    return f"https://news.google.com/rss/search?q={q}&hl=fa&gl=IR&ceid=IR:fa"


def fetch_from_news_rss():
    """جدیدترین آیتم خبری دربارهٔ چالوس را از RSS برمی‌گرداند (title, desc, link, published)."""
    url = _build_news_url()
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = root.findall(".//item")
    best = None
    for it in items:
        title = (it.findtext("title") or "")
        if "چالوس" not in normalize(title) and "چالوس" not in normalize(it.findtext("description") or ""):
            continue
        pub = it.findtext("pubDate") or ""
        best = {
            "title": title,
            "description": re.sub(r"<[^>]+>", " ", it.findtext("description") or ""),
            "link": it.findtext("link") or "",
            "published": pub,
        }
        break  # آیتم‌ها از تازه به قدیم‌اند؛ اولی = جدیدترین
    return best


STATUS_HINT_WORDS = (
    "باز", "بسته", "مسدود", "انسداد", "یکطرفه", "یک طرفه",
    "دوطرفه", "دو طرفه", "بازگشایی", "روان",
)


# واژه‌های مرتبط با خبر جاده (نه فقط وضعیت)
ROAD_WORDS = ("چالوس", "محور", "جاده", "ترافیک", "هراز", "فیروزکوه",
              "آزادراه", "کندوان", "مرزن آباد", "کرج")


def _clean_html(h: str) -> str:
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    return normalize(h)


def _parse_tg_messages(body: str):
    """
    پیام‌های صفحهٔ t.me/s/<channel> را به صورت لیست [{post, text, published}]
    و به ترتیب قدیم→جدید برمی‌گرداند. برای هر پیام جداگانه (مقاوم در برابر پیام‌های بی‌متن).
    """
    out = []
    marks = list(re.finditer(r'data-post="([^"]+)"', body))
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[start:end]
        tm = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            chunk, flags=re.DOTALL,
        )
        text = _clean_html(tm.group(1)) if tm else ""
        tt = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        out.append({"post": m.group(1), "text": text, "published": tt.group(1) if tt else ""})
    return out


def _get_channel_body():
    if not TG_CHANNEL:
        return None
    url = f"https://t.me/s/{TG_CHANNEL}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_from_telegram_channel():
    """جدیدترین پیام کانال که دربارهٔ «وضعیت» محور است (برای تشخیص باز/بسته)."""
    body = _get_channel_body()
    if not body:
        return None
    msgs = _parse_tg_messages(body)
    # از جدید به قدیم: اول پیامی که هم چالوس دارد هم واژهٔ وضعیت
    for m in reversed(msgs):
        if "چالوس" in m["text"] and _has(m["text"], STATUS_HINT_WORDS):
            return _tg_item(m)
    for m in reversed(msgs):
        if "چالوس" in m["text"]:
            return _tg_item(m)
    return None


def _tg_item(m):
    link = f"https://t.me/{m['post']}" if m.get("post") else f"https://t.me/{TG_CHANNEL}"
    return {"title": m["text"][:400], "description": "", "link": link, "published": m["published"]}


def fetch_channel_news(limit=6):
    """چند پست اخیر کانال که به جاده مربوط‌اند (برای /news) — جدید به قدیم."""
    body = _get_channel_body()
    if not body:
        return []
    msgs = _parse_tg_messages(body)
    items = []
    for m in reversed(msgs):
        if m["text"] and _has(m["text"], ROAD_WORDS):
            items.append({
                "text": m["text"],
                "link": f"https://t.me/{m['post']}" if m.get("post") else f"https://t.me/{TG_CHANNEL}",
                "published": m["published"],
                "source": "چالوس سیتی",
            })
        if len(items) >= limit:
            break
    return items


def fetch_from_balad():
    """نسخهٔ پشتیبان: صفحهٔ جست‌وجوی بلد را می‌گیرد و متن را برمی‌گرداند."""
    r = requests.get(BALAD_FALLBACK_URL, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    # فقط حدود محدوده‌ای که «چالوس» در آن آمده
    norm = normalize(text)
    idx = norm.find("چالوس")
    snippet = norm[max(0, idx - 200): idx + 400] if idx >= 0 else norm[:600]
    return {"title": snippet, "description": "", "link": BALAD_FALLBACK_URL, "published": ""}


# ---------------------------------------------------------------------------
# LLM اختیاری برای موارد مبهم
# ---------------------------------------------------------------------------

_LLM_PROMPT = (
    "متن زیر خبری دربارهٔ وضعیت راه‌هاست. فقط و فقط وضعیت «محور چالوس (کرج–چالوس)» را "
    "مشخص کن. خروجی را دقیقاً به صورت JSON بده با کلیدهای:\n"
    '{"status": "open|closed|one_way|unknown", "direction": "south_to_north|north_to_south|null", "reason": "..."}\n'
    "open = باز/دوطرفه، closed = مسدود/بسته، one_way = یک‌طرفه.\n\n"
    "متن:\n{txt}"
)


def _llm_result_from_json(data: dict):
    status = data.get("status", "unknown")
    fa = {"open": "باز (دوطرفه)", "closed": "بسته (مسدود)",
          "one_way": "یک‌طرفه", "unknown": "نامشخص"}.get(status, "نامشخص")
    d = data.get("direction")
    d = None if d in (None, "null", "") else d
    d_fa = {"south_to_north": DIR_S2N, "north_to_south": DIR_N2S}.get(d)
    return _result(status, fa, d, d_fa, data.get("reason", ""), 0.8)


def classify_with_gemini(text: str):
    if not GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    try:
        r = requests.post(url, timeout=HTTP_TIMEOUT + 15, json={
            "contents": [{"parts": [{"text": _LLM_PROMPT.format(txt=text[:1500])}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        })
        r.raise_for_status()
        content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _llm_result_from_json(json.loads(content))
    except Exception:
        return None


def classify_with_llm(text: str):
    # اول Gemini (کلید پندار)، بعد OpenAI-compatible
    g = classify_with_gemini(text)
    if g:
        return g
    if not (LLM_API_BASE and LLM_API_KEY):
        return None
    prompt = _LLM_PROMPT.format(txt=text[:1500])
    try:
        r = requests.post(
            f"{LLM_API_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=HTTP_TIMEOUT + 15,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        status = data.get("status", "unknown")
        fa = {"open": "باز (دوطرفه)", "closed": "بسته (مسدود)",
              "one_way": "یک‌طرفه", "unknown": "نامشخص"}.get(status, "نامشخص")
        d = data.get("direction")
        d = None if d in (None, "null", "") else d
        d_fa = {"south_to_north": DIR_S2N, "north_to_south": DIR_N2S}.get(d)
        return _result(status, fa, d, d_fa, data.get("reason", ""), 0.8)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# تابع اصلی عمومی
# ---------------------------------------------------------------------------

def get_chalous_status() -> dict:
    """
    وضعیت فعلی چالوس را به صورت یک dict کامل برمی‌گرداند.
    در صورت خطا، status = 'error' و فیلد error پر می‌شود.
    """
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()
    src = None
    err = None
    for fetcher in (fetch_from_telegram_channel, fetch_from_news_rss, fetch_from_balad):
        try:
            src = fetcher()
            if src:
                break
        except Exception as e:  # noqa
            err = str(e)
            continue

    if not src:
        return {
            "status": "error",
            "status_fa": "خطا در دریافت اطلاعات",
            "direction": None, "direction_fa": None,
            "evidence": "", "confidence": 0.0,
            "source": "", "published": "",
            "checked_at": now.isoformat(timespec="minutes"),
            "error": err or "no source reachable",
        }

    text = f"{src.get('title','')} . {src.get('description','')}"
    result = classify_text(text)

    # اگر مبهم بود، LLM را امتحان کن
    if result["status"] == "unknown":
        llm = classify_with_llm(text)
        if llm:
            result = llm

    result["source"] = src.get("link", "")
    result["published"] = src.get("published", "")
    result["checked_at"] = now.isoformat(timespec="minutes")
    result["raw_title"] = src.get("title", "")
    return result


def status_emoji(status: str) -> str:
    return {"open": "🟢", "closed": "🔴", "one_way": "🟡",
            "unknown": "⚪️", "error": "⚠️"}.get(status, "⚪️")


def format_message(s: dict) -> str:
    """متن تلگرامی خوانا از روی نتیجه می‌سازد."""
    emoji = status_emoji(s["status"])
    lines = [f"{emoji} *وضعیت جادهٔ چالوس:* {s['status_fa']}"]
    if s.get("direction_fa"):
        lines.append(f"↪️ جهت: {s['direction_fa']}")
    if s.get("raw_title"):
        lines.append(f"\n🗞 {normalize(s['raw_title'])[:200]}")
    if s.get("published"):
        lines.append(f"🕒 خبر: {s['published']}")
    lines.append(f"⏱ بررسی: {s['checked_at']}")
    if s.get("source"):
        lines.append(f"🔗 منبع: {s['source']}")
    lines.append("\n_توجه: مرجع نهایی همیشه پلیس راه، تابلوهای مسیر و سامانهٔ ۱۴۱ است._")
    return "\n".join(lines)


def fetch_news_items(limit=6):
    """چند خبر اخیر دربارهٔ چالوس از RSS خبری — [{text, link, published, source}]."""
    try:
        url = _build_news_url()
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []
    items = []
    for it in root.findall(".//item"):
        title = it.findtext("title") or ""
        if "چالوس" not in normalize(title):
            continue
        # گوگل‌نیوز معمولاً منبع را بعد از ' - ' در تیتر می‌گذارد
        src = ""
        if " - " in title:
            title, src = title.rsplit(" - ", 1)
        items.append({
            "text": title.strip(),
            "link": it.findtext("link") or "",
            "published": it.findtext("pubDate") or "",
            "source": src.strip() or "خبرگزاری",
        })
        if len(items) >= limit:
            break
    return items


# --- پاک‌سازی و خلاصه‌سازی خبرها (هر اتفاق جدا، بدون کپیِ عینِ منبع) ---

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "←-⇿⬀-⯿️‍•▪●✔❌]"
)
_NOISE_HINT = ("عضو", "جوین", "join", "لینک کانال", "آیدی", "اینستاگرام", "اینستا",
               "تبلیغ", "ثبت سفارش", "رزرو", "تماس", "واتساپ", "ادمین", "@", "t.me", "http")


def _strip_noise(text: str) -> str:
    """ایموجی، هشتگ، لینک و خطوط تبلیغاتی/کانالی را حذف می‌کند."""
    t = _EMOJI_RE.sub("", text)
    t = re.sub(r"#\S+", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    keep = []
    for ln in re.split(r"[\n،]", t):
        s = ln.strip(" -—•|*")
        if len(s) < 3:
            continue
        if any(w in s for w in _NOISE_HINT):
            continue
        keep.append(s)
    return normalize(" ".join(keep))   # اگر همه‌چیز تبلیغ بود، خالی می‌شود و فیلتر می‌شود


_FILLER_PREFIX = re.compile(r"^(فوری|عاجل|مهم|توجه|خبر فوری|اطلاعیه|خبر)\s*[:|\-–]*\s*")


def _dedup_core(txt: str) -> str:
    t = _FILLER_PREFIX.sub("", txt)
    t = re.sub(r"[^؀-ۿ ]", " ", t)   # فقط حروف فارسی
    t = re.sub(r"\s+", " ", t).strip()
    return t[:30]


def _clean_news_rule(items, limit):
    """حالت بدون Gemini: پاک‌سازی + حذف تکراری + یک آیتم کوتاه برای هر خبر (لینکِ کانال حذف می‌شود)."""
    out, seen = [], set()
    for it in items:
        txt = _strip_noise(it.get("text", ""))
        txt = _FILLER_PREFIX.sub("", txt).lstrip(" |-–—")   # «فوری |» و امثالش را از نمایش هم بردار
        if len(txt) < 10:
            continue
        key = _dedup_core(txt)
        if not key or key in seen:
            continue
        seen.add(key)
        if len(txt) > 150:
            txt = txt[:150].rstrip() + "…"
        link = it.get("link", "")
        if "t.me" in link:   # نقل از چالوس سیتی را نمی‌آوریم
            link = ""
        out.append({"text": txt, "link": link})
        if len(out) >= limit:
            break
    return out


def summarize_news_gemini(items, limit):
    """با Gemini هر اتفاق را یک‌بار و با بازنویسیِ تمیز درمی‌آورد (بدون کپیِ عینِ منبع)."""
    if not GEMINI_API_KEY or not items:
        return None
    raw = "\n".join(f"{i+1}) {normalize(it.get('text',''))[:300]}" for i, it in enumerate(items))
    prompt = (
        "چند پست/خبر خام دربارهٔ راه‌های شمال (محور چالوس و مسیرهای مرتبط) در زیر آمده. "
        "یک فهرست تمیز بساز:\n"
        "- هر «اتفاق» فقط یک‌بار، در یک جملهٔ کوتاه و بی‌طرف فارسی. متن را بازنویسی کن، عینِ منبع را کپی نکن.\n"
        "- موارد تکراری را ادغام کن.\n"
        "- بدون ایموجی، هشتگ و تبلیغ. فقط اتفاقات مرتبط با جاده/ترافیک/وضعیت/آب‌وهوای محور.\n"
        f"- حداکثر {limit} مورد، از تازه و مهم به کم‌اهمیت.\n"
        'خروجی دقیقاً JSON: {"news": ["جملهٔ ۱", "جملهٔ ۲"]}\n\n'
        "متن‌های خام:\n" + raw
    )
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
        r = requests.post(url, timeout=HTTP_TIMEOUT + 20, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        })
        r.raise_for_status()
        content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        news = json.loads(content).get("news", [])
        return [{"text": normalize(n), "link": ""} for n in news if n and len(n) > 5][:limit]
    except Exception:
        return None


def get_road_news(limit=6):
    """خبرهای جاده، تمیز و بدون تکرار. اگر کلید Gemini باشد بازنویسی می‌شود؛ وگرنه فقط پاک‌سازی."""
    raw = []
    try:
        raw += fetch_channel_news(limit * 3)   # خام بیشتر تا ادغام/انتخاب بهتر شود
    except Exception:
        pass
    try:
        raw += fetch_news_items(limit)
    except Exception:
        pass
    return summarize_news_gemini(raw, limit) or _clean_news_rule(raw, limit)


def format_news(items) -> str:
    if not items:
        return "فعلاً خبر تازه‌ای دربارهٔ محور چالوس پیدا نشد."
    lines = ["🛣 *آخرین خبرهای محور چالوس*", ""]
    for it in items:
        lines.append(f"• {it['text']}")
        if it.get("link"):
            lines.append(f"  {it['link']}")
    lines.append("")
    lines.append("_به‌روزرسانی خودکار · مرجع نهایی: پلیس راه و سامانهٔ ۱۴۱_")
    return "\n".join(lines)


if __name__ == "__main__":
    # اجرای مستقیم برای تست: python chalous_status.py
    result = get_chalous_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n--- پیام تلگرام ---\n")
    print(format_message(result))
