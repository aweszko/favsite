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
        return {"on": pair("125"), "tot": pair("goalattempts"), "cor": pair("124"), "da": pair("1029")}
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
                "tournament": (d.get("tournament") or {}).get("name"), "category": cat.get("name"),
                "season": m.get("_seasonid") or (d.get("season") or {}).get("_id")}
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
                 "score": "%s:%s" % (gf, ga), "gf": to_int(gf), "ga": to_int(ga), "on": None, "tot": None, "cor": None}
            if dd:
                on, tot = dd.get("on"), dd.get("tot")
                # same zera przy niezerowym wyniku = brak danych
                if (gf or ga) and (on in (None, (0, 0))) and (tot in (None, (0, 0))):
                    on = tot = None
                if on: g["on"] = on[i]
                if tot: g["tot"] = tot[i]
                cor = dd.get("cor")
                if cor and g["on"] is not None: g["cor"] = cor[i]
            games.append(g)
        return games
    return cached("team2:%s:%s" % (uid, exclude_br), TEAM_TTL, f)

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
           "season": mi.get("season"), "uid": mi[side]["uid"], "opp_uid": mi[opp]["uid"],
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

# ---------- VIP: najostrzejsze typy (czysta logika + liczenie w tle) ----------
VIP_RULES = {
    "odds_min": 1.00,                 # reguła użytkownika: brak progu kursu (typuje rynki goli)
    "odds_range": (1.00, 2.00),       # zakres kursu faworyta dla kandydatów VIP
    "live_dom": {"on_diff": 2, "on_riv_max": 1, "da_ratio": 1.3, "da_diff": 10},
    "games": 5,                       # ściśle 5/5 meczów z danymi dla obu drużyn
    "live":     {"on": 1.5, "tot": 3.0, "cor": 1.0, "pts": 9},
    "prematch": {"on": 2.0, "tot": 4.0, "cor": 1.5, "pts": 10},
    "gpm_min": 1.3,                   # gole faworyta na mecz w tabeli ligowej
    "table_half": 0.5,                # faworyt w górnej połowie tabeli
}

def form_stats(games):
    """Gole, bilans, punkty i rożne z ostatnich meczów (None gdy brak)."""
    games = games or []
    gs = [g for g in games if g.get("gf") is not None and g.get("ga") is not None]
    cor = [g["cor"] for g in games if g.get("cor") is not None]
    pts = sum(3 if g["gf"] > g["ga"] else 1 if g["gf"] == g["ga"] else 0 for g in gs)
    return {"games": len(gs), "points": pts if gs else None, "gd": sum(g["gf"] - g["ga"] for g in gs) if gs else None,
            "gf_avg": round(sum(g["gf"] for g in gs) / len(gs), 2) if gs else None,
            "cor_games": len(cor), "avg_cor": round(sum(cor) / len(cor), 2) if cor else None}

def table_row(season, uid):
    """Pozycja i gole/mecz drużyny z tabeli Sportradar; None = brak danych."""
    if not season or not uid: return None
    def f():
        d = gismo("stats_season_tables/%s" % season)
        if d is None: return None
        out = {}
        for t in (d.get("tables") or []) if isinstance(d, dict) else []:
            rows = t.get("tablerows") or []
            for r in rows:
                u = (r.get("team") or {}).get("uid")
                if u is None or u in out: continue
                out[u] = {"pos": to_int(r.get("pos")), "of": len(rows), "played": to_int(r.get("total")),
                          "gf": to_int(r.get("goalsForTotal")), "group": t.get("name")}
        return out
    tab = cached("table:%s" % season, 3600, f)
    if tab is None: return None
    r = tab.get(uid) or tab.get(str(uid)) or tab.get(to_int(uid))
    if not r or not r.get("pos") or not r.get("played"): return None
    r = dict(r); r["gpm"] = round(r["gf"] / r["played"], 2) if r.get("gf") is not None else None
    return r

def _chk(name, ok, fav, riv, need):
    return {"name": name, "ok": bool(ok), "fav": fav, "riv": riv, "need": need}

def vip_check(r, cur_odds, live_det, table_f, table_o):
    """Zwraca listę kryteriów (wszystkie muszą mieć ok=True)."""
    live = r.get("mode") == "live"
    th = VIP_RULES["live" if live else "prematch"]
    st, os_ = r.get("stats") or {}, r.get("opp_stats") or {}
    ff, fo = form_stats(r.get("games")), form_stats(r.get("opp_games"))
    n = VIP_RULES["games"]
    sub = lambda a, b: None if a is None or b is None else round(a - b, 2)
    c = []
    c.append(_chk("Kurs na faworyta (bez progu)", True, cur_odds if cur_odds is not None else "brak danych", None, "dowolny"))
    c.append(_chk("Mecze z danymi 5/5 (obie drużyny)",
                  (st.get("games_with_data") or 0) >= n and (os_.get("games_with_data") or 0) >= n
                  and ff["cor_games"] >= n and fo["cor_games"] >= n,
                  "%s/5" % min(st.get("games_with_data") or 0, ff["cor_games"]), "%s/5" % min(os_.get("games_with_data") or 0, fo["cor_games"]), "5/5"))
    for key, lab, a, b in (("on", "Celne śr./mecz (ost. 5)", st.get("avg_on"), os_.get("avg_on")),
                           ("tot", "Strzały śr./mecz (ost. 5)", st.get("avg_tot"), os_.get("avg_tot")),
                           ("cor", "Rożne śr./mecz (ost. 5)", ff["avg_cor"], fo["avg_cor"])):
        d = sub(a, b)
        c.append(_chk(lab, d is not None and d >= th[key] - 1e-9, a, b, "przewaga ≥ %s" % str(th[key]).replace(".", ",")))
    c.append(_chk("Forma: punkty (ost. 5)", ff["points"] is not None and fo["points"] is not None
                  and ff["points"] >= th["pts"] and ff["points"] > fo["points"], ff["points"], fo["points"], "≥ %d i więcej niż rywal" % th["pts"]))
    c.append(_chk("Bilans bramek (ost. 5)", ff["gd"] is not None and fo["gd"] is not None and ff["gd"] > 0 and ff["gd"] > fo["gd"],
                  ff["gd"], fo["gd"], "> 0 i lepszy niż rywal"))
    tf, to = table_f or {}, table_o or {}
    half = tf.get("of") and tf.get("pos") and tf["pos"] <= max(1, int(tf["of"] * VIP_RULES["table_half"] + 0.5))
    c.append(_chk("Pozycja w tabeli (górna połowa)", bool(half),
                  "%s/%s" % (tf["pos"], tf["of"]) if table_f else "brak danych",
                  "%s/%s" % (to["pos"], to["of"]) if table_o else "brak danych", "górna połowa"))
    c.append(_chk("Gole faworyta/mecz w lidze", tf.get("gpm") is not None and tf["gpm"] >= VIP_RULES["gpm_min"] - 1e-9,
                  tf.get("gpm") if table_f else "brak danych", to.get("gpm") if table_o else "brak danych", "≥ 1,3"))
    if live:
        ld = live_det or {}
        side = 0 if r.get("side") == "D" else 1
        ld_ = VIP_RULES["live_dom"]
        def lv(k):
            p = ld.get(k)
            return (p[side], p[1 - side]) if p else (None, None)
        fa, rv = lv("on")
        c.append(_chk("LIVE: celne w meczu", fa is not None and fa >= rv + ld_["on_diff"] and rv <= ld_["on_riv_max"],
                      fa if fa is not None else "brak danych", rv if rv is not None else "brak danych",
                      "faw. ≥ rywal + %d i rywal ≤ %d" % (ld_["on_diff"], ld_["on_riv_max"])))
        fa, rv = lv("cor")
        c.append(_chk("LIVE: rożne w meczu", fa is not None and fa > rv,
                      fa if fa is not None else "brak danych", rv if rv is not None else "brak danych", "faw. > rywal"))
        fa, rv = lv("da")
        c.append(_chk("LIVE: groźne ataki", fa is not None and fa >= ld_["da_ratio"] * rv - 1e-9 and fa >= rv + ld_["da_diff"],
                      fa if fa is not None else "brak danych", rv if rv is not None else "brak danych",
                      "faw. ≥ 1,3× rywal i ≥ rywal + %d" % ld_["da_diff"]))
    return c

def live_details(br):
    def f():
        d = gismo("match_detailsextended/%s" % br)
        if not isinstance(d, dict): return None
        v = d.get("values") or {}
        def pair(k):
            val = (v.get(k) or {}).get("value") or {}
            h, a = to_int(val.get("home")), to_int(val.get("away"))
            return None if h is None or a is None else (h, a)
        return {"on": pair("125"), "cor": pair("124"), "da": pair("1029")}
    return cached("live:%s" % br, 60, f)

def vip(rank_res):
    rows = list(rank_res.get("ranking") or []) + list(rank_res.get("too_few") or [])
    live_now = {e["eventId"]: e for e in get_list("live")}
    cands = []
    for r in rows:
        st, os_ = r.get("stats") or {}, r.get("opp_stats") or {}
        if (st.get("games_with_data") or 0) < 5 or (os_.get("games_with_data") or 0) < 5: continue   # tani filtr
        cands.append(r)
    picks, rejected, watch = [], 0, []
    for r in cands:
        r = dict(r)
        cur = live_now.get(r["eventId"])
        if r["mode"] == "live" and not cur: continue                    # mecz już się skończył
        if r["mode"] != "live" and cur: r["mode"] = "live"
        if cur:
            r["minutes"], r["score"] = cur.get("minutes"), cur.get("score")
            o = cur.get("o1") if r["side"] == "D" else cur.get("o2")
            r["odds_now"] = o
        else:
            r["odds_now"] = r.get("odds_fav")
        if r["odds_now"] is not None and r["odds_now"] < VIP_RULES["odds_min"] - 1e-9: rejected += 1; continue
        ld = live_details(r["betradarId"]) if r["mode"] == "live" else None
        tf = table_row(r.get("season"), r.get("uid")); to = table_row(r.get("season"), r.get("opp_uid"))
        c = vip_check(r, r["odds_now"], ld, tf, to)
        pre = vip_check(dict(r, mode="prematch"), r["odds_now"], None, tf, to)   # PRO: kryteria ost. 5 (bez live)
        if all(x["ok"] for x in pre): watch.append(pro_watch_row(r, pre))
        if all(x["ok"] for x in c):
            r["checks"] = c; r["vip_type"] = "live" if r["mode"] == "live" else "przedmeczowy"
            r["form"] = form_stats(r.get("games")); r["opp_form"] = form_stats(r.get("opp_games"))
            picks.append(r)
        else: rejected += 1
    picks.sort(key=lambda r: (r["vip_type"] != "live", -((r["stats"]["avg_on"] or 0) - (r["opp_stats"]["avg_on"] or 0))))
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "rules": VIP_RULES, "picks": picks,
            "checked": len(cands), "rejected": rejected, "ranking_generated_warsaw": rank_res.get("generated_warsaw"),
            "watch": watch, "message": None if picks else "Brak pewniaków - żaden mecz nie spełnia wszystkich kryteriów"}

VIP_TTL = 75
VIP_STATE = {"res": None, "t": 0, "job": False}

def _vip_worker(rank_res):
    try:
        res = vip(rank_res)
        with LOCK: VIP_STATE.update(res=res, t=time.time())
    except Exception as e:
        ERRORS["vip"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK: VIP_STATE["job"] = False

def get_vip():
    rk = get_ranking("all", 3, *VIP_RULES["odds_range"])   # kursy 1,00-2,00; reużywa danych rankingu (bez podwójnego pobierania)
    with LOCK:
        res, t, job = VIP_STATE["res"], VIP_STATE["t"], VIP_STATE["job"]
        fresh = res is not None and time.time() - t < VIP_TTL
        if not fresh and not job and rk.get("status") == "ready":
            VIP_STATE["job"] = job = True
            threading.Thread(target=_vip_worker, args=(rk,), daemon=True).start()
    if res is not None:
        out = dict(res); out["status"] = "ready"; out["stale"] = not fresh; out["age_seconds"] = round(time.time() - t)
        out["errors"] = dict(ERRORS); return out
    return {"status": "computing", "stage": "ranking" if rk.get("status") != "ready" else "vip",
            "progress": rk.get("progress") or {}, "errors": dict(ERRORS)}

# ---------- PRO: lista obserwowanych + wyzwalacz live + rejestr ----------
PRO_RULES = {
    "minute": (10, 80),               # okno minut meczu
    "on_diff": 2, "on_riv_max": 1,    # celne: faw. >= rywal + 2 i rywal <= 1
    "da_ratio": 1.3, "da_diff": 10,   # groźne ataki: faw. >= 1,3x rywal i >= rywal + 10
    "win": 10,                        # okno presji (ostatnie minuty)
    "win_on": 2,                      # >= 2 celne w oknie, albo
    "win_on_alt": 1, "win_cor_alt": 2,  # >= 1 celny i >= 2 rożne
    "win_riv_on_max": 0,              # rywal: 0 celnych w oknie
}
PRO_TL_TTL = 50
PRO_LOG_PATH = os.environ.get("PRO_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pro_log.json"))
PRO_LOCK = threading.Lock()

def pro_watch_row(r, pre):
    keys = ("team", "opponent", "side", "match", "tournament", "category", "mode", "start_warsaw", "day_warsaw",
            "minutes", "score", "odds_fav", "odds_now", "eventId", "betradarId")
    w = {k: r.get(k) for k in keys}
    w["checks"] = pre
    w["summary"] = "%d/%d kryteriów ost. 5 ✓" % (sum(1 for x in pre if x["ok"]), len(pre))
    return w

def tl_events(tl):
    return [e for e in ((tl or {}).get("events") or []) if not e.get("disabled")]

def tl_minute(tl):
    ts = [to_int(e.get("time")) for e in tl_events(tl)]
    ts = [t for t in ts if t is not None and t >= 0]
    return max(ts) if ts else None

def tl_ended(tl):
    m = (tl or {}).get("match") or {}
    st = m.get("status") or {}
    return st.get("_id") == 100 or (m.get("timeinfo") or {}).get("ended") not in (None, "") or m.get("matchstatus") in ("result", "ended") \
        or any(e.get("type") == "match_ended" for e in tl_events(tl))

def tl_score(tl):
    r = ((tl or {}).get("match") or {}).get("result") or {}
    h, a = to_int(r.get("home")), to_int(r.get("away"))
    if h is None or a is None:            # z ostatniego gola
        gs = [e for e in tl_events(tl) if e.get("type") == "goal" and e.get("result")]
        if gs: g = gs[-1]["result"]; return to_int(g.get("home")) or 0, to_int(g.get("away")) or 0
        return 0, 0
    return h, a

def pressure(tl, side, minute=None, win=None):
    """Celne / rożne obu drużyn w ostatnich `win` minutach. side: 'home'/'away' (faworyt)."""
    win = win or PRO_RULES["win"]
    cur = minute if minute is not None else tl_minute(tl)
    opp = "away" if side == "home" else "home"
    out = {"fav_on": 0, "riv_on": 0, "fav_cor": 0, "riv_cor": 0, "from": None, "to": cur}
    if cur is None: return out
    lo = cur - win
    out["from"] = max(0, lo)
    for e in tl_events(tl):
        t = to_int(e.get("time"))
        if t is None or t < 0 or t <= lo or t > cur: continue
        typ, tm = e.get("type"), e.get("team")
        if typ == "shotontarget" or (typ == "goal" and not e.get("owngoal")):   # gol = celny strzał
            if tm == side: out["fav_on"] += 1
            elif tm == opp: out["riv_on"] += 1
        elif typ == "corner":
            if tm == side: out["fav_cor"] += 1
            elif tm == opp: out["riv_cor"] += 1
    return out

def pro_trigger(det, pr, side, minute, score):
    """Zwraca (ok, checks). det: {'on':(h,a),'cor':(h,a),'da':(h,a)}; pr: wynik pressure()."""
    R = PRO_RULES; i = 0 if side == "home" else 1
    def lv(k):
        p = (det or {}).get(k)
        return (p[i], p[1 - i]) if p else (None, None)
    nd = lambda x: x if x is not None else "brak danych"
    c = []
    c.append(_chk("Minuta meczu", minute is not None and R["minute"][0] <= minute <= R["minute"][1],
                  nd(minute), None, "%d–%d'" % R["minute"]))
    c.append(_chk("Wynik (dowolny)", True, score or "brak danych", None, "dowolny"))
    fa, rv = lv("on")
    c.append(_chk("Celne w meczu", fa is not None and rv is not None and fa >= rv + R["on_diff"] and rv <= R["on_riv_max"],
                  nd(fa), nd(rv), "faw. ≥ rywal + %d i rywal ≤ %d" % (R["on_diff"], R["on_riv_max"])))
    fa, rv = lv("cor")
    c.append(_chk("Rożne w meczu", fa is not None and rv is not None and fa > rv, nd(fa), nd(rv), "faw. > rywal"))
    fa, rv = lv("da")
    c.append(_chk("Groźne ataki", fa is not None and rv is not None and fa >= R["da_ratio"] * rv - 1e-9 and fa >= rv + R["da_diff"],
                  nd(fa), nd(rv), "faw. ≥ 1,3× rywal i ≥ rywal + %d" % R["da_diff"]))
    p = pr or {}
    fo, fc = p.get("fav_on", 0), p.get("fav_cor", 0)
    c.append(_chk("Presja ost. %d min: faworyt" % R["win"],
                  fo >= R["win_on"] or (fo >= R["win_on_alt"] and fc >= R["win_cor_alt"]),
                  "%d cel. / %d roż." % (fo, fc), None, "≥ 2 celne lub ≥ 1 celny + ≥ 2 rożne"))
    c.append(_chk("Presja ost. %d min: rywal bez celnych" % R["win"], p.get("riv_on", 0) <= R["win_riv_on_max"],
                  None, "%d cel." % p.get("riv_on", 0), "0 celnych"))
    return all(x["ok"] for x in c), c

def settle(entry, tl):
    """Rozlicza wpis na podstawie timeline. Zwraca dict wyników albo None (mecz trwa, brak rozstrzygnięcia)."""
    side = entry["side_key"]; h0, a0 = entry["score_h"], entry["score_a"]; tot0 = h0 + a0
    nxt = None; h, a = h0, a0
    for e in tl_events(tl):
        if e.get("type") != "goal" or not e.get("result"): continue
        gh, ga = to_int(e["result"].get("home")), to_int(e["result"].get("away"))
        if gh is None or ga is None or gh + ga <= tot0: continue
        if nxt is None:
            if gh > h0 and ga == a0: nxt = "home"
            elif ga > a0 and gh == h0: nxt = "away"
            else: nxt = e.get("team") or None
        h, a = max(h, gh), max(a, ga)
    fh, fa_ = tl_score(tl); h, a = max(h, fh), max(a, fa_)
    ended = tl_ended(tl)
    res = {}
    if nxt is not None: res["next"] = "hit" if nxt == side else "miss"
    elif ended: res["next"] = "miss"            # brak następnego gola = nietrafiony
    if h + a >= tot0 + 1: res["over"] = "hit"
    elif ended: res["over"] = "miss"
    res["final"] = "%d:%d" % (h, a) if ended else None
    return res

def streaks(results):
    """results: lista 'hit'/'miss' w kolejności chronologicznej -> (bieżąca seria, najdłuższa seria trafień)."""
    best = run = 0
    for x in results:
        run = run + 1 if x == "hit" else 0; best = max(best, run)
    cur = 0; kind = None
    for x in reversed(results):
        if kind is None: kind = x
        if x != kind: break
        cur += 1
    return {"current": cur, "current_kind": kind, "longest_hit": best}

GIST_API = "https://api.github.com/gists/"
GIST_FILE = "pro_log.json"
GIST_MIN_INTERVAL = 30
GIST_STATE = {"last": 0.0, "dirty": False, "timer": None, "ok": None, "err": None}
GIST_SLOCK = threading.Lock()

def gist_cfg():
    t, g = os.environ.get("GIST_TOKEN", "").strip(), os.environ.get("GIST_ID", "").strip()
    return (t, g) if t and g else None

def persist_active():
    return gist_cfg() is not None

def _gist_http(method, gid, token, body=None, timeout=15):
    """Warstwa HTTP (podmieniana w testach). Zwraca (status, dict)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    rq = urllib.request.Request(GIST_API + gid, data=data, method=method,
                                headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                         "Content-Type": "application/json", "User-Agent": "triumfator"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8") or "{}")

def _gist_err(e):
    # nigdy nie zapisujemy tokenu - tylko typ błędu / kod HTTP
    msg = type(e).__name__ + (" %s" % e.code if hasattr(e, "code") else "")
    GIST_STATE["ok"], GIST_STATE["err"] = False, msg
    ERRORS["gist"] = msg
    print("[gist] błąd: " + msg, file=sys.stderr)

def gist_load():
    cfg = gist_cfg()
    if not cfg: return None
    try:
        st, d = _gist_http("GET", cfg[1], cfg[0])
        f = (d.get("files") or {}).get(GIST_FILE) or {}
        content = f.get("content")
        if f.get("truncated") and f.get("raw_url"):
            rq = urllib.request.Request(f["raw_url"], headers={"Authorization": "Bearer " + cfg[0], "User-Agent": "triumfator"})
            with urllib.request.urlopen(rq, timeout=15) as r: content = r.read().decode("utf-8")
        log = json.loads(content or "[]")
        if not isinstance(log, list): raise ValueError("bad log")
        GIST_STATE["ok"] = True
        return log
    except Exception as e:
        _gist_err(e); return None

def gist_push_now():
    cfg = gist_cfg()
    if not cfg: return False
    with GIST_SLOCK:
        GIST_STATE["dirty"] = False; GIST_STATE["last"] = time.time(); GIST_STATE["timer"] = None
    try:
        with PRO_LOCK: content = json.dumps(PRO_LOG, ensure_ascii=False)
        _gist_http("PATCH", cfg[1], cfg[0], {"files": {GIST_FILE: {"content": content}}})
        GIST_STATE["ok"], GIST_STATE["err"] = True, None
        ERRORS.pop("gist", None)
        return True
    except Exception as e:
        _gist_err(e); return False

def gist_schedule():
    """Zapis do gista: co najwyżej raz na ~30 s, zmiany łączone."""
    if not gist_cfg(): return
    with GIST_SLOCK:
        GIST_STATE["dirty"] = True
        if GIST_STATE["timer"] is not None: return
        delay = max(0.0, GIST_STATE["last"] + GIST_MIN_INTERVAL - time.time())
        t = threading.Timer(delay, gist_push_now); t.daemon = True
        GIST_STATE["timer"] = t
    t.start()

def pro_load():
    g = gist_load()
    if g is not None: return g
    try:
        with open(PRO_LOG_PATH, encoding="utf-8") as fh: return json.load(fh)
    except Exception: return []

def pro_save(log):
    try:
        tmp = PRO_LOG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump(log, fh, ensure_ascii=False)
        os.replace(tmp, PRO_LOG_PATH)
    except Exception as e: ERRORS["rejestr"] = "%s: %s" % (type(e).__name__, e)
    try: gist_schedule()
    except Exception as e: _gist_err(e)

PRO_LOG = pro_load()

def timeline(br, ttl=PRO_TL_TTL):
    return cached("tl:%s" % br, ttl, lambda: gismo("match_timeline/%s" % br))

def record_stats(log):
    def summ(key):
        done = [e[key] for e in log if e.get(key) in ("hit", "miss")]
        hits = done.count("hit")
        return {"types": len(log), "hits": hits, "misses": len(done) - hits, "pending": len(log) - len(done),
                "hit_rate": round(100.0 * hits / len(done), 1) if done else None, **streaks(done)}
    return {"next": summ("next"), "over": summ("over")}

def pro_settle_pending(now_ts):
    """Ponowne pobranie timeline meczów z nierozliczonymi typami (działa też po uśpieniu serwisu)."""
    with PRO_LOCK: pend = [e for e in PRO_LOG if e.get("next") is None or e.get("over") is None]
    changed = False
    for e in pend[:6]:                  # max 6 zapytań na odświeżenie
        tl = timeline(e["betradarId"], 120)
        if not tl: continue
        r = settle(e, tl)
        with PRO_LOCK:
            for k in ("next", "over", "final"):
                if r.get(k) and e.get(k) != r[k]: e[k] = r[k]; changed = True
            if e.get("next") is None and now_ts - e.get("ts", now_ts) > 6 * 3600 and r.get("final") is None:
                e["next"] = e.get("next") or "nierozliczony"; e["over"] = e.get("over") or "nierozliczony"; changed = True
    if changed:
        with PRO_LOCK: pro_save(PRO_LOG)

def pro(vip_res):
    watch = [dict(w) for w in (vip_res.get("watch") or [])]
    live_now = {e["eventId"]: e for e in get_list("live")}
    now = datetime.now(timezone.utc); now_ts = int(now.timestamp())
    types, near = [], []
    logged = {e["eventId"] for e in PRO_LOG}
    for w in watch:
        cur = live_now.get(w["eventId"])
        w["live"] = bool(cur)
        if not cur: continue
        side = "home" if w["side"] == "D" else "away"
        w["minutes"], w["score"] = cur.get("minutes"), cur.get("score")
        o = cur.get("o1") if side == "home" else cur.get("o2")
        w["odds_live"] = o if o is not None else "brak danych"
        det = live_details(w["betradarId"]); tl = timeline(w["betradarId"])
        minute = tl_minute(tl)
        if minute is None: minute = to_int(str(cur.get("minutes") or "").split("+")[0].replace("'", ""))
        h, a = tl_score(tl) if tl else tuple((to_int(x) or 0) for x in (cur.get("score") or "0:0").split(":")[:2])
        pr = pressure(tl, side, minute) if tl else {"fav_on": 0, "riv_on": 0, "fav_cor": 0, "riv_cor": 0, "from": None, "to": minute}
        ok, c = pro_trigger(det, pr, side, minute, "%d:%d" % (h, a))
        w.update(minute=minute, score="%d:%d" % (h, a), pressure=pr, live_checks=c, missing=sum(1 for x in c if not x["ok"]))
        if ok:
            tot = h + a
            w["markets"] = ["Następny gol: %s" % w["team"], "Powyżej %d,5 goli" % tot]
            types.append(w)
            if w["eventId"] not in logged:
                with PRO_LOCK:
                    PRO_LOG.append({"eventId": w["eventId"], "betradarId": w["betradarId"], "match": w["match"], "team": w["team"],
                                    "side_key": side, "minute": minute, "score_h": h, "score_a": a, "goals": tot,
                                    "over_line": "%d,5" % tot, "ts": now_ts, "time_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M"),
                                    "odds": o, "next": None, "over": None, "final": None})
                    del PRO_LOG[:-300]
                    pro_save(PRO_LOG)
                logged.add(w["eventId"])
        else:
            near.append({"match": w["match"], "team": w["team"], "minute": minute, "score": w["score"], "missing": w["missing"],
                         "failed": [x["name"] for x in c if not x["ok"]]})
    pro_settle_pending(now_ts)
    with PRO_LOCK: log = [dict(e) for e in PRO_LOG]
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "rules": PRO_RULES,
            "watch": watch, "watch_live": sum(1 for w in watch if w.get("live")), "types": types, "near": sorted(near, key=lambda x: x["missing"]),
            "message": None if types else "Brak typów w tej chwili",
            "record": record_stats(log), "log": list(reversed(log))[:40],
            "notes": ["Typy to spełnione kryteria, nie gwarancja.", ("Rejestr jest zapisywany w prywatnym gistcie." if persist_active() else "Rejestr zeruje się po restarcie serwera.")],
            "persistent": persist_active()}

PRO_STATE = {"res": None, "t": 0, "job": False}
PRO_TTL = 45

def _pro_worker(vres):
    try:
        res = pro(vres)
        with LOCK: PRO_STATE.update(res=res, t=time.time())
    except Exception as e:
        ERRORS["pro"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK: PRO_STATE["job"] = False

def get_pro():
    v = get_vip()
    with LOCK:
        res, t, job = PRO_STATE["res"], PRO_STATE["t"], PRO_STATE["job"]
        fresh = res is not None and time.time() - t < PRO_TTL
        if not fresh and not job and v.get("status") == "ready":
            PRO_STATE["job"] = True
            threading.Thread(target=_pro_worker, args=(v,), daemon=True).start()
    if res is not None:
        out = dict(res); out["status"] = "ready"; out["stale"] = not fresh; out["age_seconds"] = round(time.time() - t)
        out["errors"] = dict(ERRORS); return out
    return {"status": "computing", "stage": v.get("stage") or "pro", "progress": v.get("progress") or {}, "errors": dict(ERRORS)}

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
                "cache_entries": len(CACHE), "threads": threading.active_count(),
                "persistence": {"active": persist_active(), "ok": GIST_STATE["ok"], "error": GIST_STATE["err"]}}

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
        if u.path == "/api/vip":
            try: return self.send(200, json.dumps(get_vip(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/pro":
            try: return self.send(200, json.dumps(get_pro(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
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
