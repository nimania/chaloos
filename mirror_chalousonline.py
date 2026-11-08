# -*- coding: utf-8 -*-
"""Mirror every new PUBLIC @ChalousOnline post to the Chaloos Radar channel.

Unlike the road-news digest, this feed does not filter by topic or keywords.
Public Telegram HTML is not a complete media API; unavailable attachments are
reported rather than silently dropped. Old posts are not bulk-imported on setup.
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from propaganda import is_pure_propaganda

SOURCE = os.getenv("SOURCE_CHANNEL", "chalousonline").strip().lstrip("@").lower()
DESTINATION = os.getenv("ALERT_CHAT_ID", "@chaloosradar").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
STATE_PATH = Path(os.getenv("MIRROR_STATE_FILE", os.getenv("CHALOUSONLINE_STATE_FILE", "chalousonline_state.json")))
BOOTSTRAP = os.getenv("MIRROR_BOOTSTRAP", "skip").strip().lower()
MIRROR_FILTER = os.getenv("MIRROR_FILTER", "all").strip().lower()
MAX_PAGES = int(os.getenv("MAX_SOURCE_PAGES", "40"))
HTTP_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
BASE = "https://t.me/s/" + SOURCE

def decode_text(fragment):
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:p|div|li)>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")
    fragment = re.sub(r"[ \t]+", " ", fragment)
    fragment = re.sub(r"\n[ \t]*\n{2,}", "\n\n", fragment).strip()
    return strip_source_footer(fragment)

def strip_source_footer(text):
    """Remove the channel's repetitive promotional footer, not the news text."""
    lines = text.splitlines()
    handle = re.compile(r"(?:🆔\s*)?@" + re.escape(SOURCE) + r"\s*$", re.I)
    link = re.compile(r"https?://(?:t\.me|telegram\.me)/(?:s/)?" + re.escape(SOURCE) + r"/?\s*$", re.I)
    while lines and (not lines[-1].strip() or handle.fullmatch(lines[-1].strip()) or link.fullmatch(lines[-1].strip())):
        lines.pop()
    if lines and re.search(r"کان[\sـ]*ال\s+ج[\sـ]*اده\s+چ[\sـ]*ال[\sـ]*وس", lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()

def parse_posts(page, source=None):
    source = (source or SOURCE).strip().lstrip('@').lower()
    marks = list(re.finditer(r'data-post=["\x27]([^"\x27]+)["\x27]', page))
    out = {}
    for i, match in enumerate(marks):
        slug = html.unescape(match.group(1))
        channel, sep, ident = slug.rpartition("/")
        if not sep or channel.lower() != source or not ident.isdigit():
            continue
        snippet = page[match.start(): marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        tm = re.search(
            r'<div\b[^>]*class="[^"]*\btgme_widget_message_text\b[^"]*"[^>]*>(.*?)</div>',
            snippet, re.S | re.I
        )
        content = decode_text(tm.group(1)) if tm else ""
        time_tag = re.search(r'<time\b[^>]*\bdatetime=["\x27]([^"\x27]+)', snippet, re.I)
        published = time_tag.group(1) if time_tag else ""
        # Read actual photo wrappers and video players in their visual order.
        # Generic background images can be *video thumbnails*, not photos.
        media_order = []
        seen_media = set()
        for tag in re.finditer(r"<(?:a|div)\b[^>]*>", snippet, re.I | re.S):
            if "tgme_widget_message_photo_wrap" not in tag.group(0):
                continue
            photo = re.search(r'background-image:\s*url\(["\x27]?([^)"\x27]+)', html.unescape(tag.group(0)), re.I)
            if photo:
                url = html.unescape(photo.group(1))
                if url.startswith("https://") and url not in seen_media:
                    media_order.append((tag.start(), "photo", url))
                    seen_media.add(url)
        for tag in re.finditer(r"<(?:video|source)\b[^>]*>", snippet, re.I | re.S):
            match_url = re.search(r'\bsrc=["\x27]([^"\x27]+)', html.unescape(tag.group(0)), re.I)
            if match_url:
                url = html.unescape(match_url.group(1))
                if url.startswith("https://") and url not in seen_media:
                    media_order.append((tag.start(), "video", url))
                    seen_media.add(url)
        # A public Telegram photo may not have a standard photo_wrap class.
        # Use background images only if there is no video player in this post.
        if not media_order and not re.search(r"tgme_widget_message_video", snippet, re.I):
            for m in re.finditer(r'background-image:\s*url\(["\x27]?([^)"\x27]+)', snippet, re.I):
                url = html.unescape(m.group(1))
                if url.startswith("https://") and url not in seen_media:
                    media_order.append((m.start(), "photo", url))
                    seen_media.add(url)
        media_order.sort(key=lambda item: item[0])
        assets = [(kind, url) for _, kind, url in media_order]
        photos = [url for kind, url in assets if kind == "photo"]
        videos = [url for kind, url in assets if kind == "video"]
        # When the original post combines photos and videos, do not discard
        # its photos merely because video data is also present.
        inaccessible_video = bool(re.search(
            r'tgme_widget_message_video', snippet, re.I
        )) and not videos
        inaccessible_other = bool(re.search(
            r'tgme_widget_message_(?:document|audio|voice|sticker|poll)', snippet, re.I
        ))
        media_hint = bool(re.search(
            r'tgme_widget_message_(?:photo|video|document|audio|voice|sticker|poll)',
            snippet, re.I
        ))
        out[int(ident)] = {
            "id": int(ident), "text": content, "published": published, "photos": photos,
            "videos": videos, "assets": assets, "has_media": media_hint,
            "hidden_media": inaccessible_video or inaccessible_other,
            "source_url": "https://t.me/" + source + "/" + ident,
        }
    return list(out.values())


def should_publish(post, mode=None):
    """Restrict weather-source items to regional weather and road conditions.

    The normal JadehChalus and ChalousOnline mirrors remain unfiltered.
    A skipped source ID still advances the independent per-source cursor,
    so routine off-topic posts do not get reconsidered forever.
    """
    if is_pure_propaganda(post.get("text", "")):
        return False
    mode = (mode or MIRROR_FILTER).lower()
    if mode == "all":
        return True
    if mode != "regional_weather":
        raise ValueError("Unknown mirror filter: " + mode)
    text = post.get("text", "")
    text = html.unescape(text).lower().replace("ي", "ی").replace("ك", "ک")
    text = text.replace("\u200c", " ").replace("ـ", "")
    if not text.strip():
        return False  # No location/forecast context in a media-only post.
    # The channel explicitly tags international weather stories this way.
    if re.search(r"#\s*منهای[\s_]*مازندران", text):
        return False
    # Only locally actionable weather reporting, forecasts, and hazards.
    region = (
        "مازندران", "چالوس", "کندوان", "مرزن آباد", "مرزناباد",
        "کلاردشت", "نوشهر", "نمک آبرود", "رامسر", "تنکابن",
        "عباس آباد", "سلمانشهر", "متل قو", "نور", "محمودآباد",
        "آمل", "بابل", "بابلسر", "ساری", "قائمشهر", "بهشهر",
        "سوادکوه", "هراز", "فیروزکوه", "سیاه بیشه",
        "آزادراه تهران شمال", "دریای کاسپین", "دریای خزر",
    )
    weather = (
        "هواشناسی", "پیش بینی", "پیش بینی", "پیش‌بینی",
        "رگبار", "بارندگی", "بارش", "باران", "برف", "یخبندان",
        "یخ زدگی", "مه گرفتگی", "تگرگ", "رعدوبرق", "رعد و برق",
        "طوفان", "توفان", "وزش باد", "سرما", "سرمای", "هوای سرد",
        "گرما", "دمای", "دما", "وضعیت دریا", "هشدار جوی",
        "آب و هوا", "آب‌وهوا", "سیلاب", "سیل", "ارتفاعات",
        "ابر", "جبهه", "روزنگار", "مواج", "موج", "دید افقی",
        "هشدار هوا", "پیش آگاهی", "پیش‌آگاهی",
    )
    # Restrict to weather or road hazards even if a local town is named.
    if not any(x in text for x in weather):
        return False
    # Local reference is preferable. Broad forecasts without explicit
    # province names are also valid from this dedicated local-weather feed,
    # but international/other-province posts must not leak into the mirror.
    foreign = (
        "شیلی", "سانتیاگو", "ژاپن", "آمریکا", "اروپا", "کانادا",
        "هند", "چین", "استرالیا", "ترکیه", "پاکستان",
        "تهران", "اصفهان", "خوزستان", "فارس", "کرمان", "البرز",
    )
    if not any(x in text for x in region) and any(x in text for x in foreign):
        return False
    return True


def fetch_page(before=None):
    query = "?" + urlencode({"before": before}) if before else ""
    r = requests.get(BASE + query, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    posts = parse_posts(r.text)
    if not posts:
        raise RuntimeError("Public Telegram page has no readable posts; no cursor advanced.")
    return posts

def load_state():
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return int(value.get("last_id", 0))
    except (FileNotFoundError, ValueError, TypeError):
        return 0

def save_state(post_id):
    data = {"channel": SOURCE, "last_id": post_id}
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)

def collect_new(cursor):
    """Page backward until cursor; abort rather than silently skip a large backlog."""
    gathered, before, seen, reached = {}, None, set(), False
    for page_number in range(MAX_PAGES):
        messages = fetch_page(before)
        ids = [p["id"] for p in messages]
        for p in messages:
            if p["id"] > cursor:
                gathered[p["id"]] = p
        earliest = min(ids)
        if cursor == 0:
            if BOOTSTRAP == "recent":
                # Import all posts on the currently visible public page on first run.
                # Deep backfills need a separate safe import to avoid flooding users.
                return [gathered[k] for k in sorted(gathered)], 0, False
            return [], max(ids), True
        if earliest <= cursor:
            reached = True
            break
        if earliest in seen:
            raise RuntimeError("Telegram pagination repeated the same page.")
        seen.add(earliest)
        before = earliest
    if not reached:
        raise RuntimeError("Telegram backlog exceeds MAX_SOURCE_PAGES; refusing to skip posts.")
    return [gathered[k] for k in sorted(gathered)], cursor, False

# Pace channel messages, and obey retry_after if Telegram reports a flood limit.
LAST_REQUEST = 0.0

def bot_call(method, payload, files=None):
    global LAST_REQUEST
    endpoint = "https://api.telegram.org/bot" + BOT_TOKEN + "/" + method
    for attempt in range(6):
        idle = time.monotonic() - LAST_REQUEST
        if idle < 2.5:
            time.sleep(2.5 - idle)
        LAST_REQUEST = time.monotonic()
        if files:
            for filedef in files.values():
                filedef[1].seek(0)
            r = requests.post(endpoint, data={"chat_id": DESTINATION, **payload}, files=files, timeout=90)
        else:
            r = requests.post(endpoint, json={"chat_id": DESTINATION, **payload}, timeout=70)
        try:
            response = r.json()
        except ValueError:
            response = {}
        if r.status_code == 429:
            pause = int(response.get("parameters", {}).get("retry_after", 8 + attempt * 4))
            print("Telegram rate limit; retry after", pause, "seconds", flush=True)
            time.sleep(min(180, max(3, pause + 1)))
            continue
        if r.status_code >= 500 and attempt < 5:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        if not response.get("ok"):
            raise RuntimeError(response.get("description", "Telegram API rejected post."))
        return response
    raise RuntimeError("Telegram rate-limit retry attempts exceeded.")

def _utf16_units(text):
    """Telegram limits captions and messages in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def split_telegram_text(text, limit):
    """Avoid splitting an emoji or a non-BMP codepoint across chunks."""
    used = 0
    for i, char in enumerate(text):
        unit_count = 2 if ord(char) > 0xFFFF else 1
        if used + unit_count > limit:
            # Prefer a whitespace boundary near the end of the allowed span.
            boundary = max(text.rfind("\n", 0, i), text.rfind(" ", 0, i))
            if boundary >= i // 2:
                return text[:boundary + 1], text[boundary + 1:]
            return text[:i], text[i:]
        used += unit_count
    return text, ""


def send_text(text, reply_to_message_id=None):
    """Reply to the associated media rather than creating an orphaned text post."""
    while text:
        part, text = split_telegram_text(text, 3900)
        payload = {"text": part, "disable_web_page_preview": True}
        if reply_to_message_id:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        response = bot_call("sendMessage", payload)
        if not reply_to_message_id:
            reply_to_message_id = response.get("result", {}).get("message_id")
    return reply_to_message_id


def _download_media(kind, url, stack):
    """Download a media file when Telegram's CDN URL cannot be fetched by Bot API."""
    from tempfile import SpooledTemporaryFile
    source = stack.enter_context(requests.get(
        url, headers=HEADERS, stream=True, timeout=HTTP_TIMEOUT
    ))
    source.raise_for_status()
    cap = 45 * 1024 * 1024
    if int(source.headers.get("content-length") or 0) > cap:
        raise RuntimeError("Media exceeds 45MB upload limit")
    mime = source.headers.get("Content-Type", "").split(";")[0].strip()
    if not mime or "/" not in mime:
        mime = "image/jpeg" if kind == "photo" else "video/mp4"
    tmp = stack.enter_context(SpooledTemporaryFile(max_size=5 * 1024 * 1024))
    size = 0
    for chunk in source.iter_content(chunk_size=256 * 1024):
        if chunk:
            size += len(chunk)
            if size > cap:
                raise RuntimeError("Media exceeds 45MB upload limit")
            tmp.write(chunk)
    if not size:
        raise RuntimeError("Media download was empty")
    tmp.seek(0)
    extension = ".jpg" if kind == "photo" else ".mp4"
    return (SOURCE + extension, tmp, mime)


def send_media(kind, url, post_id, caption=""):
    """Send one media item with caption, returning its Telegram message ID."""
    method = "sendPhoto" if kind == "photo" else "sendVideo"
    payload = {kind: url}
    if caption:
        payload["caption"] = caption
    try:
        response = bot_call(method, payload)
        return response["result"]["message_id"]
    except Exception as exc:
        print("Remote media URL rejected:", post_id, kind, str(exc)[:150], flush=True)
    try:
        from contextlib import ExitStack
        with ExitStack() as stack:
            upload = _download_media(kind, url, stack)
            payload = {"caption": caption} if caption else {}
            response = bot_call(method, payload, files={kind: upload})
            return response["result"]["message_id"]
    except Exception as exc:
        print("Media upload failed:", post_id, kind, type(exc).__name__, str(exc)[:180], flush=True)
        return None


def send_media_group(assets, post_id, caption=""):
    """Publish 2-10 photos/videos as a single Telegram album.

    Return message IDs on success, or None so the caller can fall back to
    one-by-one uploads. This function never partially publishes an album.
    """
    if not 2 <= len(assets) <= 10:
        raise ValueError("Telegram albums require 2-10 media items")
    remote_media = []
    for i, (kind, url) in enumerate(assets):
        item = {"type": kind, "media": url}
        if i == 0 and caption:
            item["caption"] = caption
        remote_media.append(item)
    try:
        response = bot_call("sendMediaGroup", {"media": remote_media})
        return [item["message_id"] for item in response["result"]]
    except Exception as exc:
        print("Remote album URL rejected:", post_id, str(exc)[:150], flush=True)

    # Upload from bytes for Telegram CDN URLs it cannot retrieve directly.
    try:
        from contextlib import ExitStack
        with ExitStack() as stack:
            files = {}
            local_media = []
            for i, (kind, url) in enumerate(assets):
                files["asset" + str(i)] = _download_media(kind, url, stack)
                item = {"type": kind, "media": "attach://asset" + str(i)}
                if i == 0 and caption:
                    item["caption"] = caption
                local_media.append(item)
            response = bot_call("sendMediaGroup", {
                "media": json.dumps(local_media, ensure_ascii=False)
            }, files=files)
            return [item["message_id"] for item in response["result"]]
    except Exception as exc:
        print("Album upload failed:", post_id, type(exc).__name__, str(exc)[:180], flush=True)
        return None


def publish(post):
    """Keep the original text and its photos/videos together in the destination.

    A short text is the caption. For longer text, attach the first 1000 UTF-16
    units to the media and send the remainder as a reply to that media, never
    as an unrelated channel post. Multiple files form an album whenever possible.
    """
    if is_pure_propaganda(post.get("text", "")):
        return
    assets = post.get("assets")
    if assets is None:
        assets = [("photo", url) for url in post.get("photos", [])]
        assets += [("video", url) for url in post.get("videos", [])]
    incomplete = bool(
        (post.get("has_media") and not assets) or post.get("hidden_media")
    )
    body = post.get("text", "").strip()
    caption, continuation = split_telegram_text(body, 1000) if assets else ("", body)
    anchor = None
    caption_sent = False

    # Preserve the full album, splitting only when Telegram's 10-item cap applies.
    for index in range(0, len(assets), 10):
        batch = assets[index:index + 10]
        batch_caption = caption if not caption_sent else ""
        if len(batch) > 1:
            message_ids = send_media_group(batch, post["id"], caption=batch_caption)
            if message_ids:
                anchor = anchor or message_ids[0]
                caption_sent = caption_sent or bool(batch_caption)
                continue
        # If an album cannot be sent, upload each item and keep the caption on
        # the first *successfully delivered* item, not the first attempted item.
        for kind, url in batch:
            item_caption = caption if not caption_sent else ""
            message_id = send_media(kind, url, post["id"], caption=item_caption)
            if message_id:
                anchor = anchor or message_id
                caption_sent = caption_sent or bool(item_caption)
            else:
                incomplete = True

    if anchor:
        remainder = continuation if caption_sent else body
    else:
        remainder = body
    if incomplete:
        warning = (
            "📎 بخشی از فایل‌های این پست از نمای عمومی تلگرام قابل دریافت نبود. "
            "مشاهدهٔ نسخهٔ اصلی: " + post["source_url"]
        )
        remainder = (remainder + "\n\n" if remainder else "") + warning
    if not anchor and not remainder and post.get("has_media"):
        remainder = (
            "📎 پست رسانه‌ای که فایل آن از نمای عمومی قابل دریافت نبود: "
            + post["source_url"]
        )
    if remainder:
        send_text(remainder, reply_to_message_id=anchor)
    return incomplete

def main():
    if not BOT_TOKEN or not DESTINATION:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or ALERT_CHAT_ID is missing.")
    cursor = load_state()
    posts, baseline, initial = collect_new(cursor)
    if initial:
        # Begin monitoring at the latest visible post, without flooding old news.
        save_state(baseline)
        print("Initial cursor saved:", baseline, "No old posts published.", flush=True)
        return 0
    partial = 0
    # The older road-news digest has already published selected posts from this
    # source. On bootstrap, skip those exact links to avoid channel duplicates.
    legacy_links = set()
    if cursor == 0 and SOURCE == "jadeh_chalus":
        try:
            legacy_links = set(json.loads(Path("news_state.json").read_text(encoding="utf-8")).get("published", []))
        except (OSError, ValueError):
            pass
    for post in posts[:80]:
        if not should_publish(post):
            save_state(post["id"])
            print("Filtered off-topic post:", post["id"], "mode=", MIRROR_FILTER, flush=True)
            continue
        if post["source_url"] in legacy_links:
            save_state(post["id"])
            print("Previously published by digest:", post["id"], flush=True)
            continue
        partial += int(publish(post))
        save_state(post["id"])  # Commit each success so retries do not duplicate older posts.
        print("Mirrored:", post["id"], "media_partial=", bool(partial), flush=True)
        time.sleep(0.5)
    print("Completed: new_posts=", min(80, len(posts)), "remaining_for_later=", max(0, len(posts)-80), "posts_with_missing_media=", partial, flush=True)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Mirror failed:", type(exc).__name__, str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
