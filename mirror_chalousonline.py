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

SOURCE = "chalousonline"
DESTINATION = os.getenv("ALERT_CHAT_ID", "@chaloosradar").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
STATE_PATH = Path(os.getenv("CHALOUSONLINE_STATE_FILE", "chalousonline_state.json"))
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
    # The original source name is an internal audit field, not copy in the post.
    lines = text.splitlines()
    while lines and (
        not lines[-1].strip()
        or re.fullmatch(r"(?:🆔\s*)?@chalousonline\s*", lines[-1].strip(), re.I)
        or re.fullmatch(r"https?://(?:t\.me|telegram\.me)/(?:s/)?chalousonline/?", lines[-1].strip(), re.I)
    ):
        lines.pop()
    return "\n".join(lines).strip()

def parse_posts(page):
    marks = list(re.finditer(r'data-post=["\x27]([^"\x27]+)["\x27]', page))
    out = {}
    for i, match in enumerate(marks):
        slug = html.unescape(match.group(1))
        channel, sep, ident = slug.rpartition("/")
        if not sep or channel.lower() != SOURCE or not ident.isdigit():
            continue
        snippet = page[match.start(): marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        tm = re.search(
            r'<div\b[^>]*class="[^"]*\btgme_widget_message_text\b[^"]*"[^>]*>(.*?)</div>',
            snippet, re.S | re.I
        )
        content = decode_text(tm.group(1)) if tm else ""
        photos = []
        for u in re.findall(r'background-image:\s*url\(["\x27]?([^)"\x27]+)', snippet, re.I):
            u = html.unescape(u)
            if u.startswith("https://") and u not in photos:
                photos.append(u)
        videos = []
        for u in re.findall(r'<(?:video|source)\b[^>]*\bsrc=["\x27]([^"\x27]+)', snippet, re.I):
            u = html.unescape(u)
            if u.startswith("https://") and u not in videos:
                videos.append(u)
        # Some video previews contain a still image; avoid sending it twice.
        if videos:
            photos = []
        media_hint = bool(re.search(
            r'tgme_widget_message_(?:photo|video|document|audio|voice|sticker|poll)',
            snippet, re.I
        ))
        out[int(ident)] = {
            "id": int(ident), "text": content, "photos": photos[:10],
            "videos": videos[:10], "has_media": media_hint,
            "source_url": "https://t.me/" + SOURCE + "/" + ident,
        }
    return list(out.values())

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

def send_text(text):
    # Telegram limits a regular text message to 4096 characters.
    while text:
        part, text = text[:3900], text[3900:]
        bot_call("sendMessage", {"text": part, "disable_web_page_preview": True})

def send_media(kind, url, post_id):
    method = "sendPhoto" if kind == "photo" else "sendVideo"
    try:
        bot_call(method, {kind: url})
        return True
    except Exception as exc:
        # Telegram often rejects CDN URL fetching (HTTP 400). Try uploading
        # downloaded bytes instead of discarding the photo or video.
        print("Direct media URL rejected:", post_id, kind, type(exc).__name__, flush=True)
    try:
        from tempfile import SpooledTemporaryFile
        with requests.get(url, headers=HEADERS, stream=True, timeout=HTTP_TIMEOUT) as source:
            source.raise_for_status()
            length = int(source.headers.get("content-length") or 0)
            cap = 45 * 1024 * 1024
            if length > cap:
                raise RuntimeError("Media exceeds safe upload limit")
            media_type = source.headers.get("Content-Type", "").split(";")[0].strip()
            if not media_type or "/" not in media_type:
                media_type = "image/jpeg" if kind == "photo" else "video/mp4"
            with SpooledTemporaryFile(max_size=5 * 1024 * 1024) as tmp:
                total = 0
                for chunk in source.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        total += len(chunk)
                        if total > cap:
                            raise RuntimeError("Media too large to upload")
                        tmp.write(chunk)
                if not total:
                    raise RuntimeError("Media download was empty")
                tmp.seek(0)
                filename = "chalousonline.jpg" if kind == "photo" else "chalousonline.mp4"
                bot_call(method, {}, files={kind: (filename, tmp, media_type)})
        return True
    except Exception as exc:
        print("Media could not be uploaded:", post_id, kind, type(exc).__name__, str(exc)[:150], flush=True)
        return False

def publish(post):
    assets = [("photo", url) for url in post["photos"]]
    assets += [("video", url) for url in post["videos"]]
    incomplete = bool(post["has_media"] and not assets)
    # Individual uploads allow a per-file fallback when Telegram cannot fetch
    # remote CDN URLs. The rate limit is respected for each channel message.
    for kind, url in assets:
        if not send_media(kind, url, post["id"]):
            incomplete = True
    body = post["text"]
    if incomplete:
        body = (body + "\n\n" if body else "") + "📎 بخشی از پیوست‌های این پست در پیش‌نمایش عمومی تلگرام قابل انتقال نبود."
    if not body and not assets:
        body = "📎 پست رسانه‌ای تازه؛ محتوای آن از پیش‌نمایش عمومی قابل دریافت نبود."
    if body:
        send_text(body)
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
    for post in posts[:80]:
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
