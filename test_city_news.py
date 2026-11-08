# -*- coding: utf-8 -*-
"""Offline tests for six-city Mazandaran editorial filtering and cursor safety."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import city_news as city

BABEL = {"handle": "babolnews", "name": "بابل نیوز", "city": "بابل"}
OFFICIAL = {"handle": "ostandarimazandaran_ir", "name": "استانداری", "city": None}


def item(text, hours_ago=2, ident=202, now=None):
    now = now or datetime.now(timezone.utc)
    return {
        "id": ident,
        "text": text,
        "published": (now - timedelta(hours=hours_ago)).isoformat(),
        "source_url": "https://t.me/babolnews/" + str(ident),
    }


class CityNewsTests(unittest.TestCase):
    def test_local_civic_notice(self):
        decision = city.classify(
            item("⚡️ جدول زمان‌بندی خاموشی برق شهرستان بابل فردا منتشر شد"), BABEL
        )
        self.assertEqual(decision, ("بابل", "خدمات شهری"))

    def test_regional_source_requires_explicit_city(self):
        self.assertEqual(
            city.classify(item("شهرداری ساری درباره جمع‌آوری پسماند اطلاعیه داد"), OFFICIAL),
            ("ساری", "خدمات شهری")
        )
        self.assertIsNone(
            city.classify(item("ابلاغیه عمومی درباره وضعیت اقتصاد کشور"), OFFICIAL)
        )

    def test_nonlocal_promotion_and_nonlocal_news_rejected(self):
        self.assertIsNone(city.classify(
            item("قرعه‌کشی بزرگ تبلیغات فروشگاه‌های بابل"), BABEL
        ))
        self.assertIsNone(city.classify(
            item("شهرداری تهران درباره قطعی برق اطلاعیه منتشر کرد"), BABEL
        ))
        self.assertIsNone(city.classify(
            item("دیدار وزیر خارجه آمریکا در واشنگتن"), BABEL
        ))

    def test_road_closures_not_bypassed_through_city_news(self):
        self.assertIsNone(city.classify(
            item("جاده چالوس مسدود شد، اطلاعیه شهرداری منتشر شد"), BABEL
        ))

    def test_date_must_be_present_and_fresh(self):
        now = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
        text = "آتش‌نشانی بابل آتش‌سوزی بازار را مهار کرد"
        self.assertIsNotNone(city.useful_and_recent(item(text, now=now), BABEL, now))
        self.assertIsNone(city.useful_and_recent(item(text, hours_ago=70, now=now), BABEL, now))
        old = item(text, now=now)
        old["published"] = ""
        self.assertIsNone(city.useful_and_recent(old, BABEL, now))

    def test_ramsar_feed_routes_tonekabon(self):
        source = {"handle": "ramsar11", "name": "رامسر و تنکابن", "city": "رامسر"}
        self.assertEqual(city.classify(
            item("قطعی برق در برخی محله‌های تنکابن اعلام شد"), source
        ), ("تنکابن", "خدمات شهری"))

    def test_first_run_seeds_baseline_without_publishing(self):
        page = [item("خبر تازه بابل", ident=300), item("خبر گذشته", ident=298)]
        with patch.object(city, "fetch_public_page", return_value=page) as request:
            rows, baseline = city.collect_since("babolnews", 0)
        self.assertEqual(rows, [])
        self.assertEqual(baseline, 300)
        self.assertEqual(request.call_count, 1)

    def test_pagination_reaches_cursor_without_skipping(self):
        newer = [item("x", ident=105), item("y", ident=104)]
        older = [item("z", ident=103), item("w", ident=102), item("old", ident=100)]
        with patch.object(city, "fetch_public_page", side_effect=[newer, older]):
            rows, baseline = city.collect_since("babolnews", 100)
        self.assertEqual([x["id"] for x in rows], [102, 103, 104, 105])
        self.assertEqual(baseline, 100)

    def test_attributed_text_contains_exact_source_url(self):
        post = item("آتش‌نشانی بابل آتش‌سوزی مرکز تجاری را مهار کرد")
        msg = city.format_post(post, BABEL, "بابل", "خدمات شهری")
        self.assertIn("بابل نیوز", msg)
        self.assertIn(post["source_url"], msg)
        self.assertIn("🕒 تاریخ گزارش", msg)

    def test_cross_channel_text_signature(self):
        a = item("قطعی برق بابل اعلام شد\n@babolnews")
        b = item("قطعی برق بابل اعلام شد\n@anotherchannel")
        self.assertEqual(city.fingerprint(a), city.fingerprint(b))


if __name__ == "__main__":
    unittest.main()
