#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ranking faworytów wg strzałów z ostatnich 5 meczów.
Tylko biblioteka standardowa Pythona 3.9+. Uruchom: python app.py -> http://localhost:8000
Źródła: Superbet PL (lista meczów, kursy 1X2), Sportradar gismo (statystyki). Bez kluczy i logowania."""
import json, os, sys, time, threading, urllib.request, urllib.parse, webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", "8000"))
SB = "https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL"
G = "https://stats.fn.sportradar.com/betradar/en/Europe:Berlin/gismo/"
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TEAM_TTL = 6 * 3600
LIST_TTL = 55
WEEK_DAYS = 7
SEM = threading.Semaphore(4)          # max 4 równoległych zapytań HTTP
LOCK = threading.Lock()
CACHE = {}                            # klucz -> (czas, wartość)
ERRORS = {}                           # źródło -> ostatni błąd
COUNTERS = {"http_ok": 0, "http_fail": 0, "sportradar_ok": 0, "sportradar_fail": 0, "superbet_ok": 0, "superbet_fail": 0}
LAST_OK = {}                          # źródło -> czas ostatniej udanej odpowiedzi
STARTED = time.time()
def _inc(k):
    with LOCK: COUNTERS[k] = COUNTERS.get(k, 0) + 1

# ---------- czas Warszawy (bez zoneinfo/tzdata, działa też na Windows) ----------
def _last_sunday(y, m):
    d = datetime(y, m + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if m < 12 else datetime(y, 12, 31, tzinfo=timezone.utc)
    return d - timedelta(days=(d.weekday() + 1) % 7)

def warsaw_offset(utc_dt):
    y = utc_dt.year
    start = _last_sunday(y, 3).replace(hour=1)
    end = _last_sunday(y, 10).replace(hour=1)
    return timedelta(hours=2 if start <= utc_dt < end else 1)

def to_warsaw(utc_dt):
    return (utc_dt + warsaw_offset(utc_dt)).replace(tzinfo=None)

# ---------- HTTP ----------
def http_json(url, source):
    last = None
    for i in range(3):
        try:
            with SEM:
                req = urllib.request.Request(url, headers=HDR)
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = json.loads(r.read().decode("utf-8"))
            ERRORS.pop(source, None)
            _inc("http_ok"); _inc(source.lower() + "_ok"); LAST_OK[source] = time.time()
            return data
        except Exception as e:
            last = e
            time.sleep(0.7 * (i + 1))
    ERRORS[source] = "%s: %s" % (type(last).__name__, last)
    _inc("http_fail"); _inc(source.lower() + "_fail")
    return None

def cached(key, ttl, fn):
    with LOCK:
        v = CACHE.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    val = fn()
    if val is None and v:             # błąd źródła -> ostatni cache
        return v[1]
    if val is not None:
        with LOCK:
            CACHE[key] = (time.time(), val)
    return val

def gismo(ep):
    d = http_json(G + ep, "Sportradar")
    try:
        return d["doc"][0]["data"]
    except Exception:
        return None

# ---------- Superbet ----------
def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_1x2(odds):
    o1 = o2 = ox = None
    for o in odds or []:
        if (o.get("marketName") or "").strip() not in ("Mecz", "1X2", "Zwycięzca meczu"):
            continue
        c = str(o.get("code") or o.get("name"))
        try:
            p = float(o.get("price"))
        except Exception:
            continue
        if c == "1": o1 = p
        elif c == "2": o2 = p
        elif c in ("0", "X"): ox = p
    return o1, ox, o2

def event_odds(eid):
    def f():
        d = http_json("%s/events/%s" % (SB, eid), "Superbet")
        if not d: return None
        ev = d.get("data")
        if isinstance(ev, list): ev = ev[0] if ev else {}
        return parse_1x2((ev or {}).get("odds"))
    return cached("odds:%s" % eid, LIST_TTL, f)

def struct():
    def f():
        d = http_json(SB + "/struct", "Superbet")
        if not d: return None
        d = d.get("data") or {}
        nm = lambda x: (x.get("localNames") or {}).get("pl-PL") or ""
        return {"t": {str(x["id"]): (nm(x), x.get("order") or 0) for x in d.get("tournaments") or []},
                "c": {str(x["id"]): nm(x) for x in d.get("categories") or []}}
    return cached("struct", 3600, f) or {"t": {}, "c": {}}

def _window(state, now):
    if state == "live":
        return now - timedelta(hours=5), now + timedelta(hours=2)
    return now, now + timedelta(days=WEEK_DAYS)

def parse_utc(s):
    try: return datetime.strptime(str(s)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception: return None

def fetch_list(state):
    now = datetime.now(timezone.utc)
    a, b = _window(state, now)
    q = urllib.parse.urlencode({"currentStatus": "active", "offerState": state,
                                "startDate": fmt(a), "endDate": fmt(b)})
    d = http_json(SB + "/events/by-date?" + q, "Superbet")
    if d is None: return None
    stc = struct()
    out = []
    for e in d.get("data") or []:
        if e.get("sportId") != 5: continue
        m = e.get("metadata") or {}
        o1, ox, o2 = parse_1x2(e.get("odds"))
        name = e.get("matchName") or ""
        parts = name.split("·") if "·" in name else name.split(" - ")
        start = e.get("utcDate") or e.get("matchDate") or e.get("startDate")
        dt = parse_utc(start)
        tn = stc["t"].get(str(e.get("tournamentId")), ("", 0))
        out.append({"eventId": e.get("eventId"), "name": name.replace("·", " – "),
                    "home": parts[0].strip() if parts else "", "away": parts[1].strip() if len(parts) > 1 else "",
                    "br": str(e.get("betradarId") or m.get("brId") or "") or None,
                    "tournamentId": e.get("tournamentId"), "league": tn[0] or None, "lorder": tn[1],
                    "country": stc["c"].get(str(e.get("categoryId"))) or None,
                    "start": start, "ts": int(dt.timestamp() * 1000) if dt else None,
                    "start_warsaw": to_warsaw(dt).strftime("%H:%M") if dt else "",
                    "day_warsaw": to_warsaw(dt).strftime("%Y-%m-%d") if dt else "",
                    "minutes": m.get("minutes"), "period": m.get("periodStatus"),
                    "score": None if m.get("homeTeamScore") is None else "%s:%s" % (m.get("homeTeamScore"), m.get("awayTeamScore")),
                    "status": m.get("status"), "mode": state, "o1": o1, "ox": ox, "o2": o2})
    if state == "live":   # w liście live brak kursów -> równolegle /events/{id} (cache 55 s)
        need = [e for e in out if e["o1"] is None]
        with ThreadPoolExecutor(8) as ex:
            for e, o in zip(need, ex.map(lambda e: event_odds(e["eventId"]), need)):
                if o: e["o1"], e["ox"], e["o2"] = o
    return out

def get_list(state):
    return cached("list:" + state, LIST_TTL, lambda: fetch_list(state)) or []

def matches():
    with ThreadPoolExecutor(2) as ex:
        live, pre = ex.map(get_list, ["live", "prematch"])
    ids = {e["eventId"] for e in live}
    evs = live + [e for e in pre if e["eventId"] not in ids]
    keys = ("eventId", "home", "away", "name", "league", "lorder", "country", "ts", "start_warsaw", "day_warsaw",
            "minutes", "score", "mode", "o1", "ox", "o2")
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "now_ts": int(now.timestamp() * 1000),
            "today_warsaw": to_warsaw(now).strftime("%Y-%m-%d"),
            "matches": [{k: e.get(k) for k in keys} for e in evs], "errors": dict(ERRORS)}

# ---------- Sportradar ----------
def to_int(x):
    try: return int(float(str(x).strip()))
    except Exception: return None

def match_details(mid):
    def f():
        d = gismo("match_detailsextended/%s" % mid)
        if d is None: return None
        if not isinstance(d, dict): return {"on": None, "tot": None}
        v = d.get("values") or {}
        if not isinstance(v, dict): v = {}
        def pair(k):
            val = (v.get(k) or {}).get("value") or {}
            h, a = to_int(val.get("home")), to_int(val.get("away"))
            return None if h is None or a is None else (h, a)
        return {"on": pair("125"), "tot": pair("goalattempts")}
    return cached("det:%s" % mid, 7 * 24 * 3600, f)

def match_info(br):
    def f():
        d = gismo("match_info/%s" % br)
        if not d or not isinstance(d, dict): return None
        m = d.get("match") or {}
        t = m.get("teams") or {}
        cat = d.get("realcategory") or d.get("category") or m.get("_category") or {}
        return {"home": {"uid": (t.get("home") or {}).get("uid"), "name": (t.get("home") or {}).get("name")},
                "away": {"uid": (t.get("away") or {}).get("uid"), "name": (t.get("away") or {}).get("name")},
                "tournament": (d.get("tournament") or {}).get("name"), "category": cat.get("name")}
    return cached("mi:%s" % br, TEAM_TTL, f)

def team_last5(uid, exclude_br):
    def f():
        d = gismo("stats_team_lastx/%s/15" % uid)
        if d is None: return None
        if not isinstance(d, dict): d = {}
        ms = [x for x in (d.get("matches") or [])
              if (x.get("result") or {}).get("home") is not None and str(x.get("_id")) != str(exclude_br)]
        ms.sort(key=lambda x: (x.get("time") or {}).get("uts") or 0, reverse=True)
        ms = ms[:5]
        dets = [match_details(x["_id"]) for x in ms]
        games = []
        for x, dd in zip(ms, dets):
            ish = (x["teams"]["home"].get("uid") == uid)
            i = 0 if ish else 1
            r = x["result"]
            gf, ga = (r["home"], r["away"]) if ish else (r["away"], r["home"])
            g = {"date": (x.get("time") or {}).get("date"), "uts": (x.get("time") or {}).get("uts"),
                 "opp": x["teams"]["away" if ish else "home"].get("name"), "ha": "D" if ish else "W",
                 "score": "%s:%s" % (gf, ga), "on": None, "tot": None}
            if dd:
                on, tot = dd.get("on"), dd.get("tot")
                # same zera przy niezerowym wyniku = brak danych
                if (gf or ga) and (on in (None, (0, 0))) and (tot in (None, (0, 0))):
                    on = tot = None
                if on: g["on"] = on[i]
                if tot: g["tot"] = tot[i]
            games.append(g)
        return games
    return cached("team:%s:%s" % (uid, exclude_br), TEAM_TTL, f)

# ---------- logika (czysta, testowalna) ----------
def pick_favourite(o1, o2, odds_min, odds_max, gap=0.5):
    if o1 is None or o2 is None: return None
    if o1 <= o2: side, fav, other = "home", o1, o2
    else: side, fav, other = "away", o2, o1
    if odds_min <= fav <= odds_max and other - fav >= gap - 1e-9:
        return side, fav, other
    return None

def compute_stats(games):
    with_data = [g for g in games if g.get("on") is not None or g.get("tot") is not None]
    on = [g["on"] for g in games if g.get("on") is not None]
    tot = [g["tot"] for g in games if g.get("tot") is not None]
    both = [g for g in games if g.get("on") is not None and g.get("tot") is not None]
    s_on, s_tot = sum(on), sum(tot)
    b_on, b_tot = sum(g["on"] for g in both), sum(g["tot"] for g in both)
    return {"games_with_data": len(with_data), "games_total": len(games),
            "sum_on": s_on, "avg_on": round(s_on / len(on), 2) if on else None,
            "sum_tot": s_tot, "avg_tot": round(s_tot / len(tot), 2) if tot else None,
            "accuracy": round(100.0 * b_on / b_tot, 1) if b_tot else None}

def qualifies(r):
    """Obie drużyny 5/5 meczów z danymi; 4 vs 5 tylko gdy przewaga faworyta >= 1,5 celnego/mecz."""
    st, os_ = r.get("stats") or {}, r.get("opp_stats") or {}
    fa, ob = st.get("games_with_data") or 0, os_.get("games_with_data") or 0
    if fa >= 5 and ob >= 5: return True
    if sorted((min(fa, 5), min(ob, 5))) == [4, 5]:
        x, y = st.get("avg_on"), os_.get("avg_on")
        return x is not None and y is not None and x - y >= 1.5
    return False

def build_ranking(rows, min_games=None, sort_key="avg_on"):
    ok = [r for r in rows if qualifies(r)]
    few = [r for r in rows if not qualifies(r)]
    def k(r):
        x, y = r["stats"].get("avg_on") or 0, ((r.get("opp_stats") or {}).get("avg_on") or 0)
        return (x - y, x)                     # głównie różnica średnich celnych na mecz
    ok.sort(key=k, reverse=True)
    for i, r in enumerate(ok, 1): r["rank"] = i
    return ok, few

# ---------- składanie ----------
def process_event(ev, odds_min, odds_max):
    try:
        return _process_event(ev, odds_min, odds_max)
    except Exception as e:
        ERRORS["przetwarzanie"] = "%s: %s" % (type(e).__name__, e)
        return ("nostats", None)

def _process_event(ev, odds_min, odds_max):
    o1, o2 = ev["o1"], ev["o2"]
    fv = pick_favourite(o1, o2, odds_min, odds_max)
    if not fv: return ("nofav", None)
    side, fav, other = fv
    if not ev["br"]: return ("nobr", None)
    mi = match_info(ev["br"])
    if not mi or not mi[side]["uid"]: return ("nostats", None)
    games = team_last5(mi[side]["uid"], ev["br"])
    if games is None: return ("nostats", None)
    opp = "away" if side == "home" else "home"
    st = compute_stats(games)
    og = team_last5(mi[opp]["uid"], ev["br"]) if mi[opp]["uid"] else None
    ost = compute_stats(og) if og is not None else None
    row = {"team": mi[side]["name"], "opponent": mi[opp]["name"], "side": "D" if side == "home" else "W",
           "match": ev["name"], "tournament": mi.get("tournament") or ev.get("league"), "category": mi.get("category"),
           "mode": ev["mode"], "start_warsaw": ev.get("start_warsaw", ""), "day_warsaw": ev.get("day_warsaw"),
           "minutes": ev["minutes"], "score": ev["score"],
           "odds_fav": fav, "odds_other": other, "odds_draw": ev.get("ox"),
           "eventId": ev["eventId"], "betradarId": ev["br"], "games": games, "stats": st,
           "opp_games": og, "opp_stats": ost, "diff": compare(st, ost)}
    return ("nostats" if st["games_with_data"] == 0 else "ok", row)

def compare(a, b):
    """Różnica faworyt - rywal; None gdy brak danych po którejkolwiek stronie."""
    out = {}
    for k in ("avg_on", "avg_tot", "sum_on", "sum_tot", "accuracy"):
        x, y = (a or {}).get(k), (b or {}).get(k)
        if (b is None or not b.get("games_with_data") or not (a or {}).get("games_with_data")) or x is None or y is None:
            out[k] = None
        else:
            out[k] = round(x - y, 2)
    return out

def ranking(mode="all", min_games=3, odds_min=1.2, odds_max=2.0, sort_key="sum_on", progress=None):
    evs = []
    if mode in ("live", "all"): evs += get_list("live")
    if mode in ("prematch", "all"):
        ids = {e["eventId"] for e in evs}
        today = to_warsaw(datetime.now(timezone.utc)).strftime("%Y-%m-%d")   # ranking: przedmeczowe tylko dziś
        evs += [e for e in get_list("prematch") if e["eventId"] not in ids and e.get("day_warsaw") == today]
    evs = [dict(e) for e in evs]
    # najpierw odfiltruj mecze bez faworyta (bez żadnych zapytań HTTP)
    favs = [e for e in evs if pick_favourite(e["o1"], e["o2"], odds_min, odds_max)]
    res = [("nofav", None)] * (len(evs) - len(favs))
    if progress is not None: progress.update(done=0, total=len(favs))
    def run(e):
        r = process_event(e, odds_min, odds_max)
        if progress is not None:
            with LOCK: progress["done"] += 1
        return r
    with ThreadPoolExecutor(4) as ex:
        res += list(ex.map(run, favs))
    rows = [r for k, r in res if r]
    ok, few = build_ranking(rows, min_games, sort_key)
    cnt = lambda k: sum(1 for kk, _ in res if kk == k)
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
            "params": {"min_games": min_games, "odds_min": odds_min, "odds_max": odds_max},
            "ranking": ok, "too_few": few,
            "footer": {"matches": len(evs), "without_betradar": sum(1 for e in evs if not e["br"]),
                       "favourites": len(rows) + cnt("nobr") + sum(1 for k, r in res if k == "nostats" and not r),
                       "favourites_without_betradar": cnt("nobr"), "without_stats": cnt("nostats")},
            "errors": dict(ERRORS)}

# ---------- ranking w tle (single-flight + cache) ----------
TTL = {"live": 180, "prematch": 600, "all": 180}
JOBS = {}                             # klucz -> {"thread", "progress", "started"}
RESULTS = {}                          # klucz -> (czas, wynik, czas_liczenia_s)

def _rkey(mode, mg, omin, omax): return (mode, int(mg), round(float(omin), 2), round(float(omax), 2))

def _worker(key):
    prog = JOBS[key]["progress"]; t0 = time.time()
    try:
        res = ranking(key[0], key[1], key[2], key[3], progress=prog)
        res["compute_seconds"] = round(time.time() - t0, 1)
        with LOCK: RESULTS[key] = (time.time(), res)
    except Exception as e:
        ERRORS["ranking"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK: JOBS.pop(key, None)

def get_ranking(mode="all", min_games=3, odds_min=1.2, odds_max=2.0):
    key = _rkey(mode, min_games, odds_min, odds_max)
    with LOCK:
        r = RESULTS.get(key)
        fresh = r and time.time() - r[0] < TTL[mode]
        job = JOBS.get(key)
        if not fresh and not job:
            busy = [k for k in JOBS if k[0] == mode]          # jeden wątek na tryb
            if busy: job = JOBS[busy[0]]
            else:
                if len(RESULTS) > 20: RESULTS.clear()
                job = JOBS[key] = {"progress": {"done": 0, "total": 0}, "started": time.time()}
                threading.Thread(target=_worker, args=(key,), daemon=True).start()
    if r:
        out = dict(r[1]); out["status"] = "ready"; out["stale"] = not fresh
        out["age_seconds"] = round(time.time() - r[0]); out["errors"] = dict(ERRORS)
        if job: out["progress"] = dict(job["progress"])
        return out
    return {"status": "computing", "progress": dict(job["progress"]),
            "elapsed_seconds": round(time.time() - job["started"]), "errors": dict(ERRORS)}

def status():
    with LOCK:
        return {"uptime_seconds": round(time.time() - STARTED), "errors": dict(ERRORS), "counters": dict(COUNTERS),
                "last_ok_seconds_ago": {k: round(time.time() - v) for k, v in LAST_OK.items()},
                "jobs": [{"key": list(k), "progress": dict(j["progress"]), "elapsed_seconds": round(time.time() - j["started"])} for k, j in JOBS.items()],
                "results": [{"key": list(k), "age_seconds": round(time.time() - v[0]), "compute_seconds": v[1].get("compute_seconds"),
                             "ranking": len(v[1]["ranking"]), "too_few": len(v[1]["too_few"]), "footer": v[1]["footer"]} for k, v in RESULTS.items()],
                "cache_entries": len(CACHE), "threads": threading.active_count()}

# ---------- serwer ----------
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {"/logo.png": ("logo.png", "image/png"), "/icon.png": ("icon.png", "image/png"),
          "/favicon.png": ("favicon.png", "image/png"), "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
          "/favicon.ico": ("favicon.ico", "image/x-icon")}

def static_file(path):
    """Zwraca (bytes, content_type) dla plików graficznych lub None."""
    if path not in STATIC: return None
    name, ctype = STATIC[path]
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as fh: return fh.read(), ctype
    except OSError: return None

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, HTML, "text/html; charset=utf-8")
        st = static_file(u.path)
        if st:
            self.send_response(200); self.send_header("Content-Type", st[1])
            self.send_header("Content-Length", str(len(st[0]))); self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers(); return self.wfile.write(st[0])
        if u.path == "/api/matches":
            try: return self.send(200, json.dumps(matches(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/status":
            return self.send(200, json.dumps(status(), ensure_ascii=False), "application/json; charset=utf-8")
        if u.path == "/healthz":
            return self.send(200, "ok", "text/plain")
        if u.path == "/api/ranking":
            q = dict(urllib.parse.parse_qsl(u.query))
            try:
                mode = q.get("mode", "all")
                if mode not in ("live", "prematch", "all"): mode = "all"
                res = get_ranking(mode, int(q.get("min_games", 3)), float(str(q.get("odds_min", 1.2)).replace(",", ".")),
                              float(str(q.get("odds_max", 2.0)).replace(",", ".")))
                return self.send(200, json.dumps(res, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e:
                return self.send(500, json.dumps({"error": str(e)}), "application/json")
        self.send(404, "nie znaleziono", "text/plain; charset=utf-8")

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print("Favsite działa: http://localhost:%d  (Ctrl+C = stop)" % port)
    threading.Thread(target=lambda: (matches(), get_ranking("all")), daemon=True).start()  # rozgrzanie cache
    if "--open" in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:%d" % port)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("Zatrzymano.")

if __name__ == "__main__":
    main()
