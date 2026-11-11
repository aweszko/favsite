#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ranking faworytów wg strzałów z ostatnich 5 meczów.
Tylko biblioteka standardowa Pythona 3.9+. Uruchom: python app.py -> http://localhost:8000
Źródła: Superbet PL (lista meczów, kursy 1X2), Sportradar gismo (statystyki). Bez kluczy i logowania."""
import json, os, sys, time, threading, urllib.request, urllib.parse, webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
import next_goal
import goal_overs
import other_overs
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", "8000"))
SB = "https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL"
G = "https://stats.fn.sportradar.com/betradar/en/Europe:Berlin/gismo/"
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TEAM_TTL = 6 * 3600
LIST_TTL = 55
LIST_LIVE_TTL = 30          # świeży wynik live (lista Superbet)
LIVE_STATE_MAX_AGE = 120    # nie pokazuj live, gdy stan starszy niż ok. 2 min
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

CACHE_DIR = os.environ.get("OVERS_CACHE_DIR") or os.path.join(
    os.environ.get("TMPDIR") or "/tmp", "triumfator-cache")
DISK_TTL_MIN = 3600

def _cache_path(key):
    import hashlib
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

def _disk_get(key, ttl):
    try:
        with open(_cache_path(key), "r", encoding="utf-8") as f:
            rec = json.load(f)
        ts, val = rec.get("t"), rec.get("v")
        if ts is not None and val is not None and time.time() - ts < ttl:
            return ts, val
    except Exception:
        return None
    return None

def _disk_put(key, val):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(key)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "v": val}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass

def cached(key, ttl, fn):
    """Pamięć + dysk przy TTL ≥ 1 h (lastx, timeline) — ta sama instancja po restarcie wątku."""
    with LOCK:
        v = CACHE.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    if ttl >= DISK_TTL_MIN:
        disk = _disk_get(key, ttl)
        if disk:
            with LOCK:
                CACHE[key] = disk
            return disk[1]
    val = fn()
    if val is None and v:             # błąd źródła -> ostatni cache
        return v[1]
    if val is not None:
        with LOCK:
            CACHE[key] = (time.time(), val)
        if ttl >= DISK_TTL_MIN:
            _disk_put(key, val)
    return val

def cache_fetched_at(key):
    with LOCK:
        v = CACHE.get(key)
    return v[0] if v else None


def cache_age(key):
    ts = cache_fetched_at(key)
    return None if ts is None else time.time() - ts


def state_stamp(fetched_at):
    """Czas Warszawy HH:MM:SS, z którego pochodzi stan oferty Superbet."""
    if not fetched_at:
        return None, None, None
    try:
        dt = datetime.fromtimestamp(float(fetched_at), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None, None, None
    warsaw = to_warsaw(dt).strftime("%H:%M:%S")
    age = time.time() - fetched_at
    return fetched_at, warsaw, age


def stamp_card(card, fetched_at, live=True):
    if not card:
        return card
    ts, warsaw, age = state_stamp(fetched_at)
    card["state_ts"] = ts
    card["state_warsaw"] = warsaw
    card["state_age_seconds"] = None if age is None else round(age)
    card["state_label"] = ("stan z %s" % warsaw) if warsaw else None
    return card


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
                    "periods": m.get("periods") or [],
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

RECENT_DAYS = 90       # mecz liczy się tylko, gdy nie starszy niż 90 dni (uts) - WSZĘDZIE

def is_recent(uts, now_ts=None, days=RECENT_DAYS):
    if not uts: return False
    now_ts = time.time() if now_ts is None else now_ts
    try: return now_ts - float(uts) <= days * 86400
    except (TypeError, ValueError): return False

def all_recent(games, now_ts=None, days=RECENT_DAYS):
    return bool(games) and all(is_recent(g.get("uts"), now_ts, days) for g in games)

class Games(list):
    """Lista meczów + flaga stale (część z ost. 5 meczów starsza niż 90 dni i odrzucona)."""
    stale = False

def recent_last5(matches, exclude_id, now_ts=None):
    """JEDNO miejsce: zakończone mecze (bez exclude_id), od najnowszego, tylko <=90 dni, max 5.
    Zwraca (mecze, stale) - stale=True gdy w surowych ost. 5 był mecz starszy niż 90 dni."""
    ms = [x for x in (matches or []) if (x.get("result") or {}).get("home") is not None and str(x.get("_id")) != str(exclude_id)]
    ms.sort(key=lambda x: (x.get("time") or {}).get("uts") or 0, reverse=True)
    stale = any(not is_recent((x.get("time") or {}).get("uts"), now_ts) for x in ms[:5])
    return [x for x in ms if is_recent((x.get("time") or {}).get("uts"), now_ts)][:5], stale

def team_last5(uid, exclude_br):
    def f():
        d = gismo("stats_team_lastx/%s/15" % uid)
        if d is None: return None
        if not isinstance(d, dict): d = {}
        ms, stale = recent_last5(d.get("matches"), exclude_br)
        dets = [match_details(x["_id"]) for x in ms]
        games = []
        for x, dd in zip(ms, dets):
            ish = (x["teams"]["home"].get("uid") == uid)
            i = 0 if ish else 1
            r = x["result"]
            gf, ga = (r["home"], r["away"]) if ish else (r["away"], r["home"])
            g = {"date": (x.get("time") or {}).get("date"), "uts": (x.get("time") or {}).get("uts"),
                 "opp": x["teams"]["away" if ish else "home"].get("name"), "ha": "D" if ish else "W",
                 "score": "%s:%s" % (gf, ga), "gf": to_int(gf), "ga": to_int(ga), "on": None, "tot": None, "cor": None,
                 "on_a": None, "tot_a": None}
            if dd:
                on, tot = dd.get("on"), dd.get("tot")
                # same zera przy niezerowym wyniku = brak danych
                if (gf or ga) and (on in (None, (0, 0))) and (tot in (None, (0, 0))):
                    on = tot = None
                if on: g["on"], g["on_a"] = on[i], on[1 - i]
                if tot: g["tot"], g["tot_a"] = tot[i], tot[1 - i]
                cor = dd.get("cor")
                if cor and g["on"] is not None: g["cor"] = cor[i]
            games.append(g)
        out = Games(games); out.stale = stale and len(games) < 5
        return out
    return cached("team3:%s:%s" % (uid, exclude_br), TEAM_TTL, f)

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
    if r.get("stale"): return False        # mecze starsze niż 90 dni -> za mało danych (nieaktualne)
    if fa >= 5 and ob >= 5: return True
    if sorted((min(fa, 5), min(ob, 5))) == [4, 5]:
        x, y = st.get("avg_on"), os_.get("avg_on")
        return x is not None and y is not None and x - y >= 1.5
    return False

def stats_lead(r):
    """Faworyt z kursu musi prowadzić w ost. 5: średnia celnych ORAZ średnia strzałów ogółem > rywala."""
    st, os_ = r.get("stats") or {}, r.get("opp_stats") or {}
    a1, b1, a2, b2 = st.get("avg_on"), os_.get("avg_on"), st.get("avg_tot"), os_.get("avg_tot")
    if None in (a1, b1, a2, b2): return False
    return a1 - b1 > 1e-9 and a2 - b2 > 1e-9

def build_ranking(rows, min_games=None, sort_key="avg_on", with_conflict=False):
    q = [r for r in rows if qualifies(r)]
    few = [r for r in rows if not qualifies(r)]
    ok = [r for r in q if stats_lead(r)]
    conflict = [r for r in q if not stats_lead(r)]
    def k(r):
        x, y = r["stats"].get("avg_on") or 0, ((r.get("opp_stats") or {}).get("avg_on") or 0)
        return (x - y, x)                     # głównie różnica średnich celnych na mecz
    ok.sort(key=k, reverse=True)
    for i, r in enumerate(ok, 1): r["rank"] = i
    conflict.sort(key=k)
    if with_conflict: return ok, few, conflict
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
           "opp_games": og, "opp_stats": ost, "diff": compare(st, ost),
           "stale": bool(getattr(games, "stale", False) or getattr(og, "stale", False))}
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
    ok, few, conflict = build_ranking(rows, min_games, sort_key, with_conflict=True)
    cnt = lambda k: sum(1 for kk, _ in res if kk == k)
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
            "params": {"min_games": min_games, "odds_min": odds_min, "odds_max": odds_max},
            "ranking": ok, "too_few": few, "conflict": conflict,
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
NG_GIST_FILE = "next_goal_log.json"
NG_LESSONS_FILE = "next_goal_lessons.json"
NG_LOG_PATH = os.environ.get("NG_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "next_goal_log.json"))
NG_LESSONS_PATH = os.environ.get("NG_LESSONS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "next_goal_lessons.json"))
NG_LOCK = threading.Lock()
GIST_MIN_INTERVAL = 30
GIST_STATE = {"last": 0.0, "dirty": False, "timer": None, "ok": None, "err": None}
GIST_SLOCK = threading.Lock()

def gist_cfg():
    t, g = os.environ.get("GIST_TOKEN", "").strip(), os.environ.get("GIST_ID", "").strip()
    return (t, g) if t and g else None

def persist_active():
    """Gist aktywny tylko gdy skonfigurowany i nie odrzucił uwierzytelnienia (401/403/404 -> wyłączony do restartu)."""
    return gist_cfg() is not None and not GIST_STATE.get("disabled")

def persist_working():
    return persist_active() and GIST_STATE["ok"] is not False

def _gist_http(method, gid, token, body=None, timeout=8):
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
    if getattr(e, "code", None) in (401, 403, 404): GIST_STATE["disabled"] = True   # nieważny token - pomijamy po cichu
    ERRORS.pop("gist", None)   # błąd gista nie jest błędem źródła danych - nie pokazujemy go użytkownikowi
    print("[gist] błąd: " + msg, file=sys.stderr)

def _gist_file_content(d, name, token):
    f = (d.get("files") or {}).get(name) or {}
    content = f.get("content")
    if f.get("truncated") and f.get("raw_url"):
        rq = urllib.request.Request(f["raw_url"], headers={"Authorization": "Bearer " + token, "User-Agent": "triumfator"})
        with urllib.request.urlopen(rq, timeout=8) as r: content = r.read().decode("utf-8")
    return content

def gist_load(file_name=None):
    cfg = gist_cfg()
    if not cfg or not persist_active(): return None
    name = file_name or GIST_FILE
    try:
        st, d = _gist_http("GET", cfg[1], cfg[0])
        content = _gist_file_content(d, name, cfg[0])
        log = json.loads(content or ("[]" if name != NG_LESSONS_FILE else "[]"))
        if name == NG_LESSONS_FILE:
            if not isinstance(log, list): raise ValueError("bad lessons")
        elif not isinstance(log, list):
            raise ValueError("bad log")
        GIST_STATE["ok"] = True
        return log
    except Exception as e:
        _gist_err(e); return None

def gist_push_now():
    cfg = gist_cfg()
    if not cfg or not persist_active(): return False
    with GIST_SLOCK:
        GIST_STATE["dirty"] = False; GIST_STATE["last"] = time.time(); GIST_STATE["timer"] = None
    try:
        with PRO_LOCK: pro_content = json.dumps(PRO_LOG, ensure_ascii=False)
        with NG_LOCK:
            ng_content = json.dumps(NG_LOG, ensure_ascii=False)
            lessons_content = json.dumps(NG_LESSONS, ensure_ascii=False)
        _gist_http("PATCH", cfg[1], cfg[0], {"files": {
            GIST_FILE: {"content": pro_content},
            NG_GIST_FILE: {"content": ng_content},
            NG_LESSONS_FILE: {"content": lessons_content},
        }})
        GIST_STATE["ok"], GIST_STATE["err"] = True, None
        ERRORS.pop("gist", None)
        return True
    except Exception as e:
        _gist_err(e); return False

def gist_schedule():
    """Zapis do gista: co najwyżej raz na ~30 s, zmiany łączone."""
    if not persist_active(): return
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

def ng_load():
    g = gist_load(NG_GIST_FILE)
    if g is not None: return g
    try:
        with open(NG_LOG_PATH, encoding="utf-8") as fh: return json.load(fh)
    except Exception: return []

def ng_lessons_load():
    g = gist_load(NG_LESSONS_FILE)
    if g is not None: return g
    try:
        with open(NG_LESSONS_PATH, encoding="utf-8") as fh: return json.load(fh)
    except Exception: return []

def ng_save(log=None, lessons=None):
    try:
        if log is not None:
            tmp = NG_LOG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh: json.dump(log, fh, ensure_ascii=False)
            os.replace(tmp, NG_LOG_PATH)
        if lessons is not None:
            tmp = NG_LESSONS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh: json.dump(lessons, fh, ensure_ascii=False)
            os.replace(tmp, NG_LESSONS_PATH)
    except Exception as e: ERRORS["ng_rejestr"] = "%s: %s" % (type(e).__name__, e)
    try: gist_schedule()
    except Exception as e: _gist_err(e)

NG_LOG = ng_load()
NG_LESSONS = ng_lessons_load()

def ng_attach(lst):
    """Picks+funnel+history+insights+learning na bazie listy dziurawych."""
    with NG_LOCK: lessons = list(NG_LESSONS); log = [dict(e) for e in NG_LOG]
    built = next_goal.build_next_goal(lst, lessons=lessons)
    insights = next_goal.aggregate_insights(log)
    # odśwież lekcje gdy próba wystarczająca
    if insights.get("lessons") is not None and not insights.get("sample_too_small"):
        with NG_LOCK:
            if insights["lessons"] != NG_LESSONS:
                NG_LESSONS[:] = insights["lessons"]
                ng_save(lessons=list(NG_LESSONS))
                lessons = list(NG_LESSONS)
                built = next_goal.build_next_goal(lst, lessons=lessons)
    pending_ids = {e.get("eventId") for e in log if e.get("status") == "pending"}
    for p in built["picks"]:
        p["already_tipped"] = p.get("eventId") in pending_ids
    return {
        "funnel": built["funnel"], "picks": built["picks"],
        "list": built.get("list") or [],
        "history": next_goal.history_view(log),
        "insights": {"texts": insights["texts"], "settled": insights["settled"], "wins": insights["wins"],
                     "losses": insights["losses"], "pending": insights["pending"], "hit_rate": insights["hit_rate"],
                     "buckets": insights["buckets"], "sample_too_small": insights["sample_too_small"]},
        "learning": {"lessons": lessons, "applied": bool(lessons),
                     "note": ("Korekty scoringu aktywne (nie sit)." if lessons else
                              ("Próba < %d – bez korekt wag." % next_goal.LEARN_MIN_SETTLED))},
        "persistent": persist_active(),
        "persist_working": persist_working(),
    }

def ng_settle_pending():
    now = datetime.now(timezone.utc); now_ts = int(now.timestamp())
    with NG_LOCK: pend = [e for e in NG_LOG if e.get("status") == "pending"]
    changed = False
    for e in pend[:8]:
        br = e.get("betradarId")
        if not br: continue
        try: tl = timeline(br, 120)
        except Exception: tl = None
        if not tl: continue
        r = next_goal.settle_tip(e, tl)
        if r.get("pending"): continue
        an = next_goal.analyze_tip(e, r)
        with NG_LOCK:
            e["status"] = r["status"]; e["result"] = r.get("result")
            e["goal_minute"] = r.get("goal_minute"); e["goal_side"] = r.get("goal_side")
            e["owngoal"] = r.get("owngoal"); e["final"] = r.get("final")
            e["no_goal"] = bool(r.get("no_goal"))
            e["settled_warsaw"] = to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S")
            e["cause"] = an["C_cause"]; e["analysis"] = an
            changed = True
        # timeout 6h bez final -> zostaw pending (timeline może wrócić)
    if changed:
        with NG_LOCK:
            insights = next_goal.aggregate_insights(NG_LOG)
            if not insights.get("sample_too_small"):
                NG_LESSONS[:] = insights.get("lessons") or []
            ng_save(log=list(NG_LOG), lessons=list(NG_LESSONS))

def ng_place_tip(pick):
    """Zapis typu ze snapshotem. Jeden aktywny (pending) na eventId."""
    if not pick or not pick.get("eventId"):
        return {"ok": False, "error": "brak eventId"}
    eid = pick["eventId"]
    now = datetime.now(timezone.utc)
    with NG_LOCK:
        for e in NG_LOG:
            if e.get("eventId") == eid and e.get("status") == "pending":
                return {"ok": False, "error": "ten mecz ma już aktywny typ", "entry": e}
        snap = next_goal.snapshot_from_pick(pick, to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"))
        snap["id"] = "%s-%s" % (eid, int(now.timestamp()))
        snap["ts"] = int(now.timestamp())
        NG_LOG.append(snap)
        del NG_LOG[:-300]
        ng_save(log=list(NG_LOG))
        return {"ok": True, "entry": snap}

def ng_get_tip(tip_id):
    with NG_LOCK: log = list(NG_LOG)
    for e in log:
        if str(e.get("id")) == str(tip_id) or str(e.get("eventId")) == str(tip_id):
            return next_goal.detail_view(e)
    return None

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
            "notes": ["Typy to spełnione kryteria, nie gwarancja.", ("Rejestr jest zapisywany w prywatnym gistcie." if persist_working() else "Rejestr zeruje się po restarcie serwera.")],
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


# ---------- Najsłabsi: ranking najsłabszych drużyn dnia (ost. 5, bez kursów) ----------
def _avg(xs, n):
    return round(sum(xs) / len(xs), 2) if len(xs) == n and n else None

def weak_stats(games):
    """Gole/strzały z ost. 5 meczów. Średnie strzałów tylko gdy dane z wszystkich 5 meczów (inaczej None)."""
    gs = [g for g in (games or []) if g.get("gf") is not None and g.get("ga") is not None][:5]
    n = len(gs)
    col = lambda k: [g[k] for g in gs if g.get(k) is not None]
    return {"games": n, "gf": sum(g["gf"] for g in gs), "ga": sum(g["ga"] for g in gs),
            "gf_avg": round(sum(g["gf"] for g in gs) / n, 2) if n else None,
            "ga_avg": round(sum(g["ga"] for g in gs) / n, 2) if n else None,
            "clean": sum(1 for g in gs if g["ga"] == 0), "blank": sum(1 for g in gs if g["gf"] == 0),
            "on_avg": _avg(col("on"), n), "tot_avg": _avg(col("tot"), n),
            "on_a_avg": _avg(col("on_a"), n), "tot_a_avg": _avg(col("tot_a"), n),
            "results": " ".join(g["score"] for g in gs)}

def build_worst(rows, mode="all", n=5):
    """rows: dict(team, ..., mode, ws). Zwraca sekcje A (traci najwięcej), B (strzela najmniej), C (obie najsłabsze tercje)."""
    rows = [r for r in rows if mode != "live" or r.get("mode") == "live"]
    ok = [r for r in rows if (r.get("ws") or {}).get("games", 0) >= n]
    few = len(rows) - len(ok)
    nz = lambda v, d: d if v is None else v
    a = sorted(ok, key=lambda r: (-r["ws"]["ga"], -nz(r["ws"]["on_a_avg"], -1), r["ws"]["clean"], r["team"] or ""))
    b = sorted(ok, key=lambda r: (r["ws"]["gf"], nz(r["ws"]["on_avg"], 99), -r["ws"]["blank"], r["team"] or ""))
    third = (len(ok) + 2) // 3
    ta = {id(r) for r in a[:third]}; tb = {id(r) for r in b[:third]}
    pos_a = {id(r): i for i, r in enumerate(a)}; pos_b = {id(r): i for i, r in enumerate(b)}
    c = sorted([r for r in ok if id(r) in ta and id(r) in tb], key=lambda r: (pos_a[id(r)] + pos_b[id(r)], r["ws"]["gf"] - r["ws"]["ga"]))
    return {"conceding": a, "scoring": b, "double": c[:15], "too_few": few, "teams": len(rows), "third": third}

def worst_pool():
    evs = list(get_list("live"))
    ids = {e["eventId"] for e in evs}
    today = to_warsaw(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    evs += [e for e in get_list("prematch") if e["eventId"] not in ids and e.get("day_warsaw") == today]
    return evs

def worst_event(ev):
    """Obie drużyny meczu -> wiersze. Te same klucze cache co ranking/VIP (match_info, team_last5) - bez podwójnego pobierania."""
    if not ev.get("br"): return ("nobr", [])
    mi = match_info(ev["br"])
    if not mi: return ("nostats", [])
    out = []
    for side, opp in (("home", "away"), ("away", "home")):
        uid = mi[side]["uid"]
        if not uid: continue
        games = team_last5(uid, ev["br"])
        out.append({"team": mi[side]["name"] or ev.get(side), "opponent": mi[opp]["name"] or ev.get(opp),
                    "side": "D" if side == "home" else "W", "uid": uid,
                    "tournament": mi.get("tournament") or ev.get("league"), "category": mi.get("category") or ev.get("country"),
                    "mode": ev["mode"], "start_warsaw": ev.get("start_warsaw"), "minutes": ev.get("minutes"), "score": ev.get("score"),
                    "eventId": ev["eventId"], "betradarId": ev["br"], "ws": weak_stats(games) if games is not None else {"games": 0}})
    return ("ok", out)

def worst(progress=None):
    evs = worst_pool()
    if progress is not None: progress.update(done=0, total=len(evs))
    def run(e):
        try: r = worst_event(e)
        except Exception as ex:
            ERRORS["najslabsi"] = "%s: %s" % (type(ex).__name__, ex); r = ("nostats", [])
        if progress is not None:
            with LOCK: progress["done"] += 1
        return r
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(run, evs))
    rows, seen = [], set()
    for _, rs in res:
        for r in rs:
            if (r["uid"], r["eventId"]) in seen: continue
            seen.add((r["uid"], r["eventId"])); rows.append(r)
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "rows": rows,
            "all": build_worst(rows, "all"), "live": build_worst(rows, "live"),
            "footer": {"matches": len(evs), "live_matches": sum(1 for e in evs if e["mode"] == "live"),
                       "without_betradar": sum(1 for k, _ in res if k == "nobr"), "without_stats": sum(1 for k, _ in res if k == "nostats")},
            "note": "Statystyki z ostatnich 5 meczów – forma, nie gwarancja"}


# ---------- Dziurawe obrony v2: tylko LIVE, temperatura z korektą siły rywala ----------
LEAGUE_AVG = 1.35      # fallback: typowa średnia goli drużyny na mecz
GA_CAP = 4             # cap goli straconych w meczu
TIER_W = 0.5           # waga meczu z rywalem z innej ligi
TEMP_MIN = 40
WORST_RULE = ("Dla każdego z 5 meczów: gole stracone obcinamy do 4 (cap), a potem odejmujemy średnią goli strzelanych przez tego rywala "
              "w jego ost. 5 meczach (bez meczu z tą drużyną): nadwyżka = max(0, min(stracone,4) − śr. rywala). Gdy brak danych o rywalu: "
              "nadwyżka = max(0, min(stracone,4) − 1,35 [średnia ligowa]). Mecz ma wagę ×0,5, gdy główna liga rywala (najczęstsza rozgrywka "
              "w jego ost. 15 meczach) różni się od głównej ligi drużyny (np. puchar z zespołem z wyższej ligi, puchary europejskie). "
              "Liczą się tylko drużyny, których wszystkie 5 meczów odbyło się w ost. 90 dni (inaczej: nieaktualne dane); średnia rywala tylko z jego meczów z ost. 90 dni (min. 3). "
              "Temperatura = 100 × [40% · min(1, śr. ważona nadwyżki / 2) + 30% · min(1, śr. celnych rywali / 7) + "
              "20% · udział meczów ze straconym golem + 10% · min(1, śr. strzałów rywali / 18)]; bez strzałów: (40%·G + 20%·C) / 60%.")

def lastx_raw(uid):
    def f():
        d = gismo("stats_team_lastx/%s/15" % uid)
        return d if isinstance(d, dict) else ({} if d is not None else None)
    return cached("lx:%s" % uid, TEAM_TTL, f)

def _done(d):
    return [x for x in ((d or {}).get("matches") or []) if (x.get("result") or {}).get("home") is not None]

def primary_league(d):
    c = {}
    for x in _done(d):
        k = x.get("_utid") or x.get("_tid")
        if k is not None: c[k] = c.get(k, 0) + 1
    return max(c, key=lambda k: (c[k], str(k))) if c else None

def opp_attack(uid, exclude_mid, now_ts=None):
    """Średnia goli strzelanych przez rywala w jego ost. 5 (bez wskazanego meczu, tylko mecze z ost. 90 dni; min. 3) + jego główna liga."""
    if not uid: return None
    d = lastx_raw(uid)
    if not d: return None
    ms, _ = recent_last5(d.get("matches"), exclude_mid, now_ts)
    gf = []
    for x in ms[:5]:
        ish = (x["teams"]["home"].get("uid") == uid)
        v = to_int(x["result"]["home"] if ish else x["result"]["away"])
        if v is not None: gf.append(v)
    if len(gf) < 3: return None
    return {"avg": round(sum(gf) / len(gf), 2), "n": len(gf), "league": primary_league(d)}

def team_games(uid, exclude_br):
    """Ost. 5 meczów z uid rywala, rozgrywką i strzałami (te same klucze det: co ranking)."""
    def f():
        d = lastx_raw(uid)
        if d is None: return None
        tours = d.get("tournaments") or {}
        ms, stale = recent_last5(d.get("matches"), exclude_br)
        out = []
        for x in ms[:5]:
            ish = (x["teams"]["home"].get("uid") == uid); i = 0 if ish else 1
            r = x["result"]; gf, ga = (r["home"], r["away"]) if ish else (r["away"], r["home"])
            o = x["teams"]["away" if ish else "home"]
            g = {"mid": x.get("_id"), "date": (x.get("time") or {}).get("date"), "uts": (x.get("time") or {}).get("uts"),
                 "opp": o.get("name"), "opp_uid": o.get("uid"), "ha": "D" if ish else "W",
                 "comp": (tours.get(str(x.get("_tid"))) or {}).get("name"), "league": x.get("_utid") or x.get("_tid"),
                 "score": "%s:%s" % (gf, ga), "gf": to_int(gf), "ga": to_int(ga), "on_a": None, "tot_a": None, "on_f": None, "tot_f": None}
            dd = match_details(x["_id"])
            if dd:
                on, tot = dd.get("on"), dd.get("tot")
                if (g["gf"] or g["ga"]) and on in (None, (0, 0)) and tot in (None, (0, 0)): on = tot = None
                if on: g["on_a"], g["on_f"] = on[1 - i], on[i]
                if tot: g["tot_a"], g["tot_f"] = tot[1 - i], tot[i]
            out.append(g)
        return {"games": out, "league": primary_league(d), "stale": stale}
    return cached("tg:%s:%s" % (uid, exclude_br), TEAM_TTL, f)

def opp_attack_summary(games, now_ts=None):
    """Atak rywala z jego ost. 5 meczów (tylko <=90 dni). None gdy brak świeżych meczów."""
    gs = [g for g in (games or []) if g.get("gf") is not None and is_recent(g.get("uts"), now_ts)][:5]
    if not gs: return None
    n = len(gs)
    def av(k):
        v = [g[k] for g in gs if g.get(k) is not None]
        return round(sum(v) / len(v), 1) if len(v) == n else None
    return {"n": n, "gf": sum(g["gf"] for g in gs), "gf_avg": round(sum(g["gf"] for g in gs) / n, 2),
            "on_avg": av("on_f"), "tot_avg": av("tot_f"), "results": " ".join(g["score"] for g in gs)}

TL_DONE_TTL = 24 * 3600
TL_MAX_TEAMS = 30      # limit zapytań o timeline na jedno liczenie (darmowy serwer)

def conceded_minutes(tl, ha):
    """Minuty goli straconych przez drużynę (ha 'D'/'W') z timeline zakończonego meczu. None gdy brak timeline."""
    if not tl or not isinstance(tl, dict): return None
    me = "home" if ha == "D" else "away"
    out = []
    for e in tl_events(tl):
        if e.get("type") != "goal": continue
        tm = e.get("team")
        if tm not in ("home", "away") or tm == me: continue
        t = to_int(e.get("time"))
        if t is None: continue
        out.append(max(1, t))
    return sorted(out)

def concede_profile(per_game):
    """per_game: lista list minut (None = brak timeline). Podsumowanie albo None."""
    ok = [m for m in per_game if m is not None]
    if not ok: return None
    mins = [x for m in ok for x in m]
    return {"games": len(ok), "missing": len(per_game) - len(ok), "total": len(mins), "minutes": mins,
            "h1": sum(1 for x in mins if x <= 45), "h2": sum(1 for x in mins if x > 45), "first30": sum(1 for x in mins if x <= 30)}

def adjust_games(games, team_league, opp_info):
    """opp_info: lista (dict|None) per mecz. Zwraca mecze z polami excess/weight/capped/notes."""
    out = []
    for g, oi in zip(games, opp_info):
        g = dict(g); ga = g["ga"] or 0
        cg = min(ga, GA_CAP); g["capped"] = ga > GA_CAP
        notes = []
        if g["capped"]: notes.append("cap: liczone %d z %d goli" % (GA_CAP, ga))
        if oi and oi.get("avg") is not None:
            g["opp_avg"] = oi["avg"]; g["excess"] = round(max(0.0, cg - oi["avg"]), 2)
        else:
            g["opp_avg"] = None; g["excess"] = round(max(0.0, cg - LEAGUE_AVG), 2)
            notes.append("brak danych o sile rywala")
        w = 1.0
        ol = (oi or {}).get("league")
        if team_league is not None and ol is not None and ol != team_league:
            w = TIER_W; notes.append("rywal z innej ligi – waga ×0,5")
        g["weight"] = w; g["notes"] = notes
        out.append(g)
    return out

def temperature(adj):
    """adj: 5 meczów po adjust_games. Zwraca dict temp/komponenty albo None gdy <5."""
    if len(adj) < 5: return None
    W = sum(g["weight"] for g in adj)
    G = sum(g["weight"] * g["excess"] for g in adj) / W
    C = sum(g["weight"] * (1 if (g["ga"] or 0) > 0 else 0) for g in adj) / W
    sg, sc = min(1.0, G / 2.0), C
    on = [g["on_a"] for g in adj]; tot = [g["tot_a"] for g in adj]
    shots = None not in on and None not in tot
    if shots:
        so, st = min(1.0, sum(on) / 5 / 7.0), min(1.0, sum(tot) / 5 / 18.0)
        t = 0.4 * sg + 0.3 * so + 0.2 * sc + 0.1 * st
    else:
        so = st = None; t = (0.4 * sg + 0.2 * sc) / 0.6
    temp = int(round(100 * t))
    return {"temp": temp, "label": temp_label(temp), "shots": shots, "g_excess_avg": round(G, 2), "conceded_share": round(C, 2),
            "on_a_avg": round(sum(on) / 5, 1) if shots else None, "tot_a_avg": round(sum(tot) / 5, 1) if shots else None}

def temp_label(t):
    return "bardzo dziurawa" if t >= 80 else "dziurawa" if t >= 60 else "przepuszcza"

WEAK_ATT = 0.8         # rywal strzelający < 0,8 gola/mecz: wynik mocno obniżony
CHANCE_RULE = ("Szansa na gola rywala (0–100) = 100 × [35% · temperatura obrony/100 + 35% · min(1, śr. goli rywala z ost. 5 meczów (≤90 dni) / 2) + "
               "10% · min(1, śr. celnych rywala / 6) (gdy brak: strzały / 15) + 20% · presja LIVE (udział rywala w celnych, rożnych i groźnych atakach "
               "w tym meczu, średnia z dostępnych)]. Brakujące składniki są pomijane, a wagi przeskalowane. Gdy rywal strzela < 0,8 gola/mecz: "
               "wynik × 0,5 i maks. 30. Gdy w tym meczu po 30+ min rywal ma ≤1 celny oraz 0 rożnych lub < 35% groźnych ataków: wynik × 0,5 i maks. 30. Brak danych o ataku rywala: przyjmujemy 1,35 [średnia ligowa] i maks. 50.")

def live_pressure(live):
    """Udział rywala (indeks 1) w celnych/rożnych/groźnych atakach LIVE. None gdy brak."""
    if not live: return None
    parts = {}
    for k in ("on", "cor", "da"):
        v = live.get(k)
        if not v or v[0] is None or v[1] is None: continue
        a, b = to_int(v[0]) or 0, to_int(v[1]) or 0
        parts[k] = round(b / (a + b), 2) if a + b > 0 else 0.5
    if not parts: return None
    return {"share": round(sum(parts.values()) / len(parts), 2), "parts": parts}

def live_dead(live, minutes):
    """Rywal praktycznie nie atakuje w tym meczu: po 30+ min ≤1 celny i (0 rożnych lub udział w groźnych atakach < 35%)."""
    m = to_int(str(minutes or "").split("+")[0].strip("'"))
    if not live or m is None or m < 30: return False
    on = live.get("on"); cor = live.get("cor"); da = live.get("da")
    if not on or on[1] is None or (to_int(on[1]) or 0) > 1: return False
    c0 = bool(cor) and cor[1] is not None and (to_int(cor[1]) or 0) == 0
    dlow = False
    if da and da[0] is not None and da[1] is not None:
        a, b = to_int(da[0]) or 0, to_int(da[1]) or 0
        dlow = a + b > 0 and b / (a + b) < 0.35
    return c0 or dlow

def goal_chance(t, opp_att, live, minutes=None):
    """Łączy temperaturę obrony z siłą ataku rywala (+ LIVE). Zwraca dict z wynikiem i składnikami."""
    if not t: return None
    comp, notes = [], []
    comp.append(("Temperatura obrony", 0.35, t["temp"] / 100.0))
    ga = (opp_att or {}).get("gf_avg")
    if ga is None:
        ga_used = LEAGUE_AVG; notes.append("brak danych o ataku rywala – przyjęto 1,35, maks. 50")
    else: ga_used = ga
    comp.append(("Gole rywala/mecz", 0.35, min(1.0, ga_used / 2.0)))
    on, tot = (opp_att or {}).get("on_avg"), (opp_att or {}).get("tot_avg")
    if on is not None: comp.append(("Celne rywala/mecz", 0.10, min(1.0, on / 6.0)))
    elif tot is not None: comp.append(("Strzały rywala/mecz", 0.10, min(1.0, tot / 15.0)))
    lp = live_pressure(live)
    if lp: comp.append(("Presja LIVE rywala", 0.20, lp["share"]))
    W = sum(w for _, w, _ in comp)
    v = 100 * sum(w * x for _, w, x in comp) / W
    if ga is None: v = min(v, 50)
    elif ga < WEAK_ATT:
        v = min(v * 0.5, 30); notes.append("rywal strzela < 0,8 gola/mecz – wynik ×0,5, maks. 30")
    if live_dead(live, minutes):
        v = min(v * 0.5, 30); notes.append("LIVE: rywal po 30+ min praktycznie nie atakuje (≤1 celny, 0 rożnych lub mało groźnych ataków) – wynik ×0,5, maks. 30")
    sc = int(round(v))
    return {"score": sc, "label": "wysoka" if sc >= 65 else "średnia" if sc >= 45 else "niska", "opp_gf_avg": ga,
            "live_share": lp["share"] if lp else None, "notes": notes,
            "parts": [{"name": n, "weight": round(w / W * 100), "value": int(round(x * 100))} for n, w, x in comp]}

def holes_rank(rows):
    """Sort: szansa na gola rywala malejąco, potem temperatura, gole stracone, nazwa. Temperatura <TEMP_MIN wypada."""
    ok = [r for r in rows if r.get("t") and r["t"]["temp"] >= TEMP_MIN]
    ok.sort(key=lambda r: (-((r.get("chance") or {}).get("score") or 0), -r["t"]["temp"], -r["ga_raw"], r["team"] or ""))
    for i, r in enumerate(ok, 1): r["rank"] = i
    return ok

EARLY_MIN = 25
BAL_DIFF = 10
SECTIONS = ("main", "early", "balanced")

def minute_int(m):
    """'67' / '45+2' / "67'" -> int; przerwa = 45; nieznane -> None."""
    if m is None: return None
    s = str(m).strip().lower()
    if s in ("ht", "przerwa", "half time", "halftime"): return 45
    d = ""
    for ch in s:
        if ch.isdigit(): d += ch
        elif d: break
    return int(d) if d else None

def holes_sections(lst):
    """Sekcje: main (wyraźny kierunek, ≥25'), early (<25'), balanced (obie drużyny meczu na liście
    lub szanse obu kierunków różnią się < 10 pkt). Wewnątrz sekcji sortowanie jak dotąd; rank od nowa."""
    ev = {}
    for r in lst: ev.setdefault(r.get("eventId"), []).append(r)
    for r in lst:
        both = len(ev[r.get("eventId")]) > 1
        me = (r.get("chance") or {}).get("score") or 0
        oc = r.get("opp_chance")
        close = oc is not None and abs(me - oc) < BAL_DIFF
        r["balanced"] = both or close
        r["balanced_why"] = (("obie drużyny tego meczu są na liście dziurawych obron" if both else
                              "szansa na gola w drugą stronę jest podobna (%d vs %d, różnica < %d pkt)" % (me, oc, BAL_DIFF))
                             if r["balanced"] else None)
        mi = minute_int(r.get("minutes"))
        r["early"] = mi is not None and mi < EARLY_MIN
        r["section"] = "balanced" if r["balanced"] else "early" if r["early"] else "main"
    lst.sort(key=lambda r: (SECTIONS.index(r["section"]), -((r.get("chance") or {}).get("score") or 0),
                            -((r.get("t") or {}).get("temp") or 0), -(r.get("ga_raw") or 0), r.get("team") or ""))
    for i, r in enumerate(lst, 1): r["rank"] = i
    return lst

def section_counts(lst):
    c = {k: 0 for k in SECTIONS}
    for r in lst: c[r.get("section", "main")] += 1
    ev = {k: len({r.get("eventId") for r in lst if r.get("section") == k}) for k in SECTIONS}
    return {"teams": c, "matches": ev}

def holes_event(ev, opp_cache):
    if not ev.get("br"): return ("nobr", [])
    mi = match_info(ev["br"])
    if not mi: return ("nostats", [])
    live = None
    try: live = live_details(ev["br"])
    except Exception: live = None
    out = []
    for side, opp in (("home", "away"), ("away", "home")):
        uid = mi[side]["uid"]
        if not uid: continue
        tg = team_games(uid, ev["br"])
        games = (tg or {}).get("games") or []
        row = {"team": mi[side]["name"] or ev.get(side), "opponent": mi[opp]["name"] or ev.get(opp), "side": "D" if side == "home" else "W",
               "uid": uid, "tournament": mi.get("tournament") or ev.get("league"), "category": mi.get("category") or ev.get("country"),
               "minutes": ev.get("minutes"), "score": ev.get("score"), "eventId": ev["eventId"], "betradarId": ev.get("br"),
               "games_n": len(games), "t": None,
               "stale": bool((tg or {}).get("stale")) and len(games) < 5, "_g": games, "opp_uid": mi[opp]["uid"]}
        if len(games) >= 5 and all(g["ga"] is not None and g["gf"] is not None for g in games):
            infos = []
            for g in games:
                k = (g["opp_uid"], g["mid"])
                if k not in opp_cache:
                    try: opp_cache[k] = opp_attack(g["opp_uid"], g["mid"])
                    except Exception: opp_cache[k] = None
                infos.append(opp_cache[k])
            adj = adjust_games(games, tg.get("league"), infos)
            row.update(games=adj, ga_raw=sum(g["ga"] for g in adj), gf_raw=sum(g["gf"] for g in adj),
                       clean=sum(1 for g in adj if g["ga"] == 0), two_plus=sum(1 for g in adj if g["ga"] >= 2),
                       t=temperature(adj))
            if live:
                i = 0 if side == "home" else 1
                row["live"] = {k: (None if not live.get(k) else [live[k][i], live[k][1 - i]]) for k in ("on", "cor", "da")}
        out.append(row)
    return ("ok", out)

def holes(progress=None):
    evs = list(get_list("live"))
    if progress is not None: progress.update(done=0, total=len(evs))
    opp_cache = {}
    def run(e):
        try: r = holes_event(e, opp_cache)
        except Exception as ex:
            ERRORS["dziurawe"] = "%s: %s" % (type(ex).__name__, ex); r = ("nostats", [])
        if progress is not None:
            with LOCK: progress["done"] += 1
        return r
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(run, evs))
    rows, seen = [], set()
    for _, rs in res:
        for r in rs:
            if (r["uid"], r["eventId"]) in seen: continue
            seen.add((r["uid"], r["eventId"])); rows.append(r)
    by_ev = {}
    for r in rows: by_ev.setdefault(r["eventId"], []).append(r)
    for r in rows:
        o = [x for x in by_ev[r["eventId"]] if x is not r]
        r["opp_att"] = opp_attack_summary(o[0]["_g"]) if o else None
        r["chance"] = goal_chance(r.get("t"), r["opp_att"], r.get("live"), r.get("minutes"))
    for r in rows:
        o = [x for x in by_ev[r["eventId"]] if x is not r]
        r["opp_chance"] = (o[0].get("chance") or {}).get("score") if o and o[0].get("t") else None
    full = [r for r in rows if r.get("t")]
    lst = holes_sections(holes_rank(full))
    def prof(r):
        try:
            pg = [conceded_minutes(cached("tlf:%s" % g["mid"], TL_DONE_TTL, lambda m=g["mid"]: gismo("match_timeline/%s" % m)), g["ha"])
                  for g in r["games"]]
            r["concede"] = concede_profile(pg)
        except Exception as ex:
            ERRORS["dziurawe_tl"] = "%s: %s" % (type(ex).__name__, ex); r["concede"] = None
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(prof, lst[:TL_MAX_TEAMS]))
    for r in rows: r.pop("_g", None)
    now = datetime.now(timezone.utc)
    return {"generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"), "list": lst, "sections": section_counts(lst),
            "counts": {"teams": len(rows), "listed": len(lst), "too_few": sum(1 for r in rows if not r.get("t") and not r.get("stale")),
                       "stale": sum(1 for r in rows if r.get("stale")), "recent_days": RECENT_DAYS, "below": len(full) - len(lst),
                       "no_shots": sum(1 for r in lst if not r["t"]["shots"])},
            "footer": {"live_matches": len(evs), "without_betradar": sum(1 for k, _ in res if k == "nobr"),
                       "without_stats": sum(1 for k, _ in res if k == "nostats")},
            "rule": WORST_RULE, "chance_rule": CHANCE_RULE, "note": "Statystyki z ostatnich 5 meczów – forma, nie gwarancja",
            "next_goal": ng_attach(lst)}

WORST_TTL = 120
WORST_STATE = {"res": None, "t": 0, "job": None}

def _worst_worker(prog):
    try:
        res = holes(prog)
        with LOCK: WORST_STATE.update(res=res, t=time.time()); ERRORS.pop("dziurawe_job", None)
    except Exception as e:
        ERRORS["najslabsi"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK:
            if WORST_STATE["job"] is not None and WORST_STATE["job"]["progress"] is prog: WORST_STATE["job"] = None

def _slim(r):
    return {k: r[k] for k in ("team", "opponent", "side", "tournament", "category", "mode", "start_warsaw", "minutes", "score", "eventId", "ws")}

WORST_JOB_MAX = 600   # zawieszone liczenie (>10 min) nie blokuje kolejnego

def worst_overlay(res, live_evs, now_utc=None):
    """Nakłada świeże minuty/wynik z listy LIVE (cache 55 s, tanie) na ranking; usuwa zakończone mecze."""
    now_utc = now_utc or datetime.now(timezone.utc)
    out = dict(res)
    cur = {e["eventId"]: e for e in (live_evs or [])}
    if cur:
        lst = []
        for r in res.get("list") or []:
            e = cur.get(r.get("eventId"))
            if e is None: continue
            r = dict(r); r["minutes"], r["score"] = e.get("minutes"), e.get("score"); lst.append(r)
        lst = holes_sections(lst)
        out["list"] = lst; out["sections"] = section_counts(lst)
        out["next_goal"] = ng_attach(lst)
        out["live_warsaw"] = to_warsaw(now_utc).strftime("%Y-%m-%d %H:%M:%S")
        ng_settle_pending()
    elif res.get("list") is not None:
        out["next_goal"] = ng_attach(res.get("list"))
        ng_settle_pending()
    return out

def get_worst(mode="all"):
    mode = "live" if mode == "live" else "all"
    with LOCK:
        res, t, job = WORST_STATE["res"], WORST_STATE["t"], WORST_STATE["job"]
        fresh = res is not None and time.time() - t < WORST_TTL
        if job is not None and time.time() - job["started"] > WORST_JOB_MAX:
            ERRORS["dziurawe_job"] = "liczenie > %d s – restart" % WORST_JOB_MAX; job = WORST_STATE["job"] = None
        if not fresh and job is None:
            job = WORST_STATE["job"] = {"progress": {"done": 0, "total": 0}, "started": time.time()}
            threading.Thread(target=_worst_worker, args=(job["progress"],), daemon=True).start()
    if res is not None:
        try: live_evs = get_list("live")
        except Exception: live_evs = None
        out = worst_overlay(res, live_evs); out.update(status="ready", stale=not fresh, age_seconds=round(time.time() - t), errors=dict(ERRORS))
        if job: out["progress"] = dict(job["progress"])
        return out
    return {"status": "computing", "progress": dict(job["progress"]), "elapsed_seconds": round(time.time() - job["started"]),
            "errors": dict(ERRORS)}


# ---------- Overy goli (minuty z ost. ~10 meczów, liczone w tle) ----------
OVERS_TTL = 600
OVERS_JOB_MAX = 1800          # twardy strop tylko przy braku pulsu
OVERS_STALL_SEC = 240         # restart wyłącznie, gdy nic nie przybyło
OVERS_MATCH_MAX = 40
OVERS_HISTORY = 10            # wystarczy przy progu n≥8; mniej HTTP
OVERS_WORKERS = 2
OVERS_STATE = {"res": None, "t": 0, "job": None}

def _tl_goals(mid):
    """Timeline zakończonego meczu (cache 7 dni — wynik się nie zmienia)."""
    def f():
        d = gismo("match_timeline/%s" % mid)
        return d if isinstance(d, dict) else ({} if d is not None else None)
    return cached("tlgoal:%s" % mid, 7 * 24 * 3600, f)


def _pair_score(d):
    if not d or d.get("home") is None or d.get("away") is None:
        return None
    h, a = to_int(d.get("home")), to_int(d.get("away"))
    if h is None or a is None:
        return None
    return h, a


def team_goal_views(uid, exclude_br=None, history=None):
    """Ost. N meczów drużyny z minutami goli (albo choćby p1/ft)."""
    n_hist = history if history is not None else OVERS_HISTORY
    def f():
        d = lastx_raw(uid)
        if not d:
            return None
        ms = []
        for x in _done(d)[:n_hist]:
            mid = x.get("_id")
            if exclude_br and str(mid) == str(exclude_br):
                continue
            th, ta = (x.get("teams") or {}).get("home") or {}, (x.get("teams") or {}).get("away") or {}
            is_home = str(th.get("uid")) == str(uid)
            per = x.get("periods") or {}
            ft = _pair_score(per.get("ft") or x.get("result"))
            p1 = _pair_score(per.get("p1"))
            tl = _tl_goals(mid)
            goals, ft_g = [], None
            tl_ok = False
            if tl:
                goals, ft_g = goal_overs.extract_goals(tl.get("events") or [])
                tl_ok = True
                if ft and goals and (ft[0] + ft[1]) != len(goals):
                    tl_ok = False
            mv = goal_overs.match_view(
                uid, is_home, goals if tl_ok else [], ft=ft or ft_g, p1=p1,
                timeline_ok=tl_ok, mid=mid,
                opp_uid=(ta if is_home else th).get("uid"),
                uts=(x.get("time") or {}).get("uts"))
            if not tl_ok and p1 and ft:
                mv["gf_ht"] = p1[0] if is_home else p1[1]
                mv["ga_ht"] = p1[1] if is_home else p1[0]
                mv["tot_1h"] = p1[0] + p1[1]
                mv["tot_2h"] = (ft[0] + ft[1]) - (p1[0] + p1[1])
                mv["any_2h"] = mv["tot_2h"] >= 1
                mv["gf_1h"] = mv["gf_ht"]
                mv["gf_2h"] = (mv["gf"] or 0) - mv["gf_ht"]
                mv["ga_1h"] = mv["ga_ht"]
                mv["ga_2h"] = (mv["ga"] or 0) - mv["ga_ht"]
                mv["scored_2h"] = mv["gf_2h"] >= 1
                mv["ht_00"] = mv["tot_1h"] == 0
                mv["ht_le1"] = mv["tot_1h"] <= 1
                mv["any_1h"] = mv["tot_1h"] >= 1
                mv["first_1h_known"] = bool(mv.get("timeline_ok")) or mv["tot_1h"] == 0
                mv["first_2h_known"] = bool(mv.get("timeline_ok")) or mv["tot_2h"] == 0
            ms.append(mv)
        return ms
    return cached("gviews:%s:%s:%s" % (uid, exclude_br or "", n_hist), TEAM_TTL, f)


def _overs_window(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    today = to_warsaw(now_utc).strftime("%Y-%m-%d")
    tomorrow = (to_warsaw(now_utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    evs = [e for e in (get_list("prematch") or [])
           if e.get("day_warsaw") in (today, tomorrow) and e.get("br")]
    evs.sort(key=lambda e: e.get("ts") or 0)
    return evs[:OVERS_MATCH_MAX], today, tomorrow


def _seed_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "overs_seed.json")


def _seed_still_relevant(p, now_ts=None):
    now_ts = now_ts if now_ts is not None else time.time()
    ts = p.get("ts")
    if ts:
        try:
            return float(ts) >= now_ts - 3 * 3600  # jeszcze trwa albo wkrótce
        except Exception:
            pass
    day, hm = p.get("day_warsaw") or "", p.get("start_warsaw") or ""
    if day and hm:
        try:
            local = datetime.strptime(day + " " + hm, "%Y-%m-%d %H:%M")
            utc_guess = local.replace(tzinfo=timezone.utc) - warsaw_offset(datetime.now(timezone.utc))
            return utc_guess.timestamp() >= now_ts - 3 * 3600
        except Exception:
            return True
    return True


def _seed_payload(now_ts=None):
    try:
        with open(_seed_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None
    raw_picks = [p for p in (raw.get("picks") or []) if _seed_still_relevant(p, now_ts)]
    picks_1h = [p for p in (raw.get("picks_1h") or []) if _seed_still_relevant(p, now_ts)]
    # Zakładka przedmeczowa: tylko over w 1. połowie (zrzut ma też stare typy 2H).
    one_h = [goal_overs.sanitize_team_names(dict(p)) for p in picks_1h if goal_overs._is_pre_1h_line(p)]
    if not one_h:
        one_h = [goal_overs.sanitize_team_names(dict(p)) for p in raw_picks if goal_overs._is_pre_1h_line(p)]
    if not one_h:
        return None
    for i, p in enumerate(one_h, 1):
        p["rank"] = i
    out = dict(raw)
    out.update(picks=one_h, n_picks=len(one_h), picks_1h=one_h, n_picks_1h=len(one_h),
               source="seed")
    note = out.get("note") or ""
    if "1. połow" not in note:
        out["note"] = ("Tylko gole w 1. połowie (over 0.5, 1.5 i 2.5). "
                       + note).strip()
    return out


def _ensure_seed():
    """Natychmiastowe karty z wbudowanego zrzutu, zanim doliczy się na żywo."""
    with LOCK:
        if OVERS_STATE["res"] is not None:
            return OVERS_STATE["res"]
    seed = _seed_payload()
    if not seed:
        return None
    with LOCK:
        if OVERS_STATE["res"] is None:
            OVERS_STATE.update(res=seed, t=0)  # t=0 -> zawsze stale, odśwież w tle
    return seed


def _merge_list(live_cards, processed_ids, seed_picks=None):
    live = [goal_overs.public_card(c) for c in goal_overs.rank_picks(live_cards, limit=goal_overs.MAX_PICKS)]
    live_ids = {p.get("eventId") for p in live}
    extra = []
    for p in seed_picks or []:
        eid = p.get("eventId")
        if eid in live_ids or eid in processed_ids:
            continue
        if _seed_still_relevant(p):
            extra.append(dict(p))
    extra.sort(key=lambda c: (-(c.get("chance") or 0), -(c.get("n") or 0)))
    room = max(0, goal_overs.MAX_PICKS - len(live))
    combined = live + extra[:room]
    for i, p in enumerate(combined, 1):
        p["rank"] = i
    used_extra = extra[:room]
    if live and not used_extra:
        source = "live"
    elif live and used_extra:
        source = "mixed"
    elif live:
        source = "live"
    else:
        source = "seed"
    return combined, source


def _merge_picks(live_cards, processed_ids, seed_picks=None, live_1h=None, seed_1h=None):
    picks, source = _merge_list(live_cards, processed_ids, seed_picks)
    picks_1h, source_1h = _merge_list(live_1h or [], processed_ids, seed_1h)
    if source == "live" and source_1h == "mixed":
        source = "mixed"
    elif source == "seed" and source_1h == "mixed":
        source = "mixed"
    elif source == "seed" and source_1h == "live" and picks:
        source = "mixed"
    return picks, picks_1h, source


def _rank_pre_unique(cards, require_odds=True):
    if require_odds:
        rows = goal_overs.rank_pre_lines(cards)
        if rows:
            return rows
    # bez kursów (albo żaden nie ma kursu ≥ 1.30): jeden mecz raz, bez over 0.5 w meczu
    return goal_overs.rank_unique_by_chance(cards, pre=True)


def _publish_overs(cards, evs, today, tomorrow, skipped, processed_ids, partial, seed_picks=None, cards_1h=None, seed_1h=None):
    if not partial:
        _attach_pre_line_odds(cards)
    ranked = _rank_pre_unique(cards, require_odds=not partial)
    # merge z seedem tylko na czas pierwszego liczenia, bez duplikowania eventId
    ranked_ids = {r.get("eventId") for r in ranked}
    extras = []
    if partial:
        seen_ex = set()
        for p in list(seed_1h or []) + list(seed_picks or []):
            if not p:
                continue
            eid = p.get("eventId")
            if eid in ranked_ids or eid in processed_ids or eid in seen_ex:
                continue
            seen_ex.add(eid)
            if _seed_still_relevant(p) and goal_overs._is_pre_1h_line(p):
                extras.append(goal_overs.sanitize_team_names(dict(p)))
        extras.sort(key=lambda c: (-(c.get("chance") or 0), -(c.get("n") or 0)))
    room = max(0, goal_overs.MAX_PICKS - len(ranked))
    picks = [goal_overs.public_line_card(r) for r in ranked] + extras[:room]
    for i, p in enumerate(picks, 1):
        p["rank"] = i
    # 1H osobno zostaje dla zgodności seed/testów, ale UI korzysta z jednego rankingu
    picks_1h, source_1h_unused = _merge_list(cards_1h or [], processed_ids, seed_1h)
    if not partial:
        source = "live"
    elif ranked and extras[:room]:
        source = "mixed"
    elif ranked:
        source = "live"
    else:
        source = "seed" if (seed_picks or extras) else "live"
    pub_matches = [goal_overs.public_match_card(c) for c in cards if c]
    now = datetime.now(timezone.utc)
    res = {
        "generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"),
        "today_warsaw": today, "tomorrow_warsaw": tomorrow,
        "picks": picks, "n_picks": len(picks),
        "picks_1h": picks_1h, "n_picks_1h": len(picks_1h),
        "matches": pub_matches, "n_matches": len(pub_matches),
        "source": source, "partial": partial,
        "footer": {"matches": len(evs), "checked": len(processed_ids),
                   "without_stats": skipped.get("nostats", 0),
                   "too_weak": skipped.get("thin", 0), "min_n": goal_overs.MIN_N,
                   "p_min": goal_overs.LIVE_RANK_P_MIN,
                   "edge_plus": goal_overs.EDGE_PLUS},
        "rule": goal_overs.CHANCE_RULE,
        "note": "Tylko gole w 1. połowie (over 0.5, 1.5 i 2.5). Każdy mecz raz. Typuję przy kursie Superbet ok. 1,30 albo wyższym. Szansa na wygraną zakładu jest przy każdym typie. Opłaca się, gdy szansa jest wyraźnie wyższa niż to, co sugeruje kurs. Forma, nie gwarancja. Nic nie jest stawiane.",
    }
    with LOCK:
        OVERS_STATE.update(res=res, t=time.time() if not partial else OVERS_STATE["t"] or time.time())
        if not partial:
            ERRORS.pop("overy_job", None)
    return res


def _overs_one(ev):
    try:
        mi = match_info(ev["br"])
        if not mi:
            return ev, "nostats", None, None
        hu = (mi.get("home") or {}).get("uid")
        au = (mi.get("away") or {}).get("uid")
        if not hu or not au:
            return ev, "nostats", None, None
        hv = team_goal_views(hu, ev.get("br"))
        av = team_goal_views(au, ev.get("br"))
        if not hv or not av:
            return ev, "nostats", None, None
        hp = goal_overs.team_profile(str(hu), hv, name=ev.get("home") or (mi["home"] or {}).get("name"))
        ap = goal_overs.team_profile(str(au), av, name=ev.get("away") or (mi["away"] or {}).get("name"))
        meta = dict(eventId=ev.get("eventId"), start_warsaw=ev.get("start_warsaw"),
                    day_warsaw=ev.get("day_warsaw"), ts=ev.get("ts"),
                    home=ev.get("home"), away=ev.get("away"),
                    home_name=ev.get("home"), away_name=ev.get("away"),
                    tournament=ev.get("league") or mi.get("tournament"),
                    category=ev.get("country") or mi.get("category"),
                    mode=ev.get("mode") or "prematch", minutes=ev.get("minutes"),
                    score=ev.get("score"), br=ev.get("br"),
                    start_pl=("%s %s" % (ev.get("day_warsaw") or "", ev.get("start_warsaw") or "")).strip())
        card = goal_overs.pre_match_card(hp, ap, meta)
        card_1h = goal_overs.pick_1h_for_match(hp, ap, meta)
        return ev, "ok", card, card_1h
    except Exception as ex:
        ERRORS["overy"] = "%s: %s" % (type(ex).__name__, ex)
        return ev, "nostats", None, None


def build_overs(progress=None, job=None, seed_picks=None, seed_1h=None):
    evs, today, tomorrow = _overs_window()
    if progress is not None:
        progress.update(done=0, total=len(evs))
    cards, cards_1h, skipped, processed = [], [], {"nobr": 0, "nostats": 0, "thin": 0}, set()

    def touch(ev, st, card, card_1h=None):
        processed.add(ev.get("eventId"))
        if st != "ok":
            skipped[st] = skipped.get(st, 0) + 1
        else:
            if card:
                cards.append(card)
            else:
                skipped["thin"] += 1
            if card_1h:
                cards_1h.append(card_1h)
        if progress is not None:
            with LOCK:
                progress["done"] = len(processed)
                if job is not None:
                    job["beat"] = time.time()
        _publish_overs(cards, evs, today, tomorrow, skipped, processed, partial=True,
                       seed_picks=seed_picks, cards_1h=cards_1h, seed_1h=seed_1h)

    if evs:
        with ThreadPoolExecutor(OVERS_WORKERS) as pool:
            futs = [pool.submit(_overs_one, ev) for ev in evs]
            for fut in as_completed(futs):
                if job and job.get("cancel"):
                    break
                ev, st, card, *rest = fut.result()
                card_1h = rest[0] if rest else None
                touch(ev, st, card, card_1h)
    return _publish_overs(cards, evs, today, tomorrow, skipped, processed, partial=False,
                          seed_picks=None, cards_1h=cards_1h, seed_1h=None)


def _overs_worker(job):
    prog = job["progress"]
    try:
        cur = OVERS_STATE.get("res") or {}
        use_seed = cur.get("source") in ("seed", "mixed")
        seed = cur.get("picks") if use_seed else None
        seed_1h = cur.get("picks_1h") if use_seed else None
        if seed is None and seed_1h is None:
            s = _seed_payload()
            seed = (s or {}).get("picks")
            seed_1h = (s or {}).get("picks_1h")
        build_overs(prog, job=job, seed_picks=seed, seed_1h=seed_1h)
        with LOCK:
            ERRORS.pop("overy_job", None)
    except Exception as e:
        ERRORS["overy"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK:
            if OVERS_STATE["job"] is job:
                OVERS_STATE["job"] = None


def _job_stalled(job):
    """Czy wątek nie daje pulsu (nie: czy batch po prostu długo liczy)."""
    if not job:
        return False
    beat = job.get("beat") or job.get("started") or 0
    return (time.time() - beat) >= OVERS_STALL_SEC


def get_overs():
    """Zwraca seed lub już policzone karty od razu; liczenie tylko w tle, bez drugiej rundy w trakcie."""
    _ensure_seed()
    with LOCK:
        res, t, job = OVERS_STATE["res"], OVERS_STATE["t"], OVERS_STATE["job"]
        live = res is not None and res.get("source") == "live" and not res.get("partial")
        fresh = bool(live and t and time.time() - t < OVERS_TTL)
        # Nigdy nie odpalaj drugiego workera, gdy pierwszy jeszcze żyje — to blokowało semafor HTTP.
        if not fresh and job is None:
            job = OVERS_STATE["job"] = {
                "progress": {"done": 0, "total": 0},
                "started": time.time(), "beat": time.time(), "cancel": False,
            }
            threading.Thread(target=_overs_worker, args=(job,), daemon=True).start()
    if res is not None:
        out = dict(res)
        # Karty (seed/częściowe) od razu; status=computing tylko po to, by UI częściej odpytywał.
        status = "computing" if job and not fresh else "ready"
        out.update(status=status, stale=not fresh,
                   age_seconds=round(time.time() - t) if t else None,
                   errors=dict(ERRORS))
        if job:
            out["progress"] = dict(job["progress"])
            out["elapsed_seconds"] = round(time.time() - job["started"])
        return out
    return {"status": "computing", "progress": dict(job["progress"]) if job else {"done": 0, "total": 0},
            "elapsed_seconds": round(time.time() - job["started"]) if job else 0, "errors": dict(ERRORS),
            "picks": []}



# ---------- kursy over/BTTS z Superbet (tylko dla czołówki przedmeczowej) ----------
_OVER_ODDS_WANT = {
    ("liczba goli", "powyżej 1.5"): "over15",
    ("liczba goli", "powyżej 2.5"): "over25",
    ("1.połowa - liczba goli", "powyżej 0.5"): "over05_1h",
    ("1. połowa - liczba goli", "powyżej 0.5"): "over05_1h",
    ("2.połowa - liczba goli", "powyżej 0.5"): "over05_2h",
    ("2. połowa - liczba goli", "powyżej 0.5"): "over05_2h",
    ("obie drużyny strzelą", "tak"): "btts",
}

def parse_over_odds(odds):
    """Wyciąga kursy prostych rynków over/BTTS; pomija kombinacje i nieaktywne."""
    out = {}
    for o in odds or []:
        st = (o.get("status") or "").lower()
        if st in ("block", "blocked", "suspended", "deactivated", "inactive"):
            continue
        mn = (o.get("marketName") or "").strip().lower()
        on = (o.get("name") or "").strip().lower()
        if ";" in mn or " & " in mn or " lub " in mn:
            continue
        key = _OVER_ODDS_WANT.get((mn, on))
        if not key:
            continue
        try:
            price = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1:
            continue
        out[key] = price
    return out


def _over_line_from_name(on):
    on = (on or "").strip().lower().replace(",", ".")
    for prefix in ("powyżej ", "powyzej ", "poniżej ", "ponizej ", "over ", "under ", "below "):
        if on.startswith(prefix):
            rest = on[len(prefix):].strip()
            tok = rest.split()[0] if rest else ""
            try:
                return float(tok)
            except (TypeError, ValueError):
                return None
    return None


def _norm_market_name(mn):
    return " ".join((mn or "").strip().lower().split())


def parse_live_goal_odds(odds):
    """Kursy prostych linii over liczby goli (mecz / 1H / 2H). Pomija kombinacje i zawieszone."""
    out = {}
    for o in odds or []:
        st = (o.get("status") or "").lower()
        if st in ("block", "blocked", "suspended", "deactivated", "inactive"):
            continue
        mn = _norm_market_name(o.get("marketName"))
        on = (o.get("name") or "").strip().lower()
        if ";" in mn or " & " in mn or " lub " in mn or " od " in mn:
            continue
        if ";" in on or " & " in on or " lub " in on:
            continue
        if mn == "liczba goli":
            scope = "match"
        elif mn in ("1.połowa - liczba goli", "1. połowa - liczba goli"):
            scope = "1h"
        elif mn in ("2.połowa - liczba goli", "2. połowa - liczba goli"):
            scope = "2h"
        else:
            continue
        line = _over_line_from_name(on)
        if line is None:
            continue
        if abs((line * 2) % 2 - 1) > 1e-6:
            continue
        try:
            price = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1:
            continue
        key = "over%d5" % int(line) if scope == "match" else "over%d5_%s" % (int(line), scope)
        is_under = on.startswith("poniżej") or on.startswith("ponizej") or on.startswith("under") or on.startswith("below")
        if is_under:
            uk = key + "_under"
            if uk not in out or price < out[uk]:
                out[uk] = price
            continue
        out[key] = price
    return out


def event_live_goal_odds(eid):
    """Aktualne kursy over z /events/{id}, cache ~55 s (live)."""
    if not eid:
        return {}
    def f():
        d = http_json("%s/events/%s" % (SB, eid), "Superbet")
        if not d:
            return None
        ev = d.get("data")
        if isinstance(ev, list):
            ev = ev[0] if ev else {}
        return parse_live_goal_odds((ev or {}).get("odds"))
    return cached("liveodds:%s" % eid, LIST_TTL, f) or {}


def event_goal_line_odds(eid, ttl=600):
    """Wszystkie proste linie over (mecz/1H/2H) z /events/{id}, cache dłuższy na przedmeczowe."""
    if not eid:
        return {}
    def f():
        d = http_json("%s/events/%s" % (SB, eid), "Superbet")
        if not d:
            return None
        ev = d.get("data")
        if isinstance(ev, list):
            ev = ev[0] if ev else {}
        return parse_live_goal_odds((ev or {}).get("odds"))
    return cached("goalodds:%s" % eid, ttl, f) or {}


def event_over_odds(eid):
    if not eid:
        return {}
    def f():
        d = http_json("%s/events/%s" % (SB, eid), "Superbet")
        if not d:
            return None
        ev = d.get("data")
        if isinstance(ev, list):
            ev = ev[0] if ev else {}
        return parse_over_odds((ev or {}).get("odds"))
    return cached("overodds:%s" % eid, 600, f) or {}


def _attach_over_odds(picks):
    eids, seen = [], set()
    for p in picks or []:
        eid = p.get("eventId")
        if eid and eid not in seen:
            seen.add(eid)
            eids.append(eid)
        if len(eids) >= goal_overs.MAX_PICKS:
            break
    if not eids:
        return picks
    got = {}
    with ThreadPoolExecutor(2) as ex:
        for eid, od in zip(eids, ex.map(event_over_odds, eids)):
            got[eid] = od or {}
    for p in picks or []:
        price = (got.get(p.get("eventId")) or {}).get(p.get("key"))
        if price:
            p["odds"] = price
            p["summary"] = goal_overs.pre_summary(p)
    return picks


def _attach_pre_line_odds(cards):
    """Dokleja kursy do wszystkich linii beforemeczowych i odświeża najlepszą linię."""
    eids, seen = [], set()
    for c in cards or []:
        eid = c.get("eventId")
        if eid and eid not in seen:
            seen.add(eid)
            eids.append(eid)
    if not eids:
        return cards
    got = {}
    with ThreadPoolExecutor(OVERS_WORKERS) as ex:
        for eid, od in zip(eids, ex.map(event_goal_line_odds, eids)):
            got[eid] = od or {}
    for c in cards or []:
        od = got.get(c.get("eventId")) or {}
        updated = goal_overs.apply_odds_to_match(c, od)
        c.clear(); c.update(updated)
        best = goal_overs.best_rank_line(c) or (
            max(c.get("lines") or [], key=lambda ln: (ln.get("chance") or 0, ln.get("n") or 0))
            if c.get("lines") else None)
        if best:
            for k in ("key", "play", "chance", "chance_pct", "frac", "k", "n",
                      "p_emp", "p_pois", "p_raw", "lam", "why", "line",
                      "odds", "implied", "implied_pct", "edge", "edge_pp", "verdict"):
                c[k] = best.get(k)
            c["summary"] = goal_overs.pre_line_summary(c, best)
        c["recommend"] = goal_overs.live_recommend(c)
    return cards


# ---------- Overy LIVE (tylko mecze trwające) ----------
LIVE_OVERS_TTL = 55
LIVE_OVERS_STATE = {"res": None, "t": 0, "job": None}
LIVE_PROFILES = {}  # eventId -> (home_profile, away_profile)
LIVE_FAIL_UNTIL = {}  # eventId -> unix, krótki backoff po błędzie


def fetch_live_football():
    """Lista live piłki z Superbet: wynik, minuta, okres, betradarId. Bez dociągania kursów 1X2."""
    def f():
        now = datetime.now(timezone.utc)
        a, b = _window("live", now)
        q = urllib.parse.urlencode({"currentStatus": "active", "offerState": "live",
                                    "startDate": fmt(a), "endDate": fmt(b)})
        d = http_json(SB + "/events/by-date?" + q, "Superbet")
        if d is None:
            return None
        stc = struct()
        out = []
        for e in d.get("data") or []:
            if e.get("sportId") != 5:
                continue
            m = e.get("metadata") or {}
            name = e.get("matchName") or ""
            parts = name.split("·") if "·" in name else name.split(" - ")
            start = e.get("utcDate") or e.get("matchDate") or e.get("startDate")
            dt = parse_utc(start)
            tn = stc["t"].get(str(e.get("tournamentId")), ("", 0))
            out.append({
                "eventId": e.get("eventId"), "name": name.replace("·", " – "),
                "home": parts[0].strip() if parts else "",
                "away": parts[1].strip() if len(parts) > 1 else "",
                "br": str(e.get("betradarId") or m.get("brId") or "") or None,
                "tournamentId": e.get("tournamentId"), "league": tn[0] or None,
                "country": stc["c"].get(str(e.get("categoryId"))) or None,
                "start": start, "ts": int(dt.timestamp() * 1000) if dt else None,
                "start_warsaw": to_warsaw(dt).strftime("%H:%M") if dt else "",
                "day_warsaw": to_warsaw(dt).strftime("%Y-%m-%d") if dt else "",
                "minutes": m.get("minutes"), "period": m.get("periodStatus"),
                "score": None if m.get("homeTeamScore") is None else "%s:%s" % (m.get("homeTeamScore"), m.get("awayTeamScore")),
                "periods": m.get("periods") or [],
                "remainingPeriodTime": m.get("remainingPeriodTime") or m.get("remainingTime"),
                "status": m.get("status"), "mode": "live",
            })
        return out
    return cached("list:live-foot", LIST_LIVE_TTL, f) or []


def _live_meta(ev):
    return dict(eventId=ev.get("eventId"), start_warsaw=ev.get("start_warsaw"),
                day_warsaw=ev.get("day_warsaw"), ts=ev.get("ts"),
                home=ev.get("home"), away=ev.get("away"),
                home_name=ev.get("home"), away_name=ev.get("away"),
                tournament=ev.get("league"), category=ev.get("country"),
                mode="live", minutes=ev.get("minutes"), score=ev.get("score"),
                period=ev.get("period"), periods=ev.get("periods") or [],
                remainingPeriodTime=ev.get("remainingPeriodTime"),
                br=ev.get("br"), o1=ev.get("o1"), ox=ev.get("ox"), o2=ev.get("o2"),
                start_pl=("%s %s" % (ev.get("day_warsaw") or "", ev.get("start_warsaw") or "")).strip())


def _live_profile_pair(ev):
    mi = match_info(ev["br"])
    if not mi:
        return None
    hu = (mi.get("home") or {}).get("uid")
    au = (mi.get("away") or {}).get("uid")
    if not hu or not au:
        return None
    hv = team_goal_views(hu, ev.get("br"))
    av = team_goal_views(au, ev.get("br"))
    if not hv or not av:
        return None
    hp = goal_overs.team_profile(str(hu), hv, name=ev.get("home") or (mi["home"] or {}).get("name"))
    ap = goal_overs.team_profile(str(au), av, name=ev.get("away") or (mi["away"] or {}).get("name"))
    return hp, ap


def _peek_cached(key, ttl):
    with LOCK:
        v = CACHE.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    return None


def _live_odds_for(eids, fetch=False):
    """Kursy over dla eventów. fetch=False — tylko z cache (lekko przy liczeniu historii)."""
    got = {}
    eids = [e for e in eids if e]
    if not eids:
        return got
    if not fetch:
        for eid in eids:
            got[eid] = _peek_cached("liveodds:%s" % eid, LIST_TTL) or {}
        return got
    with ThreadPoolExecutor(OVERS_WORKERS) as ex:
        for eid, od in zip(eids, ex.map(event_live_goal_odds, eids)):
            got[eid] = od or {}
    return got


def _publish_live_overs(evs, partial=False, fetch_odds=False, fetched_at=None):
    fetched_at = fetched_at if fetched_at is not None else cache_fetched_at("list:live-foot")
    age = (time.time() - fetched_at) if fetched_at else None
    stale_feed = bool(fetched_at is None or (age is not None and age > LIVE_STATE_MAX_AGE))
    listed_n = len(evs or [])
    if stale_feed:
        evs = []
    cards, skipped = [], {"nobr": 0, "nostats": 0, "noline": 0, "done": 0, "stale_feed": int(stale_feed)}
    for ev in evs:
        if not ev.get("br"):
            skipped["nobr"] += 1
            continue
        pair = LIVE_PROFILES.get(ev.get("eventId"))
        if not pair:
            if ev.get("eventId") in LIVE_PROFILES:
                skipped["nostats"] += 1
            continue
        if pair is None:
            skipped["nostats"] += 1
            continue
        hp, ap = pair
        card = goal_overs.live_match_card(hp, ap, _live_meta(ev))
        skipped["done"] += 1
        if card:
            stamp_card(card, fetched_at, live=True)
            cards.append(card)
        else:
            skipped["noline"] += 1
    odds_map = _live_odds_for([c.get("eventId") for c in cards], fetch=fetch_odds)
    for i, card in enumerate(cards):
        cards[i] = goal_overs.apply_odds_to_match(card, odds_map.get(card.get("eventId")) or {})
        stamp_card(cards[i], fetched_at, live=True)
    # wszystkie mecze z historią w jednej liście (mała próba = ostrzeżenie)
    matches_sorted = sorted(
        cards,
        key=lambda m: (
            -(max((ln.get("edge") if ln.get("edge") is not None else -99) for ln in (m.get("lines") or []) or [{}])),
            -(max((ln.get("chance") or 0) for ln in (m.get("lines") or []) or [{}])),
            m.get("ts") or 0,
        ),
    )
    picks = [goal_overs.public_line_card(c) for c in goal_overs.rank_live_lines(matches_sorted)]
    pub_matches = [goal_overs.public_match_card(m) for m in matches_sorted]
    now = datetime.now(timezone.utc)
    res = {
        "generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"),
        "feed_warsaw": state_stamp(fetched_at)[1],
        "feed_age_seconds": None if age is None else max(0, round(age)),
        "picks": picks, "n_picks": len(picks),
        "matches": pub_matches, "n_matches": len(pub_matches),
        "source": "live", "partial": partial, "mode": "live",
        "footer": {"matches": listed_n, "checked": skipped["done"],
                   "without_stats": skipped["nostats"] + skipped["nobr"],
                   "no_sample": skipped["noline"],
                   "stale_feed": skipped.get("stale_feed", 0),
                   "min_n": goal_overs.MIN_N,
                   "p_min": goal_overs.LIVE_RANK_P_MIN,
                   "edge_plus": goal_overs.EDGE_PLUS},
        "rule": goal_overs.CHANCE_RULE,
        "note": ("Tylko mecze na żywo. Szansa dotyczy goli w pozostałym czasie tej połowy. Każdy mecz raz. Przy typie zawsze widać szansę na wygraną zakładu, kurs i czy się opłaca. Forma, nie gwarancja. Nic nie jest stawiane."
                 if not stale_feed else
                 "Nie pokazuję typów live — stan z Superbet jest starszy niż ok. 2 minuty. Chwilę poczekaj i odśwież."),
    }
    with LOCK:
        LIVE_OVERS_STATE.update(res=res, t=time.time())
    return res


def _live_overs_one(ev):
    try:
        if not ev.get("br"):
            return ev, None
        pair = _live_profile_pair(ev)
        return ev, pair
    except Exception as ex:
        ERRORS["overy_live"] = "%s: %s" % (type(ex).__name__, ex)
        return ev, None


def build_overs_live(progress=None, job=None):
    evs = fetch_live_football()
    if progress is not None:
        progress.update(done=0, total=len(evs))
    now = time.time()
    need = [e for e in evs if e.get("br") and e.get("eventId") not in LIVE_PROFILES
            and (LIVE_FAIL_UNTIL.get(e.get("eventId")) or 0) < now]
    def touch(ev, pair):
        eid = ev.get("eventId")
        with LOCK:
            if pair:
                LIVE_PROFILES[eid] = pair
                LIVE_FAIL_UNTIL.pop(eid, None)
            else:
                LIVE_FAIL_UNTIL[eid] = time.time() + 120
        if progress is not None:
            with LOCK:
                progress["done"] = progress.get("done", 0) + 1
                if job is not None:
                    job["beat"] = time.time()
        _publish_live_overs(evs, partial=True, fetch_odds=False)

    if need:
        with ThreadPoolExecutor(OVERS_WORKERS) as pool:
            futs = [pool.submit(_live_overs_one, ev) for ev in need]
            for fut in as_completed(futs):
                if job and job.get("cancel"):
                    break
                ev, pair = fut.result()
                touch(ev, pair)
    return _publish_live_overs(evs, partial=False, fetch_odds=True)


def _live_overs_worker(job):
    try:
        build_overs_live(job.get("progress"), job=job)
        with LOCK:
            ERRORS.pop("overy_live_job", None)
    except Exception as e:
        ERRORS["overy_live"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK:
            if LIVE_OVERS_STATE.get("job") is job:
                LIVE_OVERS_STATE["job"] = None


def get_overs_live():
    """Karty z cache'owanych profili od razu, stan live co ~30 s z listy Superbet, historia z dysku/pamięci."""
    evs = fetch_live_football()
    fetched_at = cache_fetched_at("list:live-foot")
    # usuń zakończone z bieżącego widoku (profile zostają)
    missing = [e for e in evs if e.get("br") and e.get("eventId") not in LIVE_PROFILES
               and (LIVE_FAIL_UNTIL.get(e.get("eventId")) or 0) < time.time()]
    # kursy dociągamy, gdy historia już jest — cache ~30–55 s, bez dodatkowego liczenia timeline
    res = _publish_live_overs(evs, partial=bool(LIVE_OVERS_STATE.get("job") or missing),
                             fetch_odds=not missing, fetched_at=fetched_at)
    with LOCK:
        job = LIVE_OVERS_STATE.get("job")
        if missing and job is None:
            job = LIVE_OVERS_STATE["job"] = {
                "progress": {"done": 0, "total": len(missing)},
                "started": time.time(), "beat": time.time(), "cancel": False,
            }
            threading.Thread(target=_live_overs_worker, args=(job,), daemon=True).start()
        elif job and not missing:
            # nic do doliczenia — nie startuj drugiego
            pass
        res_now, t = LIVE_OVERS_STATE.get("res") or res, LIVE_OVERS_STATE.get("t") or 0
    out = dict(res_now or res)
    computing = bool(job) and bool(missing or (job and (time.time() - (job.get("started") or 0)) < 2))
    # jeśli worker jeszcze działa, UI odpytuje częściej tylko na początku
    if job and missing:
        out["status"] = "computing"
        out["progress"] = dict(job.get("progress") or {})
        out["elapsed_seconds"] = round(time.time() - job["started"])
    else:
        out["status"] = "ready"
    out["stale"] = False
    out["age_seconds"] = round(time.time() - t) if t else None
    out["errors"] = dict(ERRORS)
    out["live_matches"] = len(evs)
    return out



# ---------- Overy: tenis, siatka, kosz, hokej ----------
OTHER_OVERS_TTL = 55
OTHER_OVERS_STATE = {"res": None, "t": 0, "job": None}
OTHER_PROFILES = {}  # eventId -> historia (lastx + kursy); stan live dokładany przy żądaniu
OTHER_LIVE_CAP = 12   # na dyscyplinę, najpierw live
OTHER_PRE_CAP = 8
OTHER_SPORTS = (1, 2, 3, 4)


def fetch_other_list(state):
    """Lista Superbet dla tenisa, siatkówki, kosza i hokeja (bez dociągania 1X2)."""
    def f():
        now = datetime.now(timezone.utc)
        a, b = _window(state, now)
        q = urllib.parse.urlencode({"currentStatus": "active", "offerState": state,
                                    "startDate": fmt(a), "endDate": fmt(b)})
        d = http_json(SB + "/events/by-date?" + q, "Superbet")
        if d is None:
            return None
        stc = struct()
        out = []
        for e in d.get("data") or []:
            sid = e.get("sportId")
            if sid not in OTHER_SPORTS:
                continue
            sport = other_overs.SPORT_BY_ID.get(sid)
            if not sport:
                continue
            m = e.get("metadata") or {}
            name = e.get("matchName") or ""
            parts = name.split("·") if "·" in name else name.split(" - ")
            start = e.get("utcDate") or e.get("matchDate") or e.get("startDate")
            dt = parse_utc(start)
            tn = stc["t"].get(str(e.get("tournamentId")), ("", 0))
            hs, aws = m.get("homeTeamScore"), m.get("awayTeamScore")
            out.append({
                "eventId": e.get("eventId"), "sportId": sid, "sport": sport,
                "name": name.replace("·", " – "),
                "home": parts[0].strip() if parts else "",
                "away": parts[1].strip() if len(parts) > 1 else "",
                "br": str(e.get("betradarId") or m.get("brId") or "") or None,
                "tournament": tn[0] or None,
                "category": stc["c"].get(str(e.get("categoryId"))) or None,
                "start": start, "ts": int(dt.timestamp() * 1000) if dt else None,
                "start_warsaw": to_warsaw(dt).strftime("%H:%M") if dt else "",
                "day_warsaw": to_warsaw(dt).strftime("%Y-%m-%d") if dt else "",
                "minutes": m.get("minutes"),
                "period": m.get("periodStatus"),
                "periodStatus": m.get("periodStatus"),
                "score": None if hs is None else "%s:%s" % (hs, aws),
                "home_score": hs, "away_score": aws,
                "homeTeamScore": hs, "awayTeamScore": aws,
                "homeTeamGameScore": m.get("homeTeamGameScore"),
                "awayTeamGameScore": m.get("awayTeamGameScore"),
                "periods": m.get("periods") or [],
                "remainingPeriodTime": m.get("remainingPeriodTime") or m.get("remainingTime"),
                "status": m.get("status"), "mode": "live" if state == "live" else "prematch",
            })
        return out
    ttl = LIST_LIVE_TTL if state == "live" else LIST_TTL
    return cached("list:other-%s" % state, ttl, f) or []


def _select_other_events(live, pre):
    out, seen = [], set()
    per_live, per_pre = {s: 0 for s in OTHER_SPORTS}, {s: 0 for s in OTHER_SPORTS}
    for e in live or []:
        sid = e.get("sportId")
        eid = e.get("eventId")
        if sid not in per_live or eid in seen:
            continue
        if per_live[sid] >= OTHER_LIVE_CAP:
            continue
        per_live[sid] += 1
        seen.add(eid)
        out.append(e)
    pres = sorted([e for e in (pre or []) if e], key=lambda x: x.get("ts") or 0)
    for e in pres:
        sid = e.get("sportId")
        eid = e.get("eventId")
        if sid not in per_pre or eid in seen:
            continue
        if per_pre[sid] >= OTHER_PRE_CAP:
            continue
        per_pre[sid] += 1
        seen.add(eid)
        out.append(e)
    return out


def event_other_odds(eid, sport, live=False):
    if not eid:
        return {}
    ttl = LIST_TTL if live else 600

    def f():
        d = http_json("%s/events/%s" % (SB, eid), "Superbet")
        if not d:
            return None
        ev = d.get("data")
        if isinstance(ev, list):
            ev = ev[0] if ev else {}
        return other_overs.parse_other_odds((ev or {}).get("odds"), sport)
    return cached("otherodds:%s:%s" % (eid, sport), ttl, f) or {}


def _other_base_ev(ev):
    keys = ("eventId", "sportId", "sport", "name", "home", "away", "br",
            "tournament", "category", "start", "ts", "start_warsaw", "day_warsaw", "mode")
    return {k: ev.get(k) for k in keys}


def _store_other_profile(ev, home_ms, away_ms, offers, info):
    eid = ev.get("eventId")
    if not eid:
        return
    rec = {
        "home_ms": home_ms, "away_ms": away_ms, "offers": offers or {},
        "info": info, "sport": ev.get("sport"), "sportId": ev.get("sportId"),
        "base": _other_base_ev(ev),
        "seen_live": ev.get("mode") == "live",
        "stored_at": time.time(),
    }
    with LOCK:
        prev = OTHER_PROFILES.get(eid) or {}
        if prev.get("seen_live"):
            rec["seen_live"] = True
        OTHER_PROFILES[eid] = rec


def _card_from_other_profile(prof, ev, fetched_at, live=False):
    ev_full = dict(prof.get("base") or {})
    ev_full.update(ev or {})
    ev_full["sport"] = ev_full.get("sport") or prof.get("sport")
    ev_full["mode"] = "live" if live else (ev_full.get("mode") or "prematch")
    offers = prof.get("offers") or {}
    # świeże kursy tylko z pamięci — bez dodatkowego HTTP na żądanie
    peek = _peek_cached("otherodds:%s:%s" % (ev_full.get("eventId"), ev_full.get("sport")),
                        LIST_TTL if live else 600)
    if peek:
        offers = peek
    card = other_overs.match_card(prof.get("home_ms") or [], prof.get("away_ms") or [], ev_full, offers)
    if not card:
        return None
    info = prof.get("info") or {}
    if info.get("tournament") and not card.get("tournament"):
        card["tournament"] = info["tournament"]
    if info.get("category") and not card.get("category"):
        card["category"] = info["category"]
    stamp_card(card, fetched_at, live=live)
    return card


def _build_other_card(ev):
    br = ev.get("br")
    if not br:
        return None, "nobr"
    info = match_info(br)
    if not info:
        return None, "nostats"
    hu = (info.get("home") or {}).get("uid")
    au = (info.get("away") or {}).get("uid")
    if not hu or not au:
        return None, "nostats"
    hd, ad = lastx_raw(hu), lastx_raw(au)
    if hd is None and ad is None:
        return None, "nostats"
    sport = ev.get("sport")
    home_ms = other_overs.parse_lastx(hd, sport)
    away_ms = other_overs.parse_lastx(ad, sport)
    if not other_overs.has_main_history(home_ms, away_ms, sport):
        return None, "thin"
    offers = event_other_odds(ev.get("eventId"), sport, live=(ev.get("mode") == "live"))
    if not offers:
        return None, "noodds"
    _store_other_profile(ev, home_ms, away_ms, offers, info)
    live = ev.get("mode") == "live"
    fetched_at = cache_fetched_at("list:other-live" if live else "list:other-prematch")
    card = other_overs.match_card(home_ms, away_ms, ev, offers)
    if not card:
        return None, "noline"
    if info.get("tournament") and not card.get("tournament"):
        card["tournament"] = info["tournament"]
    if info.get("category") and not card.get("category"):
        card["category"] = info["category"]
    stamp_card(card, fetched_at, live=live)
    return card, None


def refresh_other_cards(live=None, live_t=None, pre=None, pre_t=None, now=None, profiles=None):
    """Przelicz karty z zapamiętanej historii i świeżego stanu Superbet. Bez lastx."""
    now = time.time() if now is None else now
    if live is None:
        live = fetch_other_list("live")
        live_t = cache_fetched_at("list:other-live")
    if pre is None:
        pre = fetch_other_list("prematch")
        pre_t = cache_fetched_at("list:other-prematch")
    # wiek liczony od teraz (fetch listy może ustawić live_t po zapamiętaniu now)
    live_age = (time.time() - live_t) if live_t else None
    if now is not None and live_t:
        # w testach podajemy sztuczne now
        live_age = now - live_t
    if live_age is not None:
        live_age = max(0.0, live_age)
    live_ok = live_t is not None and live_age is not None and live_age <= LIVE_STATE_MAX_AGE
    live_by = {e.get("eventId"): e for e in (live or []) if e and e.get("eventId") is not None}
    pre_by = {e.get("eventId"): e for e in (pre or []) if e and e.get("eventId") is not None}
    if profiles is None:
        with LOCK:
            profiles = dict(OTHER_PROFILES)
    cards, dropped_stale, dropped_ended = [], 0, 0
    for eid, prof in (profiles or {}).items():
        ev_live = live_by.get(eid)
        if ev_live:
            if not live_ok:
                dropped_stale += 1
                continue
            c = _card_from_other_profile(prof, ev_live, live_t, live=True)
            if c:
                cards.append(c)
                with LOCK:
                    if eid in OTHER_PROFILES:
                        OTHER_PROFILES[eid]["seen_live"] = True
            continue
        was_live = bool(prof.get("seen_live") or (prof.get("base") or {}).get("mode") == "live")
        if was_live:
            dropped_ended += 1
            continue
        ts = (prof.get("base") or {}).get("ts")
        if ts and now > (ts / 1000.0) + 180:
            dropped_ended += 1
            continue
        ev_pre = pre_by.get(eid) or dict(prof.get("base") or {})
        ev_pre = dict(ev_pre)
        ev_pre["mode"] = "prematch"
        c = _card_from_other_profile(prof, ev_pre, pre_t or prof.get("stored_at"), live=False)
        if c:
            cards.append(c)
    return {
        "cards": cards,
        "live": live or [],
        "live_t": live_t,
        "live_ok": live_ok,
        "live_age": live_age,
        "dropped_stale": dropped_stale,
        "dropped_ended": dropped_ended,
        "listed_live": len(live or []),
        "listed_pre": len(pre or []),
    }


def _publish_other(cards, evs, skipped, processed_ids, partial, listed_by=None, touch=True):
    ranked = other_overs.rank_cards(cards)
    pub_matches = [other_overs.public_match_card(c) for c in cards if c]
    picks = [other_overs.public_line_card(r) for r in ranked]
    now = datetime.now(timezone.utc)
    cov = listed_by or {}
    sports = {}
    for sid, name in other_overs.SPORT_BY_ID.items():
        pl = other_overs.SPORT_PL[name]
        with_h = sum(1 for c in cards if c.get("sport") == name)
        ranked_n = sum(1 for p in picks if p.get("sport") == name)
        sports[name] = {"id": sid, "label": pl, "listed": cov.get(sid, 0),
                        "with_history": with_h, "ranked": ranked_n}
    live_t = cache_fetched_at("list:other-live")
    res = {
        "generated_warsaw": to_warsaw(now).strftime("%Y-%m-%d %H:%M:%S"),
        "feed_warsaw": state_stamp(live_t)[1],
        "feed_age_seconds": None if not live_t else round(time.time() - live_t),
        "picks": picks, "n_picks": len(picks),
        "matches": pub_matches, "n_matches": len(pub_matches),
        "source": "live", "partial": partial,
        "note": ("Tenis, siatkówka, koszykówka i hokej. Każdy mecz raz. "
                 "Typuję tylko przy kursie Superbet ok. 1,30 albo wyższym. "
                 "Bez historii (mniej niż 8 meczów na stronę) spotkania nie pokazuję. "
                 "Szansa na wygraną zakładu jest przy każdym typie. Forma, nie gwarancja. Nic nie jest stawiane."),
        "rule": other_overs.CHANCE_RULE,
        "footer": {
            "matches": len(evs), "checked": len(processed_ids),
            "without_stats": skipped.get("nostats", 0) + skipped.get("nobr", 0),
            "too_weak": skipped.get("thin", 0),
            "no_odds": skipped.get("noodds", 0),
            "no_line": skipped.get("noline", 0),
            "min_n_side": other_overs.MIN_SIDE,
            "p_min": other_overs.P_MIN,
            "min_odds": other_overs.MIN_ODDS,
            "sports": sports,
        },
    }
    with LOCK:
        OTHER_OVERS_STATE["res"] = res
        if touch:
            OTHER_OVERS_STATE["t"] = time.time() if not partial else OTHER_OVERS_STATE["t"] or time.time()
    return res


def _other_overs_worker(job):
    try:
        live, pre = fetch_other_list("live"), fetch_other_list("prematch")
        evs = _select_other_events(live, pre)
        listed_by = {}
        for e in evs:
            listed_by[e.get("sportId")] = listed_by.get(e.get("sportId"), 0) + 1
        cards, skipped, processed = [], {}, set()
        job["progress"] = {"done": 0, "total": len(evs)}
        for i, ev in enumerate(evs, 1):
            if job.get("cancel"):
                break
            job["beat"] = time.time()
            try:
                c, why = _build_other_card(ev)
                if c:
                    cards.append(c)
                else:
                    skipped[why or "err"] = skipped.get(why or "err", 0) + 1
            except Exception as e:
                skipped["err"] = skipped.get("err", 0) + 1
                ERRORS["overy_inne"] = "%s: %s" % (type(e).__name__, e)
            processed.add(ev.get("eventId"))
            job["progress"]["done"] = i
            if i % 4 == 0 or i == len(evs):
                _publish_other(cards, evs, skipped, processed, partial=(i < len(evs)),
                               listed_by=listed_by)
        _publish_other(cards, evs, skipped, processed, partial=False, listed_by=listed_by)
    except Exception as e:
        ERRORS["overy_inne"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK:
            if OTHER_OVERS_STATE.get("job") is job:
                OTHER_OVERS_STATE["job"] = None


def get_overs_other():
    """Jedna zakładka: live i przed meczem. Stan live ze świeżej listy Superbet, historia z cache."""
    with LOCK:
        res, t, job = OTHER_OVERS_STATE["res"], OTHER_OVERS_STATE["t"], OTHER_OVERS_STATE["job"]
        has_prof = bool(OTHER_PROFILES)
        history_fresh = bool(t and time.time() - t < OTHER_OVERS_TTL and res and not res.get("partial"))
        if not history_fresh and job is None:
            job = OTHER_OVERS_STATE["job"] = {
                "progress": {"done": 0, "total": 0},
                "started": time.time(), "beat": time.time(), "cancel": False,
            }
            threading.Thread(target=_other_overs_worker, args=(job,), daemon=True).start()
    meta = refresh_other_cards()
    cards = meta["cards"]
    if cards or has_prof or (res and (res.get("matches") or res.get("picks"))):
        # przelicz typowanie na świeżym stanie; lista evs tylko do stopki
        evs = list(meta.get("live") or [])
        skipped = {
            "nostats": (res or {}).get("footer", {}).get("without_stats", 0) if res else 0,
            "nobr": 0,
            "thin": (res or {}).get("footer", {}).get("too_weak", 0) if res else 0,
            "noodds": (res or {}).get("footer", {}).get("no_odds", 0) if res else 0,
            "noline": (res or {}).get("footer", {}).get("no_line", 0) if res else 0,
        }
        listed_by = {}
        for e in evs:
            listed_by[e.get("sportId")] = listed_by.get(e.get("sportId"), 0) + 1
        # też przedmeczowe z profili
        with LOCK:
            for pr in OTHER_PROFILES.values():
                sid = pr.get("sportId")
                if sid is not None:
                    listed_by[sid] = listed_by.get(sid, 0)
        processed = set()
        out = _publish_other(cards, evs, skipped, processed,
                             partial=bool(job and not history_fresh), listed_by=listed_by,
                             touch=False)
        out["feed_warsaw"] = state_stamp(meta.get("live_t"))[1]
        out["feed_age_seconds"] = None if meta.get("live_age") is None else round(meta["live_age"])
        out["dropped_ended"] = meta.get("dropped_ended", 0)
        out["dropped_stale"] = meta.get("dropped_stale", 0)
        if not meta.get("live_ok") and any((c.get("mode") == "live") for c in cards):
            pass
        if not meta.get("live_ok"):
            # live karty już wycięte w refresh; dopisz komunikat gdy coś wypadło
            if meta.get("dropped_stale"):
                out["note"] = ("Nie pokazuję typów live — stan z Superbet jest starszy niż ok. 2 minuty. "
                               "Przedmeczowe zostawiam. Chwilę poczekaj i odśwież. " + (out.get("note") or ""))
        status = "computing" if job and not history_fresh else "ready"
        out.update(status=status, stale=not history_fresh,
                   age_seconds=None if not meta.get("live_t") else round(time.time() - meta["live_t"]),
                   errors=dict(ERRORS))
        if job:
            out["progress"] = dict(job["progress"])
            out["elapsed_seconds"] = round(time.time() - job["started"])
        return out
    return {"status": "computing",
            "progress": dict(job["progress"]) if job else {"done": 0, "total": 0},
            "elapsed_seconds": round(time.time() - job["started"]) if job else 0,
            "errors": dict(ERRORS), "picks": [], "matches": [],
            "note": "Liczą się overy tenisa, siatkówki, kosza i hokeja w tle. To chwilę trwa — nic nie zmyślam. Nic nie jest stawiane."}


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
        with LOCK: RESULTS[key] = (time.time(), res); ERRORS.pop("ranking", None); ERRORS.pop("ranking_job", None)
    except Exception as e:
        ERRORS["ranking"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with LOCK:
            if JOBS.get(key, {}).get("progress") is prog: JOBS.pop(key, None)

RANK_JOB_MAX = 600
REFRESH_EVERY = 60
REFRESH_STATE = {"last": 0.0}

def refresh_tick():
    """Jedno przejście odświeżacza: uruchamia przeliczenie przeterminowanych rankingów (bez czekania)."""
    for fn in (lambda: get_ranking("all"), lambda: get_ranking("all", 3, *VIP_RULES["odds_range"]), get_worst, get_overs, get_overs_live):
        try: fn()
        except Exception as e: print("[refresh] %s: %s" % (type(e).__name__, e), file=sys.stderr)
    REFRESH_STATE["last"] = time.time()

def _refresher():
    while True:
        refresh_tick(); time.sleep(REFRESH_EVERY)

def get_ranking(mode="all", min_games=3, odds_min=1.2, odds_max=2.0):
    key = _rkey(mode, min_games, odds_min, odds_max)
    with LOCK:
        r = RESULTS.get(key)
        fresh = r and time.time() - r[0] < TTL[mode]
        job = JOBS.get(key)
        for k in [k for k, j in JOBS.items() if time.time() - j["started"] > RANK_JOB_MAX]:   # zawieszone liczenie nie blokuje
            ERRORS["ranking_job"] = "liczenie > %d s – restart" % RANK_JOB_MAX; JOBS.pop(k, None)
            if k == key: job = None
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
                "persistence": {"active": persist_active(), "ok": GIST_STATE["ok"], "error": GIST_STATE["err"]},
                "refresher": {"last_seconds_ago": round(time.time() - REFRESH_STATE["last"]) if REFRESH_STATE["last"] else None}}

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
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/next-goal/tip":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b"{}"
                pick = json.loads(body.decode("utf-8") or "{}")
                res = ng_place_tip(pick)
                return self.send(200 if res.get("ok") else 409, json.dumps(res, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e:
                return self.send(500, json.dumps({"ok": False, "error": str(e)}), "application/json")
        return self.send(404, json.dumps({"error": "nie znaleziono"}), "application/json")
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
        if u.path == "/api/worst":
            q = dict(urllib.parse.parse_qsl(u.query))
            try: return self.send(200, json.dumps(get_worst(q.get("mode", "all")), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/pro":
            try: return self.send(200, json.dumps(get_pro(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path.startswith("/api/next-goal/tip/"):
            tip_id = urllib.parse.unquote(u.path[len("/api/next-goal/tip/"):])
            try:
                d = ng_get_tip(tip_id)
                if not d: return self.send(404, json.dumps({"error": "nie znaleziono typu"}), "application/json; charset=utf-8")
                return self.send(200, json.dumps(d, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path == "/api/goal-overs-live":
            try: return self.send(200, json.dumps(get_overs_live(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path in ("/api/goal-overs", "/api/goal-overs-pre"):
            try: return self.send(200, json.dumps(get_overs(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e: return self.send(500, json.dumps({"error": str(e)}), "application/json")
        if u.path in ("/api/goal-overs-other", "/api/goal-overs-inne"):
            try: return self.send(200, json.dumps(get_overs_other(), ensure_ascii=False), "application/json; charset=utf-8")
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
    threading.Thread(target=_refresher, daemon=True).start()   # rankingi przeliczane w tle niezależnie od gista/zapytań
    if "--open" in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:%d" % port)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("Zatrzymano.")

if __name__ == "__main__":
    main()
