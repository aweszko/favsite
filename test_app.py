# Test jednostkowy na DANYCH PRZYKŁADOWYCH (fikcyjne liczby, nie z API). Uruchom: python test_app.py
import unittest
from datetime import datetime, timezone
import app

SAMPLE_GAMES = [  # PRZYKŁADOWE
    {"on": 5, "tot": 12}, {"on": 3, "tot": 9}, {"on": None, "tot": None},
    {"on": 7, "tot": 15}, {"on": 2, "tot": 4}]

class T(unittest.TestCase):
    def test_stats(self):
        s = app.compute_stats(SAMPLE_GAMES)
        self.assertEqual((s["sum_on"], s["sum_tot"], s["games_with_data"]), (17, 40, 4))
        self.assertEqual(s["avg_on"], 4.25); self.assertEqual(s["avg_tot"], 10.0); self.assertEqual(s["accuracy"], 42.5)
    def test_no_data_is_none_not_zero(self):
        s = app.compute_stats([{"on": None, "tot": None}] * 5)
        self.assertEqual(s["games_with_data"], 0); self.assertIsNone(s["avg_on"]); self.assertIsNone(s["accuracy"])
    def test_favourite(self):
        self.assertEqual(app.pick_favourite(1.5, 6.0, 1.2, 2.0)[0], "home")
        self.assertEqual(app.pick_favourite(4.0, 1.9, 1.2, 2.0)[0], "away")
        self.assertIsNone(app.pick_favourite(1.9, 2.3, 1.2, 2.0))   # różnica < 0,50
        self.assertIsNone(app.pick_favourite(1.1, 9.0, 1.2, 2.0))   # poza zakresem
        self.assertIsNotNone(app.pick_favourite(1.5, 2.0, 1.2, 2.0))  # dokładnie 0,50
    def test_ranking_sort_and_too_few(self):
        rows = [{"team": "A", "stats": app.compute_stats(SAMPLE_GAMES)},
                {"team": "B", "stats": app.compute_stats([{"on": 9, "tot": 20}] * 5)},
                {"team": "C", "stats": app.compute_stats([{"on": 9, "tot": 9}] * 2 + [{"on": None, "tot": None}] * 3)}]
        ok, few = app.build_ranking(rows, 3)
        self.assertEqual([r["team"] for r in ok], ["B", "A"]); self.assertEqual([r["team"] for r in few], ["C"])
    def test_warsaw_time(self):
        self.assertEqual(app.to_warsaw(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)).hour, 13)
        self.assertEqual(app.to_warsaw(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)).hour, 14)

class T2(unittest.TestCase):
    def test_compare_diff(self):  # PRZYKŁADOWE
        a = app.compute_stats(SAMPLE_GAMES); b = app.compute_stats([{"on": 2, "tot": 8}] * 5)
        d = app.compare(a, b)
        self.assertEqual(d["avg_on"], 2.25); self.assertEqual(d["sum_tot"], 0)
    def test_compare_no_data(self):
        a = app.compute_stats(SAMPLE_GAMES)
        self.assertIsNone(app.compare(a, None)["avg_on"])
        self.assertIsNone(app.compare(a, app.compute_stats([{"on": None, "tot": None}]))["sum_on"])
    def test_parse_1x2(self):
        o = [{"marketName": "Mecz", "code": "1", "price": "1.5"}, {"marketName": "Mecz", "code": "0", "price": 4},
             {"marketName": "Mecz", "code": "2", "price": 6}, {"marketName": "Inny", "code": "1", "price": 9}]
        self.assertEqual(app.parse_1x2(o), (1.5, 4.0, 6.0))
    def test_week_window(self):
        from datetime import timedelta
        n = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        a, b = app._window("prematch", n); self.assertEqual(b - a, timedelta(days=7))
    def test_port_env(self):
        import os, importlib
        os.environ["PORT"] = "9123"; m = importlib.reload(app); self.assertEqual(m.PORT, 9123)
        del os.environ["PORT"]; importlib.reload(app)

if __name__ == "__main__":
    unittest.main()
