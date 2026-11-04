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
    def test_ranking_sort_and_too_few(self):  # PRZYKŁADOWE
        S = lambda on, n, nd=0: app.compute_stats([{"on": on, "tot": on + 5}] * n + [{"on": None, "tot": None}] * nd)
        rows = [{"team": "A", "stats": S(5, 5), "opp_stats": S(4, 5)},          # 5v5, +1
                {"team": "B", "stats": S(7, 5), "opp_stats": S(3, 5)},          # 5v5, +4
                {"team": "C", "stats": S(6, 4, 1), "opp_stats": S(4, 5)},       # 4v5, +2 -> ok
                {"team": "D", "stats": S(5, 4, 1), "opp_stats": S(4, 5)},       # 4v5, +1 -> za mało
                {"team": "E", "stats": S(9, 5), "opp_stats": S(1, 3, 2)},       # 3 mecze -> za mało
                {"team": "F", "stats": S(9, 5), "opp_stats": None}]
        ok, few = app.build_ranking(rows, 3)
        self.assertEqual([r["team"] for r in ok], ["B", "C", "A"]); self.assertEqual([r["team"] for r in few], ["D", "E", "F"])
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

class T3(unittest.TestCase):
    def test_background_single_flight(self):
        import time, threading
        calls = []
        ev = threading.Event()
        orig = app.ranking
        def fake(mode, mg, a, b, progress=None, **k):
            calls.append(mode); progress.update(done=0, total=2); ev.wait(2)
            return {"ranking": [], "too_few": [], "footer": {}, "errors": {}}
        app.ranking = fake
        try:
            t = time.time()
            r1 = app.get_ranking("live"); r2 = app.get_ranking("live")
            self.assertLess(time.time() - t, 0.5)
            self.assertEqual(r1["status"], "computing"); self.assertEqual(r2["progress"]["total"], 2)
            ev.set(); time.sleep(0.3)
            self.assertEqual(len(calls), 1)
            self.assertEqual(app.get_ranking("live")["status"], "ready")
            self.assertIn("counters", app.status())
        finally:
            app.ranking = orig; app.RESULTS.clear()
    def test_static_logo(self):
        for path in ("/logo.png", "/icon.png", "/favicon.png", "/apple-touch-icon.png"):
            data, ctype = app.static_file(path)
            self.assertEqual(ctype, "image/png"); self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(app.static_file("/favicon.ico")[1], "image/x-icon")
        self.assertIsNone(app.static_file("/app.py"))

    def test_html_branding(self):
        self.assertIn("<title>Triumfator", app.HTML); self.assertIn('href="/favicon.png"', app.HTML)

    def _vrow(self, mode="live", fav_on=6, opp_on=2, fav_gf=2):
        g = lambda on, tot, gf, ga, cor: {"on": on, "tot": tot, "gf": gf, "ga": ga, "cor": cor}
        fg = [g(fav_on, 15, fav_gf, 0, 7) for _ in range(5)]; og = [g(opp_on, 8, 0, 2, 3) for _ in range(5)]
        return {"mode": mode, "side": "D", "games": fg, "opp_games": og,
                "stats": app.compute_stats(fg), "opp_stats": app.compute_stats(og)}
    TF = {"pos": 2, "of": 18, "played": 10, "gf": 20, "gpm": 2.0}
    TO = {"pos": 15, "of": 18, "played": 10, "gf": 8, "gpm": 0.8}
    def test_vip_all_pass_live(self):
        c = app.vip_check(self._vrow(), 1.7, {"on": (4, 1), "cor": (3, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertTrue(all(x["ok"] for x in c), [x for x in c if not x["ok"]])
    def test_vip_odds_below_160(self):
        c = app.vip_check(self._vrow(), 1.55, {"on": (4, 1), "cor": (3, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertFalse(c[0]["ok"])
    def test_vip_live_stats_missing(self):
        c = app.vip_check(self._vrow(), 1.7, None, self.TF, self.TO)
        self.assertFalse(all(x["ok"] for x in c))
    def test_vip_live_rival_dominates(self):
        c = app.vip_check(self._vrow(), 1.7, {"on": (1, 4), "cor": (3, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertFalse(all(x["ok"] for x in c))
    def test_vip_no_table(self):
        c = app.vip_check(self._vrow("prematch"), 1.7, None, None, None)
        self.assertFalse(all(x["ok"] for x in c))
        self.assertIn("brak danych", [x["fav"] for x in c])
    def test_vip_prematch_pass_and_bottom_table(self):
        self.assertTrue(all(x["ok"] for x in app.vip_check(self._vrow("prematch"), 1.7, None, self.TF, self.TO)))
        bad = dict(self.TF, pos=16)
        self.assertFalse(all(x["ok"] for x in app.vip_check(self._vrow("prematch"), 1.7, None, bad, self.TO)))
    def test_vip_four_of_five_rejected(self):
        r = self._vrow(); r["games"][0]["on"] = None; r["games"][0]["tot"] = None; r["stats"] = app.compute_stats(r["games"])
        c = app.vip_check(r, 1.7, {"on": (4, 1), "cor": (3, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertFalse(c[1]["ok"])
    def test_vip_html(self):
        self.assertIn('data-v="vip"', app.HTML); self.assertIn("Najostrzejsze typy – żadna pewność", app.HTML)
        self.assertNotIn("100% pewniak", app.HTML)

if __name__ == "__main__":
    unittest.main()
