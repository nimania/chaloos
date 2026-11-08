# -*- coding: utf-8 -*-
"""Offline regression tests: media and post text stay together."""
import unittest
from unittest.mock import patch
import mirror_chalousonline as mirror


class TelegramPublishingTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.next_id = 100

    def fake_bot_call(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        if method == "sendMediaGroup":
            self.next_id += 10
            return {"ok": True, "result": [
                {"message_id": self.next_id + i}
                for i in range(len(payload["media"]))
            ]}
        self.next_id += 1
        return {"ok": True, "result": {"message_id": self.next_id}}

    def post(self, **kwargs):
        data = {
            "id": 123, "text": "پیام آزمایشی",
            "photos": [], "videos": [],
            "has_media": True, "hidden_media": False,
            "source_url": "https://t.me/jadeh_chalus/123",
        }
        data.update(kwargs)
        return data

    def test_short_video_uses_caption_not_extra_message(self):
        with patch.object(mirror, "bot_call", side_effect=self.fake_bot_call):
            mirror.publish(self.post(videos=["https://example.org/video.mp4"]))
        self.assertEqual([c[0] for c in self.calls], ["sendVideo"])
        self.assertEqual(self.calls[0][1]["caption"], "پیام آزمایشی")

    def test_long_photo_caption_and_reply_preserve_every_character(self):
        body = "الف" * 950 + "🌤️" + "ب" * 300
        with patch.object(mirror, "bot_call", side_effect=self.fake_bot_call):
            mirror.publish(self.post(text=body, photos=["https://example.org/p.jpg"]))
        self.assertEqual([c[0] for c in self.calls], ["sendPhoto", "sendMessage"])
        first = self.calls[0][1]["caption"]
        continuation = self.calls[1][1]["text"]
        self.assertLessEqual(mirror._utf16_units(first), 1000)
        self.assertEqual(first + continuation, body)
        self.assertEqual(self.calls[1][1]["reply_parameters"]["message_id"], 101)

    def test_multiple_assets_are_an_album_with_one_caption(self):
        with patch.object(mirror, "bot_call", side_effect=self.fake_bot_call):
            mirror.publish(self.post(photos=["https://example.org/a.jpg", "https://example.org/b.jpg"],
                                     videos=["https://example.org/a.mp4"]))
        self.assertEqual([c[0] for c in self.calls], ["sendMediaGroup"])
        media = self.calls[0][1]["media"]
        self.assertEqual([item["type"] for item in media], ["photo", "photo", "video"])
        self.assertEqual(media[0]["caption"], "پیام آزمایشی")
        self.assertTrue(all("caption" not in item for item in media[1:]))

    def test_album_failure_keeps_caption_on_first_successful_item(self):
        captions = []
        replies = []
        def fail_then_succeed(kind, url, post_id, caption=""):
            captions.append(caption)
            return None if len(captions) == 1 else 431
        with patch.object(mirror, "send_media_group", return_value=None), \
             patch.object(mirror, "send_media", side_effect=fail_then_succeed), \
             patch.object(mirror, "send_text", side_effect=lambda text, reply_to_message_id=None:
                          replies.append((text, reply_to_message_id))):
            missing = mirror.publish(self.post(photos=["one", "two"]))
        self.assertTrue(missing)
        self.assertEqual(captions, ["پیام آزمایشی", "پیام آزمایشی"])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][1], 431)

    def test_parse_keeps_source_order_for_mixed_media(self):
        page = (
            '<div data-post="chalousonline/123">'
            '<video src="https://example.org/a.mp4"></video>'
            '<a class="tgme_widget_message_photo_wrap" '
            'style="background-image:url(&#39;https://example.org/b.jpg&#39;)"></a>'
            '</div>'
        )
        items = mirror.parse_posts(page)
        self.assertEqual(items[0]["assets"], [
            ("video", "https://example.org/a.mp4"),
            ("photo", "https://example.org/b.jpg"),
        ])

    def test_video_thumbnail_is_not_reposted_as_photo(self):
        page = (
            '<div data-post="chalousonline/456">'
            '<span class="tgme_widget_message_video" '
            'style="background-image:url(&#39;https://example.org/thumb.jpg&#39;)"></span>'
            '</div>'
        )
        items = mirror.parse_posts(page)
        self.assertEqual(items[0]["assets"], [])
        self.assertTrue(items[0]["hidden_media"])

    def test_weather_source_keeps_local_forecasts_and_road_hazards(self):
        self.assertTrue(mirror.should_publish({
            "text": "پیش‌بینی ۵ روزه هوای مازندران؛ بارش در ارتفاعات چالوس"
        }, mode="regional_weather"))
        self.assertTrue(mirror.should_publish({
            "text": "هشدار یخبندان و مه گرفتگی محور کندوان"
        }, mode="regional_weather"))
        self.assertTrue(mirror.should_publish({
            "text": "پیش‌بینی ۳ روزه دریای کاسپین؛ ارتفاع موج ۰.۷ متر"
        }, mode="regional_weather"))

    def test_weather_source_excludes_foreign_and_nonweather_posts(self):
        self.assertFalse(mirror.should_publish({
            "text": "#منهای_مازندران\nگردباد قدرتمند در شیلی!"
        }, mode="regional_weather"))
        self.assertFalse(mirror.should_publish({
            "text": "خبر جشنواره فروش محصولات محلی مازندران"
        }, mode="regional_weather"))
        self.assertFalse(mirror.should_publish({
            "text": "☀️"
        }, mode="regional_weather"))
        self.assertFalse(mirror.should_publish({
            "text": "پیش بینی بارش برف در کانادا"
        }, mode="regional_weather"))
        # No filter ever affects JadehChalus or ChalousOnline mirrors.
        self.assertTrue(mirror.should_publish({
            "text": "یک پست نامرتبط"
        }, mode="all"))

    def test_text_only_keeps_full_message(self):
        with patch.object(mirror, "bot_call", side_effect=self.fake_bot_call):
            mirror.publish(self.post(has_media=False, text="فقط متن"))
        self.assertEqual([c[0] for c in self.calls], ["sendMessage"])
        self.assertEqual(self.calls[0][1]["text"], "فقط متن")


if __name__ == "__main__":
    unittest.main()
