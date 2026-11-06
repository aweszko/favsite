# Test jednostkowy na DANYCH PRZYKŁADOWYCH (fikcyjne liczby, nie z API). Uruchom: python test_app.py
import unittest
from datetime import datetime, timezone
import app
import next_goal

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

class TConflict(unittest.TestCase):  # PRZYKŁADOWE
    @staticmethod
    def S(on, tot, n=5): return app.compute_stats([{"on": on, "tot": tot}] * n)
    def test_mahallah_case_goes_to_conflict(self):
        r = {"team": "M", "stats": self.S(1.4, 6.2), "opp_stats": self.S(3.8, 9.6)}
        ok, few, cf = app.build_ranking([r], 3, with_conflict=True)
        self.assertEqual(ok, []); self.assertEqual(few, []); self.assertEqual([x["team"] for x in cf], ["M"])
    def test_lead_requires_both(self):
        self.assertFalse(app.stats_lead({"stats": self.S(5, 8), "opp_stats": self.S(3, 10)}))   # celne +, ogółem -
        self.assertFalse(app.stats_lead({"stats": self.S(3, 12), "opp_stats": self.S(4, 10)}))  # celne -, ogółem +
        self.assertFalse(app.stats_lead({"stats": self.S(4, 10), "opp_stats": self.S(4, 9)}))   # celne równe
        self.assertTrue(app.stats_lead({"stats": self.S(5, 12), "opp_stats": self.S(4, 10)}))
    def test_few_not_in_conflict(self):
        r = {"team": "X", "stats": self.S(1, 3, 3), "opp_stats": self.S(5, 9)}
        ok, few, cf = app.build_ranking([r], 3, with_conflict=True)
        self.assertEqual(([x["team"] for x in few], cf), (["X"], []))
    def test_4v5_exception_still_needs_lead(self):
        r = {"team": "Y", "stats": app.compute_stats([{"on": 6, "tot": 8}] * 4 + [{"on": None, "tot": None}]),
             "opp_stats": self.S(4, 10)}
        ok, few, cf = app.build_ranking([r], 3, with_conflict=True)
        self.assertEqual((ok, [x["team"] for x in cf]), ([], ["Y"]))

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
        c = app.vip_check(self._vrow(), 1.7, {"on": (4, 1), "cor": (4, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertTrue(all(x["ok"] for x in c), [x for x in c if not x["ok"]])
    LD = {"on": (4, 1), "cor": (4, 3), "da": (40, 20)}
    def _ok(self, ld, odds=1.7):
        return all(x["ok"] for x in app.vip_check(self._vrow(), odds, ld, self.TF, self.TO))
    def test_vip_low_odds_ok(self):
        self.assertTrue(self._ok(self.LD, 1.05)); self.assertTrue(self._ok(self.LD, None))
        c = app.vip_check(self._vrow(), None, self.LD, self.TF, self.TO)
        self.assertEqual(c[0]["fav"], "brak danych")
        self.assertEqual(app.VIP_RULES["odds_range"], (1.0, 2.0))
    def test_vip_live_on_target(self):
        self.assertTrue(self._ok(dict(self.LD, on=(3, 1))))
        self.assertTrue(self._ok(dict(self.LD, on=(2, 0))))
        self.assertFalse(self._ok(dict(self.LD, on=(2, 1))))   # tylko +1
        self.assertFalse(self._ok(dict(self.LD, on=(5, 2))))   # rywal > 1
    def test_vip_live_corners_strict(self):
        self.assertFalse(self._ok(dict(self.LD, cor=(3, 3))))
        self.assertTrue(self._ok(dict(self.LD, cor=(1, 0))))
    def test_vip_live_dangerous(self):
        self.assertTrue(self._ok(dict(self.LD, da=(33, 23))))   # 1,43x i +10
        self.assertFalse(self._ok(dict(self.LD, da=(29, 20))))  # +9
        self.assertFalse(self._ok(dict(self.LD, da=(60, 50))))  # 1,2x
    def test_vip_live_partial_missing(self):
        self.assertFalse(self._ok({"on": (4, 1), "cor": (4, 3), "da": None}))
        c = app.vip_check(self._vrow(), 1.7, {"on": None, "cor": None, "da": None}, self.TF, self.TO)
        self.assertIn("rywal + 2", [x["need"] for x in c if x["name"] == "LIVE: celne w meczu"][0])
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
        c = app.vip_check(r, 1.7, {"on": (4, 1), "cor": (4, 3), "da": (40, 20)}, self.TF, self.TO)
        self.assertFalse(c[1]["ok"])
    def test_vip_html(self):
        self.assertIn('data-v="vip"', app.HTML); self.assertIn("Najostrzejsze typy – żadna pewność", app.HTML)
        self.assertNotIn("100% pewniak", app.HTML)

def EV(typ, t, team, res=None, **kw):
    e = {"type": typ, "time": t, "team": team, "disabled": 0}
    if res: e["result"] = {"home": res[0], "away": res[1]}
    e.update(kw); return e

TL_LIVE = {"match": {"result": {"home": 1, "away": 0}, "status": {"_id": 7}}, "events": [   # PRZYKŁADOWE
    EV("goal", 12, "home", (1, 0)), EV("shotontarget", 30, "away"), EV("corner", 52, "home"),
    EV("shotontarget", 55, "home"), EV("corner", 58, "home"), EV("shotontarget", 59, "home"),
    EV("shotontarget", 45, "away"), EV("shotontarget", 60, "home", disabled=1), EV("shotofftarget", 60, "away")]}

class TPro(unittest.TestCase):
    def test_pressure_window(self):
        p = app.pressure(TL_LIVE, "home")
        self.assertEqual(p["to"], 60); self.assertEqual(p["from"], 50)
        self.assertEqual((p["fav_on"], p["fav_cor"], p["riv_on"], p["riv_cor"]), (2, 2, 0, 0))
        q = app.pressure(TL_LIVE, "away"); self.assertEqual((q["fav_on"], q["riv_on"]), (0, 2))
    def test_pressure_alt_rule(self):
        ok, c = app.pro_trigger({"on": (5, 1), "cor": (6, 2), "da": (40, 20)}, {"fav_on": 1, "fav_cor": 2, "riv_on": 0}, "home", 60, "1:0")
        self.assertTrue(ok)
        ok, _ = app.pro_trigger({"on": (5, 1), "cor": (6, 2), "da": (40, 20)}, {"fav_on": 1, "fav_cor": 1, "riv_on": 0}, "home", 60, "1:0")
        self.assertFalse(ok)
    def test_trigger_dominance(self):
        pr = {"fav_on": 2, "fav_cor": 0, "riv_on": 0}
        det = {"on": (1, 5), "cor": (2, 6), "da": (25, 40)}   # faworyt = gość
        self.assertTrue(app.pro_trigger(det, pr, "away", 50, "0:0")[0])
        self.assertFalse(app.pro_trigger(det, pr, "away", 85, "0:0")[0])            # po 80'
        self.assertFalse(app.pro_trigger(det, pr, "away", 9, "0:0")[0])             # przed 10'
        self.assertFalse(app.pro_trigger({"on": (2, 5), "cor": (2, 6), "da": (25, 40)}, pr, "away", 50, "0:0")[0])  # rywal 2 celne
        self.assertFalse(app.pro_trigger({"on": (1, 5), "cor": (2, 6), "da": (31, 40)}, pr, "away", 50, "0:0")[0])  # DA < +10
        self.assertFalse(app.pro_trigger(det, {"fav_on": 2, "riv_on": 1}, "away", 50, "0:0")[0])                     # rywal celny w oknie
        self.assertFalse(app.pro_trigger(None, pr, "away", 50, "0:0")[0])                                             # brak danych
    def test_settle(self):
        e = {"side_key": "home", "score_h": 1, "score_a": 0}
        self.assertEqual(app.settle(e, TL_LIVE), {"final": None})                    # trwa, bez gola
        tl = {"match": {"result": {"home": 2, "away": 1}, "status": {"_id": 100}},
              "events": TL_LIVE["events"] + [EV("goal", 70, "home", (2, 0)), EV("goal", 80, "away", (2, 1))]}
        self.assertEqual(app.settle(e, tl), {"next": "hit", "over": "hit", "final": "2:1"})
        tl2 = {"match": {"result": {"home": 1, "away": 1}, "status": {"_id": 100}}, "events": [EV("goal", 70, "away", (1, 1))]}
        self.assertEqual(app.settle(e, tl2)["next"], "miss"); self.assertEqual(app.settle(e, tl2)["over"], "hit")
        tl3 = {"match": {"result": {"home": 1, "away": 0}, "status": {"_id": 100}}, "events": []}
        self.assertEqual(app.settle(e, tl3), {"next": "miss", "over": "miss", "final": "1:0"})   # brak następnego gola
    def test_streaks_and_record(self):
        s = app.streaks(["hit", "hit", "hit", "miss", "hit", "hit"])
        self.assertEqual((s["current"], s["current_kind"], s["longest_hit"]), (2, "hit", 3))
        r = app.record_stats([{"next": "hit", "over": "hit"}, {"next": "miss", "over": None}, {"next": None, "over": None}])
        self.assertEqual((r["next"]["types"], r["next"]["hits"], r["next"]["misses"], r["next"]["pending"], r["next"]["hit_rate"]), (3, 1, 1, 1, 50.0))
    def test_pro_tab_html(self):
        self.assertIn('data-v="pro"', app.HTML); self.assertIn("nie gwarancja", app.HTML)
        self.assertIn("Rejestr zeruje się po restarcie serwera", app.HTML); self.assertIn("Lista obserwowanych", app.HTML)

class TGist(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self.env = {k: os.environ.get(k) for k in ("GIST_TOKEN", "GIST_ID")}
        self.http = app._gist_http; self.path = app.PRO_LOG_PATH
        self.tmp = tempfile.mkdtemp(); app.PRO_LOG_PATH = os.path.join(self.tmp, "l.json")
        self.calls = []
        app.GIST_STATE.update({"last": 0.0, "dirty": False, "timer": None, "ok": None, "err": None, "disabled": False})
    def tearDown(self):
        import os
        for k, v in self.env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        app._gist_http = self.http; app.PRO_LOG_PATH = self.path
        t = app.GIST_STATE.get("timer")
        if t: t.cancel()
        app.GIST_STATE["timer"] = None
    def on(self):
        import os; os.environ["GIST_TOKEN"] = "x-test"; os.environ["GIST_ID"] = "gid"
    def off(self):
        import os; os.environ.pop("GIST_TOKEN", None); os.environ.pop("GIST_ID", None)
    def fake(self, resp=None, exc=None):
        def f(method, gid, token, body=None, timeout=15):
            self.calls.append((method, gid, body))
            if exc: raise exc
            return 200, resp or {}
        app._gist_http = f
    def test_load_from_gist(self):
        self.on(); self.fake({"files": {"pro_log.json": {"content": '[{"eventId": 1}]'}}})
        self.assertEqual(app.pro_load(), [{"eventId": 1}]); self.assertTrue(app.persist_active())
        self.assertEqual(self.calls[0][:2], ("GET", "gid"))
    def test_load_fallback_file(self):
        import json
        json.dump([{"eventId": 2}], open(app.PRO_LOG_PATH, "w"))
        self.on(); self.fake(exc=OSError("net"))
        self.assertEqual(app.pro_load(), [{"eventId": 2}]); self.assertFalse(app.GIST_STATE["ok"])
        self.assertNotIn("x-test", json.dumps(app.status()))
    def test_no_env_file_only(self):
        self.off(); self.fake()
        self.assertFalse(app.persist_active()); self.assertEqual(app.pro_load(), [])
        app.pro_save([{"eventId": 3}]); self.assertEqual(self.calls, []); self.assertIsNone(app.GIST_STATE["timer"])
    def test_push_and_coalesce(self):
        import json
        self.on(); self.fake()
        app.GIST_STATE["last"] = __import__("time").time()   # świeżo zapisane -> odroczenie
        app.pro_save([]); t1 = app.GIST_STATE["timer"]; app.pro_save([]); 
        self.assertIs(app.GIST_STATE["timer"], t1); self.assertEqual(self.calls, [])
        t1.cancel(); app.gist_push_now()
        m, g, body = self.calls[0]
        self.assertEqual((m, g), ("PATCH", "gid")); json.loads(body["files"]["pro_log.json"]["content"])
        self.assertFalse(app.GIST_STATE["dirty"]); self.assertTrue(app.GIST_STATE["ok"])
    def test_push_failure_safe(self):
        self.on(); self.fake(exc=RuntimeError("boom"))
        self.assertFalse(app.gist_push_now()); self.assertNotIn("gist", app.ERRORS)   # po cichu
        self.assertNotIn("x-test", str(app.ERRORS)); self.assertFalse(app.GIST_STATE["ok"])
    def test_401_disables_silently(self):
        import urllib.error, json
        self.on(); self.fake(exc=urllib.error.HTTPError("u", 401, "Unauthorized", {}, None))
        self.assertIsNone(app.gist_load()); self.assertNotIn("gist", app.ERRORS)
        self.assertFalse(app.persist_active()); n = len(self.calls)
        app.pro_save([{"eventId": 9}]); self.assertIsNone(app.GIST_STATE["timer"])   # bez kolejnych prób
        self.assertFalse(app.gist_push_now()); self.assertEqual(len(self.calls), n)
        self.assertEqual(json.load(open(app.PRO_LOG_PATH)), [{"eventId": 9}])       # lokalny cache działa
        self.assertNotIn("x-test", json.dumps(app.status()))
    def test_pro_note_not_claiming_gist_when_broken(self):
        self.on(); self.fake(exc=RuntimeError("boom")); app.gist_push_now()
        self.assertFalse(app.persist_working())
        self.fake(); app.GIST_STATE["ok"] = True; self.assertTrue(app.persist_working())

class TRefresh(unittest.TestCase):
    def setUp(self):
        self.orig = (app.ranking, app.holes, app.RESULTS.copy(), app.JOBS.copy(), dict(app.WORST_STATE))
        app.RESULTS.clear(); app.JOBS.clear(); app.WORST_STATE.update(res=None, t=0, job=None)
    def tearDown(self):
        app.ranking, app.holes = self.orig[0], self.orig[1]
        app.RESULTS.clear(); app.RESULTS.update(self.orig[2]); app.JOBS.clear(); app.JOBS.update(self.orig[3])
        app.WORST_STATE.update(self.orig[4]); app.ERRORS.pop("ranking_job", None)
    def _wait(self, cond):
        import time
        for _ in range(200):
            if cond(): return True
            time.sleep(0.02)
        return False
    def test_tick_recomputes_stale_without_requests(self):
        import time
        calls = []
        app.ranking = lambda *a, **k: (calls.append(a), {"ranking": [], "too_few": [], "footer": {}})[1]
        app.holes = lambda prog: {"generated_warsaw": "x", "list": []}
        app.RESULTS[app._rkey("all", 3, 1.2, 2.0)] = (time.time() - 10000, {"ranking": [], "too_few": [], "footer": {}})
        app.refresh_tick()
        self.assertTrue(self._wait(lambda: time.time() - app.RESULTS[app._rkey("all", 3, 1.2, 2.0)][0] < 60))
        self.assertTrue(self._wait(lambda: app.WORST_STATE["res"] is not None))
        self.assertTrue(app.REFRESH_STATE["last"] > 0)
    def test_hung_ranking_job_restarted(self):
        import time
        key = app._rkey("all", 3, 1.2, 2.0)
        app.JOBS[key] = {"progress": {"done": 0, "total": 0}, "started": time.time() - app.RANK_JOB_MAX - 5}
        app.ranking = lambda *a, **k: {"ranking": [], "too_few": [], "footer": {}}
        app.get_ranking("all")
        self.assertTrue(self._wait(lambda: key in app.RESULTS))


class TestHoles(unittest.TestCase):
    def g(self, ga, on=4, tot=12, gf=1):
        return {"gf": gf, "ga": ga, "on_a": on, "tot_a": tot, "score": "%d:%d" % (gf, ga)}
    def test_cap_and_excess(self):
        a = app.adjust_games([self.g(8)], 10, [{"avg": 1.0, "league": 10}])[0]
        self.assertTrue(a["capped"]); self.assertEqual(a["excess"], 3.0); self.assertEqual(a["weight"], 1.0)
    def test_excess_floor_zero(self):
        a = app.adjust_games([self.g(1)], 10, [{"avg": 2.5, "league": 10}])[0]
        self.assertEqual(a["excess"], 0.0)
    def test_other_tier_downweighted(self):
        a = app.adjust_games([self.g(3)], 10, [{"avg": 2.0, "league": 99}])[0]
        self.assertEqual(a["weight"], 0.5); self.assertIn("rywal z innej ligi – waga ×0,5", a["notes"])
    def test_missing_opp_fallback(self):
        a = app.adjust_games([self.g(6)], 10, [None])[0]
        self.assertEqual(a["excess"], round(4 - app.LEAGUE_AVG, 2)); self.assertEqual(a["weight"], 1.0)
        self.assertIn("brak danych o sile rywala", a["notes"])
    def test_cup_blowout_does_not_dominate(self):
        # Tarnobrzeg: 0:8 z Lechem (inna liga, Lech śr. 2,4) + 4 x 1 stracony z ligowcami śr. 1,2
        cup = app.adjust_games([self.g(8)] + [self.g(1)] * 4, 1, [{"avg": 2.4, "league": 2}] + [{"avg": 1.2, "league": 1}] * 4)
        steady = app.adjust_games([self.g(2)] * 5, 1, [{"avg": 1.0, "league": 1}] * 5)
        self.assertLess(app.temperature(cup)["temp"], app.temperature(steady)["temp"])
    def test_temperature_full_and_no_shots(self):
        adj = app.adjust_games([self.g(3, 7, 18)] * 5, 1, [{"avg": 1.0, "league": 1}] * 5)
        t = app.temperature(adj); self.assertEqual(t["temp"], 100); self.assertEqual(t["label"], "bardzo dziurawa"); self.assertTrue(t["shots"])
        adj = app.adjust_games([self.g(2, None, None)] * 5, 1, [{"avg": 1.0, "league": 1}] * 5)
        t = app.temperature(adj); self.assertFalse(t["shots"]); self.assertEqual(t["temp"], round(100 * (0.4 * 0.5 + 0.2) / 0.6))
    def test_requires_five(self):
        self.assertIsNone(app.temperature(app.adjust_games([self.g(3)] * 4, 1, [None] * 4)))
    def test_rank_sort_and_threshold(self):
        mk = lambda n, t, ga: {"team": n, "t": {"temp": t}, "ga_raw": ga}
        r = app.holes_rank([mk("A", 50, 5), mk("B", 90, 9), mk("C", 39, 20), mk("D", 50, 8)])
        self.assertEqual([x["team"] for x in r], ["B", "D", "A"]); self.assertEqual(r[0]["rank"], 1)

class TestChance(unittest.TestCase):
    T = {"temp": 85}
    def test_weak_attack_low(self):
        c = app.goal_chance(self.T, {"gf_avg": 0.6, "on_avg": 2.0, "tot_avg": 8.0}, {"on": [1, 5], "cor": [1, 6], "da": [10, 40]})
        self.assertLessEqual(c["score"], 30); self.assertTrue(c["notes"])
    def test_strong_attack_high(self):
        c = app.goal_chance(self.T, {"gf_avg": 2.2, "on_avg": 6.0, "tot_avg": 15.0}, {"on": [1, 4], "cor": [2, 4], "da": [20, 40]})
        self.assertGreaterEqual(c["score"], 75)
    def test_missing_parts_renormalized(self):
        c = app.goal_chance({"temp": 100}, {"gf_avg": 2.0, "on_avg": None, "tot_avg": None}, None)
        self.assertEqual(c["score"], 100); self.assertEqual(sum(p["weight"] for p in c["parts"]), 100)
    def test_no_opp_capped(self):
        self.assertLessEqual(app.goal_chance({"temp": 100}, None, None)["score"], 50)
    def test_live_pressure(self):
        self.assertEqual(app.live_pressure({"on": [1, 3], "cor": None, "da": [0, 0]})["share"], 0.62)
        self.assertIsNone(app.live_pressure({"on": None}))
    def test_live_dead_penalty(self):
        A = {"gf_avg": 2.0, "on_avg": 5.0, "tot_avg": 14.0}; L = {"on": [6, 0], "cor": [5, 0], "da": [40, 12]}
        self.assertLessEqual(app.goal_chance(self.T, A, L, "56")["score"], 30)
        self.assertGreater(app.goal_chance(self.T, A, L, "20")["score"], 30)
        self.assertFalse(app.live_dead({"on": [2, 3], "cor": [1, 0], "da": [10, 5]}, 60))
    def test_rank_by_chance_then_temp(self):
        mk = lambda n, t, c: {"team": n, "t": {"temp": t}, "ga_raw": 5, "chance": {"score": c}}
        r = app.holes_rank([mk("SanMartin", 95, 20), mk("B", 60, 70), mk("C", 80, 70), mk("D", 30, 99)])
        self.assertEqual([x["team"] for x in r], ["C", "B", "SanMartin"])


class TestWorst(unittest.TestCase):
    def g(self, gf, ga, on=3, tot=10, on_a=4, tot_a=12):
        return {"gf": gf, "ga": ga, "score": "%d:%d" % (gf, ga), "on": on, "tot": tot, "on_a": on_a, "tot_a": tot_a}
    def row(self, name, games, mode="prematch"):
        return {"team": name, "mode": mode, "ws": app.weak_stats(games)}
    def test_weak_stats(self):
        ws = app.weak_stats([self.g(0, 4), self.g(0, 5), self.g(1, 0), self.g(0, 2), self.g(2, 2)])
        self.assertEqual((ws["gf"], ws["ga"], ws["clean"], ws["blank"]), (3, 13, 1, 3))
        self.assertEqual(ws["results"], "0:4 0:5 1:0 0:2 2:2")
        self.assertEqual(ws["on_a_avg"], 4.0)
    def test_missing_shots_is_none(self):
        gs = [self.g(1, 1) for _ in range(5)]; gs[2]["on_a"] = None
        self.assertIsNone(app.weak_stats(gs)["on_a_avg"])
    def test_sorting_and_too_few(self):
        rows = [self.row("A", [self.g(0, 3)] * 5), self.row("B", [self.g(2, 1)] * 5),
                self.row("C", [self.g(1, 0)] * 5), self.row("D", [self.g(0, 9)] * 4)]
        w = app.build_worst(rows)
        self.assertEqual([r["team"] for r in w["conceding"]], ["A", "B", "C"])
        self.assertEqual([r["team"] for r in w["scoring"]], ["A", "C", "B"])
        self.assertEqual(w["too_few"], 1)
        self.assertEqual([r["team"] for r in w["double"]], ["A"])
    def test_live_filter(self):
        rows = [self.row("A", [self.g(0, 3)] * 5, "live"), self.row("B", [self.g(0, 5)] * 5)]
        w = app.build_worst(rows, "live")
        self.assertEqual([r["team"] for r in w["conceding"]], ["A"])
        self.assertEqual(len(app.build_worst(rows, "all")["conceding"]), 2)

class TestRecency(unittest.TestCase):
    NOW = 1_800_000_000
    def gm(self, days, gf=1, ga=2, mid=None):
        return {"mid": mid or days, "uts": self.NOW - days * 86400, "date": "x", "opp": "O", "opp_uid": 9, "ha": "D", "comp": None,
                "league": 1, "score": "%d:%d" % (gf, ga), "gf": gf, "ga": ga, "on_a": None, "tot_a": None}
    def test_is_recent(self):
        self.assertTrue(app.is_recent(self.NOW - 89 * 86400, self.NOW))
        self.assertTrue(app.is_recent(self.NOW - 90 * 86400, self.NOW))
        self.assertFalse(app.is_recent(self.NOW - 91 * 86400, self.NOW))
        self.assertFalse(app.is_recent(None, self.NOW))
    def test_all_recent_requires_every_game(self):
        self.assertTrue(app.all_recent([self.gm(d) for d in (1, 5, 10, 20, 80)], self.NOW))
        self.assertFalse(app.all_recent([self.gm(d) for d in (1, 5, 10, 20, 730)], self.NOW))
        self.assertFalse(app.all_recent([], self.NOW))
    def _raw(self, days_list):
        ms = [{"_id": i, "time": {"uts": self.NOW - d * 86400}, "result": {"home": 2, "away": 0},
               "teams": {"home": {"uid": 7}, "away": {"uid": 8}}, "_tid": 1} for i, d in enumerate(days_list)]
        return {"matches": ms}
    def test_opp_attack_ignores_old(self):
        orig = app.lastx_raw
        try:
            app.lastx_raw = lambda uid: self._raw([1, 2, 400, 500, 600])
            self.assertIsNone(app.opp_attack(7, -1, self.NOW))       # tylko 2 aktualne < 3
            app.lastx_raw = lambda uid: self._raw([1, 2, 3, 500, 600])
            r = app.opp_attack(7, -1, self.NOW)
            self.assertEqual(r["n"], 3); self.assertEqual(r["avg"], 2.0)
        finally:
            app.lastx_raw = orig
    def test_holes_event_marks_stale(self):
        o = (app.match_info, app.live_details, app.team_games, app.opp_attack)
        try:
            app.match_info = lambda br: {"home": {"uid": 1, "name": "H"}, "away": {"uid": 2, "name": "A"}, "tournament": "T", "category": "C"}
            app.live_details = lambda br: None
            now = app.time.time()
            fresh = [dict(self.gm(d), uts=now - d * 86400) for d in (1, 2, 3, 4, 5)]
            app.team_games = lambda uid, br: ({"games": fresh, "league": 1, "stale": False} if uid == 1
                                              else {"games": fresh[:4], "league": 1, "stale": True})   # 5. mecz sprzed 2 lat odrzucony
            app.opp_attack = lambda uid, mid, now_ts=None: None
            _, rows = app.holes_event({"br": 5, "eventId": 1}, {})
            h, a = rows
            self.assertFalse(h["stale"]); self.assertIsNotNone(h["t"])
            self.assertTrue(a["stale"]); self.assertIsNone(a["t"])
        finally:
            app.match_info, app.live_details, app.team_games, app.opp_attack = o

class TestOppAndConcede(unittest.TestCase):
    def test_opp_attack_summary_fresh_only(self):
        now = app.time.time()
        g = lambda d, gf, on=None, tot=None: {"uts": now - d * 86400, "gf": gf, "ga": 0, "score": "%d:0" % gf, "on_f": on, "tot_f": tot}
        s = app.opp_attack_summary([g(1, 2, 5, 10), g(2, 0, 3, 8), g(400, 9, 9, 9)], now)
        self.assertEqual((s["n"], s["gf"], s["gf_avg"], s["on_avg"], s["tot_avg"]), (2, 2, 1.0, 4.0, 9.0))
        self.assertIsNone(app.opp_attack_summary([g(200, 1)], now))
        self.assertIsNone(app.opp_attack_summary([g(1, 1), g(2, 1, 3, 3)], now)["on_avg"])
    def test_conceded_minutes(self):
        tl = {"events": [{"type": "goal", "team": "away", "time": 12}, {"type": "goal", "team": "home", "time": 30},
                         {"type": "goal", "team": "away", "time": 88}, {"type": "corner", "team": "away", "time": 5}]}
        orig = app.tl_events
        try:
            app.tl_events = lambda t: t["events"]
            self.assertEqual(app.conceded_minutes(tl, "D"), [12, 88])
            self.assertEqual(app.conceded_minutes(tl, "W"), [30])
        finally:
            app.tl_events = orig
        self.assertIsNone(app.conceded_minutes(None, "D"))
    def test_concede_profile(self):
        p = app.concede_profile([[12, 88], [30], None, [], [46]])
        self.assertEqual((p["total"], p["h1"], p["h2"], p["first30"], p["missing"], p["games"]), (4, 2, 2, 2, 1, 4))
        self.assertIsNone(app.concede_profile([None, None]))

class TestSharedRecency(unittest.TestCase):
    def test_recent_last5_drops_old(self):
        now = app.time.time()
        m = lambda i, d: {"_id": i, "time": {"uts": now - d * 86400}, "result": {"home": 1, "away": 0}}
        ms, stale = app.recent_last5([m(1, 1), m(2, 10), m(3, 800), m(4, 900), m(5, 20), m(6, 30), m(7, 1000)], 99)
        self.assertEqual([x["_id"] for x in ms], [1, 2, 5, 6]); self.assertTrue(stale)
        ms, stale = app.recent_last5([m(i, i) for i in range(1, 7)], 1)
        self.assertEqual([x["_id"] for x in ms], [2, 3, 4, 5, 6]); self.assertFalse(stale)
    def test_qualifies_rejects_stale(self):
        r = {"stats": {"games_with_data": 5}, "opp_stats": {"games_with_data": 5}}
        self.assertTrue(app.qualifies(r)); r["stale"] = True; self.assertFalse(app.qualifies(r))
    def test_worst_overlay_fresh_minutes(self):
        res = {"generated_warsaw": "x", "list": [{"eventId": 1, "minutes": "56", "score": "2:0", "rank": 1},
                                                 {"eventId": 2, "minutes": "70", "score": "0:0", "rank": 2},
                                                 {"eventId": 3, "minutes": "10", "score": "0:0", "rank": 3}]}
        out = app.worst_overlay(res, [{"eventId": 1, "minutes": "67", "score": "3:0"}, {"eventId": 3, "minutes": "21", "score": "1:0"}])
        self.assertEqual([(r["eventId"], r["minutes"], r["score"], r["rank"]) for r in out["list"]], [(1, "67", "3:0", 1), (3, "21", "1:0", 2)])
        self.assertIn("live_warsaw", out); self.assertEqual(res["list"][0]["minutes"], "56")
        self.assertEqual(len(app.worst_overlay(res, [])["list"]), 3)   # brak listy LIVE -> bez zmian


class TestSections(unittest.TestCase):
    def r(self, team, ev, ch, minutes="60", oc=None, temp=70):
        return {"team": team, "eventId": ev, "chance": {"score": ch}, "minutes": minutes, "opp_chance": oc, "t": {"temp": temp}, "ga_raw": 10}

    def test_balanced_both_listed_go_bottom(self):
        lst = [self.r("City", 1, 90, "8", 85), self.r("Leipzig", 1, 85, "8", 90), self.r("A", 2, 70, "60", 30), self.r("B", 3, 50, "40", 20)]
        out = app.holes_sections(lst)
        self.assertEqual([x["team"] for x in out], ["A", "B", "City", "Leipzig"])
        self.assertTrue(out[2]["balanced"] and out[3]["balanced"]); self.assertEqual(out[2]["section"], "balanced")
        self.assertEqual([x["rank"] for x in out], [1, 2, 3, 4])

    def test_balanced_close_chance(self):
        out = app.holes_sections([self.r("A", 1, 60, "50", 55), self.r("B", 2, 40, "50", 10)])
        self.assertEqual(out[0]["team"], "B"); self.assertTrue(out[1]["balanced"]); self.assertIn("różnica", out[1]["balanced_why"])

    def test_early_below_main_above_balanced(self):
        lst = [self.r("E", 1, 95, "12"), self.r("M", 2, 50, "30"), self.r("X", 3, 99, "70", 95)]
        out = app.holes_sections(lst)
        self.assertEqual([x["section"] for x in out], ["main", "early", "balanced"])
        self.assertTrue(out[1]["early"]); self.assertFalse(out[0]["early"])
        c = app.section_counts(out); self.assertEqual(c["teams"], {"main": 1, "early": 1, "balanced": 1})

    def test_minute_int(self):
        self.assertEqual(app.minute_int("45+2"), 45); self.assertEqual(app.minute_int("24'"), 24); self.assertIsNone(app.minute_int(None))
        self.assertEqual(app.minute_int("HT"), 45)

    def test_overlay_reclassifies_early(self):
        res = {"list": [self.r("E", 1, 95, "20"), self.r("M", 2, 50, "60")]}
        out = app.worst_overlay(res, [{"eventId": 1, "minutes": "26", "score": "0:0"}, {"eventId": 2, "minutes": "61", "score": "1:0"}])
        self.assertEqual([x["team"] for x in out["list"]], ["E", "M"]); self.assertEqual(out["sections"]["teams"]["main"], 2)


class TestNextGoal(unittest.TestCase):
    """Snapshot ~2026-11-06 14:18 Europe/Warsaw – sita następnego gola."""

    def base(self, **kw):
        r = {
            "team": "X", "opponent": "Y", "side": "D", "minutes": "50", "score": "0:0",
            "balanced": False, "early": False,
            "live": {"on": [0, 3], "cor": [1, 3], "da": [20, 50]},
            "t": {"temp": 64, "shots": True},
            "opp_att": {"gf_avg": 2.2, "n": 5},
            "eventId": 1, "ga_raw": 10,
        }
        r.update(kw)
        return r

    def test_parse_minute(self):
        self.assertEqual(next_goal.parse_minute("HT"), 45)
        self.assertEqual(next_goal.parse_minute("przerwa"), 45)
        self.assertEqual(next_goal.parse_minute("45+2"), 47)
        self.assertEqual(next_goal.parse_minute("67'"), 67)
        self.assertEqual(next_goal.parse_minute("59"), 59)

    def test_skopje_passes(self):
        # side=W, score 1:0 home:away → leaky 0, rival 1
        r = self.base(team="Skopje", opponent="Tikvesh", side="W", minutes="59", score="1:0",
                      live={"on": [0, 3], "cor": [1, 3], "da": [21, 54]},
                      t={"temp": 64, "shots": True}, opp_att={"gf_avg": 2.2})
        out = next_goal.build_next_goal([r])
        self.assertEqual(len(out["picks"]), 1)
        p = out["picks"][0]
        self.assertEqual(p["team"], "Skopje")
        self.assertEqual(p["leaky_gf"], 0)
        self.assertEqual(p["rival_gf"], 1)
        self.assertEqual(p["tip"], "Tikvesh — następny gol")
        self.assertGreaterEqual(p["next_goal_score"], 50)

    def test_rivne_minute_out(self):
        r = self.base(team="Rivne", opponent="Polissya", minutes="88", score="1:0", side="W",
                      live={"on": [1, 4], "cor": [1, 8], "da": [30, 55]})
        self.assertEqual(next_goal.build_next_goal([r])["picks"], [])
        self.assertEqual(next_goal.build_next_goal([r])["funnel"][1]["after"], 0)

    def test_tanjong_minute_and_gd(self):
        r = self.base(team="Tanjong Pagar Reserve", opponent="Geylang Reserve", side="D",
                      minutes="89", score="0:7",
                      live={"on": [0, 5], "cor": [0, 4], "da": [10, 40]})
        out = next_goal.build_next_goal([r])
        self.assertEqual(out["picks"], [])
        # odpada na minucie (sito 1)
        self.assertEqual(out["funnel"][1]["after"], 0)

    def test_kiryat_yam_early_and_no_rival_shots(self):
        r = self.base(team="Hapoel Kiryat Yam", opponent="Ashdod", side="D", minutes="18", score="2:0",
                      live={"on": [4, 0], "cor": [0, 1], "da": [14, 15]},
                      t={"temp": 70, "shots": True}, opp_att={"gf_avg": 1.6})
        # nowa presja ~0.31 (nie stary równy live_share 0.51)
        pr = next_goal.next_live_pressure(r["live"])
        self.assertIsNotNone(pr)
        self.assertLess(pr["share"], 0.40)
        self.assertEqual(next_goal.build_next_goal([r])["picks"], [])

    def test_maccabi_petah_fifty_fifty_no_shots(self):
        r = self.base(team="Maccabi Petah Tikva", opponent="Maccabi Tel Aviv", side="W",
                      minutes="58", score="1:1",
                      live={"on": [2, 2], "cor": [3, 3], "da": [15, 15]},
                      t={"temp": 73, "shots": False}, opp_att={"gf_avg": 2.6})
        self.assertEqual(next_goal.build_next_goal([r])["picks"], [])

    def test_zalau_rival_not_dominating(self):
        # side=W, score 1:2 → leaky (away) 2, rival (home) 1; leaky prowadzi 2:1
        r = self.base(team="Scm Zalau", opponent="CS Academica Recea", side="W",
                      minutes="59", score="1:2",
                      live={"on": [3, 1], "cor": [4, 1], "da": [40, 20]},
                      t={"temp": 60, "shots": True}, opp_att={"gf_avg": 1.8})
        g = next_goal.leaky_rival_goals(r)
        self.assertEqual(g, (2, 1))
        self.assertEqual(next_goal.build_next_goal([r])["picks"], [])

    def test_balanced_out(self):
        r = self.base(balanced=True, minutes="40",
                      live={"on": [0, 4], "cor": [0, 3], "da": [10, 50]},
                      opp_att={"gf_avg": 2.0})
        self.assertEqual(next_goal.build_next_goal([r])["picks"], [])

    def test_funnel_steps(self):
        out = next_goal.build_next_goal([self.base()])
        self.assertEqual(len(out["funnel"]), 1 + len(next_goal.SIEVES))
        self.assertEqual(out["funnel"][0]["name"], "start (temperatura ≥ 40)")

    def test_empty_list_ok(self):
        out = next_goal.build_next_goal([])
        self.assertEqual(out["picks"], [])
        self.assertEqual(out["funnel"][0]["after"], 0)

    def test_holes_payload_includes_next_goal_without_changing_rules(self):
        # pusta lista: next_goal obecne; rule/chance_rule nienaruszone w module
        self.assertTrue(hasattr(app, "CHANCE_RULE") and "35%" in app.CHANCE_RULE)
        self.assertEqual(app.SECTIONS, ("main", "early", "balanced"))


class TestNextGoalTips(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.tmp = tempfile.mkdtemp()
        self.old_log, self.old_les = app.NG_LOG_PATH, app.NG_LESSONS_PATH
        app.NG_LOG_PATH = os.path.join(self.tmp, "ng.json")
        app.NG_LESSONS_PATH = os.path.join(self.tmp, "les.json")
        with app.NG_LOCK:
            self.backup_log = list(app.NG_LOG); self.backup_les = list(app.NG_LESSONS)
            app.NG_LOG[:] = []; app.NG_LESSONS[:] = []
    def tearDown(self):
        with app.NG_LOCK:
            app.NG_LOG[:] = self.backup_log; app.NG_LESSONS[:] = self.backup_les
        app.NG_LOG_PATH, app.NG_LESSONS_PATH = self.old_log, self.old_les

    def pick(self, **kw):
        p = {
            "eventId": 101, "betradarId": "br1", "team": "Skopje", "opponent": "Tikvesh",
            "side": "W", "minutes": "59", "score": "1:0", "leaky_gf": 0, "rival_gf": 1, "rywal_gd": 1,
            "live": {"on": [0, 3], "cor": [1, 3], "da": [21, 54]}, "pressure": 88, "pressure_share": 0.88,
            "pressure_parts": {"on": 1.0, "da": 0.72, "cor": 0.75}, "temp": 64, "shots": True, "gf_avg": 2.2,
            "next_goal_score": 80, "score_parts": [{"name": "presja", "weight": 50, "value": 88}],
            "sieves_passed": ["minuta 25–75"], "tip": "Tikvesh — następny gol", "balanced": False,
            "tournament": "1. MFL", "category": "Macedonia",
        }
        p.update(kw); return p

    def test_place_tip_snapshot_and_no_duplicate(self):
        r = app.ng_place_tip(self.pick())
        self.assertTrue(r["ok"]); e = r["entry"]
        self.assertEqual(e["status"], "pending")
        self.assertEqual(e["eventId"], 101)
        self.assertEqual(e["score_h"], 1); self.assertEqual(e["score_a"], 0)
        self.assertEqual(e["leaky_gf"], 0); self.assertEqual(e["rival_gf"], 1)
        self.assertEqual(e["live"]["on"], [0, 3])
        self.assertIsNotNone(e["time_warsaw"])
        self.assertFalse(e["balanced"])
        r2 = app.ng_place_tip(self.pick())
        self.assertFalse(r2["ok"]); self.assertIn("aktywny", r2["error"])

    def test_settle_rival_goal_win(self):
        e = next_goal.snapshot_from_pick(self.pick(), "2026-11-06 14:00:00")
        tl = {"match": {"result": {"home": 2, "away": 0}, "status": {"_id": 100}},
              "events": [{"type": "goal", "time": 70, "team": "home", "result": {"home": 2, "away": 0}}]}
        # tip side W → leaky=away, rival=home; home scores → win
        r = next_goal.settle_tip(e, tl)
        self.assertEqual(r["status"], "win"); self.assertEqual(r["goal_minute"], 70)

    def test_settle_leaky_goal_loss(self):
        e = next_goal.snapshot_from_pick(self.pick(), "t")
        tl = {"match": {"result": {"home": 1, "away": 1}, "status": {"_id": 100}},
              "events": [{"type": "goal", "time": 66, "team": "away", "result": {"home": 1, "away": 1}}]}
        r = next_goal.settle_tip(e, tl)
        self.assertEqual(r["status"], "loss"); self.assertEqual(r["goal_minute"], 66)

    def test_settle_no_goal_ft_loss(self):
        e = next_goal.snapshot_from_pick(self.pick(), "t")
        tl = {"match": {"result": {"home": 1, "away": 0}, "status": {"_id": 100}}, "events": []}
        r = next_goal.settle_tip(e, tl)
        self.assertEqual(r["status"], "loss"); self.assertTrue(r.get("no_goal"))

    def test_settle_ignores_goal_before_tip_minute(self):
        e = next_goal.snapshot_from_pick(self.pick(minutes="59", score="1:0"), "t")
        # gol w 50' ignorowany; mecz trwa bez późniejszego gola → pending
        tl = {"match": {"result": {"home": 1, "away": 0}, "status": {"_id": 0}},
              "events": [{"type": "goal", "time": 50, "team": "home", "result": {"home": 1, "away": 0}}]}
        r = next_goal.settle_tip(e, tl)
        self.assertTrue(r.get("pending"))

    def test_analysis_has_cause(self):
        e = next_goal.snapshot_from_pick(self.pick(), "t")
        st = {"status": "loss", "no_goal": True, "final": "1:0", "goal_minute": None, "goal_side": None}
        an = next_goal.analyze_tip(e, st)
        self.assertEqual(an["C_cause"]["code"], "no_goal")
        self.assertIn("A_snapshot", an); self.assertIn("D_counterfactual", an)

    def test_insights_no_lessons_under_15(self):
        log = []
        for i in range(10):
            e = next_goal.snapshot_from_pick(self.pick(eventId=i, minutes="55"), "t")
            e["status"] = "win" if i % 2 == 0 else "loss"
            e["cause"] = {"code": "win" if e["status"] == "win" else "no_goal"}
            log.append(e)
        ins = next_goal.aggregate_insights(log)
        self.assertTrue(ins["sample_too_small"]); self.assertEqual(ins["lessons"], [])

    def test_insights_lessons_when_enough(self):
        log = []
        # 16 settled, bucket minuta_60-75 with 8 losses → rate 0 < 35%
        for i in range(16):
            e = next_goal.snapshot_from_pick(self.pick(eventId=i, minutes="70", score="0:1", side="D",
                                                       leaky_gf=0, rival_gf=1, rywal_gd=1), "t")
            # fix snapshot fields after override
            e["minute"] = 70; e["rywal_gd"] = 1; e["pressure_share"] = 0.7; e["shots"] = True
            e["status"] = "loss"; e["cause"] = {"code": "no_goal"}; e["no_goal"] = True
            log.append(e)
        ins = next_goal.aggregate_insights(log)
        self.assertFalse(ins["sample_too_small"])
        self.assertTrue(any(L["bucket"] == "minuta_60-75" and L["delta"] < 0 for L in ins["lessons"]))

    def test_html_has_typoj_and_hash(self):
        self.assertIn("Typuj", app.HTML); self.assertIn("#nastepny", app.HTML)
        self.assertIn("Historia typów", app.HTML); self.assertIn("Wnioski", app.HTML)


if __name__ == "__main__":
    unittest.main()
