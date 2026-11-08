"""Conservative editorial gate: exclude unmistakable propaganda-only posts.
Official attribution alone is never grounds for exclusion. Concrete civic
information and reporting/criticism of propaganda remain eligible.
"""
import html
import re
import unicodedata


def is_pure_propaganda(text):
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"[\u064b-\u065fـ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Do not confuse reporting about slogans with endorsement.
    if re.search(r"انتقاد|اعتراض|شعار داد|شعارهای|شعار هایی|گزارش.*شعار|ادعا|تکذیب", text):
        return False
    # Preserve actionable local facts, including official notices and projects.
    if re.search(r"قطع آب|قطعی آب|قطع برق|خاموشی|مسدود|انسداد|هشدار|سیلاب|زلزله|تعطیل|محدودیت تردد|ساعت|\d|[۰-۹]|بهره برداری|افتتاح|تامین آب|تأمین آب", text):
        return False
    slogans = (
        "مرگ بر آمریکا", "مرگ بر اسرائیل", "لبیک یا خامنه ای",
        "جانم فدای رهبر", "تا آخر ایستاده ایم", "وعده صادق محقق شد",
    )
    glorification = re.search(r"رهبری حکیمانه|تدابیر داهیانه|منویات مقام معظم|بیعت با.*رهب|تجدید میثاق با.*(رهب|ولایت)|حماسه حضور|مشت محکم بر دهان|دشمنان.*به زانو|دشمن.*خوار و ذلیل", text)
    mobilization = re.search(r"دعوت.*(راهپیمایی|تجمع|بیعت)|حضور پرشور.*(راهپیمایی|تجمع)|همه می آییم", text)
    return any(s in text for s in slogans) or bool(glorification or mobilization)
