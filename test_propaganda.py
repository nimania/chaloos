import unittest
from propaganda import is_pure_propaganda

class PropagandaPolicyTests(unittest.TestCase):
    def test_pure_slogans_and_glorification(self):
        for text in ["مرگ بر آمریکا؛ جانم فدای رهبر", "با تدابیر داهیانه دشمن به زانو درآمد", "دعوت به حضور در راهپیمایی و تجدید میثاق با رهبری"]:
            with self.subTest(text=text):
                self.assertTrue(is_pure_propaganda(text))

    def test_civic_facts_and_critical_reporting(self):
        for text in ["استانداری اعلام کرد: قطع آب از ساعت ۱۰ تا ۱۲", "افتتاح بیمارستان جدید با حضور استاندار", "انتقاد شهروندان از شعار مرگ بر آمریکا", "هشدار سیلاب در مازندران", "خبر بازداشت یک مدیر شهری"]:
            with self.subTest(text=text):
                self.assertFalse(is_pure_propaganda(text))
