# -*- coding: utf-8 -*-
"""Overy: tenis, siatkówka, koszykówka, hokej.

Szansa tylko z prawdziwej historii (Sportradar lastx) i kursów Superbet.
Nic nie jest wymyślane ani stawiane.
"""
import re
import goal_overs as g

MIN_SIDE = 8          # minimum meczów z daną statystyką na każdą stronę
P_MIN = 0.60
MIN_ODDS = g.PRE_MIN_ODDS  # ok. 1.30
MAX_PICKS = g.MAX_PICKS
EDGE_PLUS = g.EDGE_PLUS

SPORT_BY_ID = {1: "volleyball", 2: "tennis", 3: "hockey", 4: "basketball"}
SPORT_PL = {
    "tennis": "Tenis",
    "volleyball": "Siatkówka",
    "basketball": "Koszykówka",
    "hockey": "Hokej",
}
# Główna statystyka, po której sprawdzamy, czy w ogóle jest historia.
MAIN_FIELD = {
    "tennis": "games",
    "volleyball": "sets",
    "basketball": "points",
    "hockey": "goals_reg",
}

CHANCE_RULE = (
    "Biorę ostatnie mecze obu drużyn albo zawodników i liczę, jak często wynik "
    "przekraczał daną linię (np. liczba gemów, setów, punktów albo goli). "
    "Szansa na wygraną zakładu to ta częstość, lekko poprawiona prostym rachunkiem "
    "(dokładam po jednym „tak” i jednym „nie”, żeby przy małej liczbie meczów nie przesadzać). "
    "Gdy mecz już trwa, nie patrzę na cały dawny wynik. Liczę, ile jeszcze zwykle padało "
    "w pozostałym czasie: w tej kwarcie albo tercji (według minut, które zostają) "
    "i w następnych częściach gry, albo w tym i kolejnych setach. "
    "Typuję tylko linie z kursem Superbet ok. 1,30 albo wyższym. "
    "Gdy nasza szansa wychodzi dużo wyżej (o więcej niż 15 na 100) niż to, co sugeruje kurs, "
    "nie typuję — piszę „niepewne dane”. "
    "Jak którejś strony brakuje historii (mniej niż 8 meczów), w ogóle nie pokazuję tego spotkania. "
    "To częstość z historii, nie pewność że tak będzie."
)


def _as_int(v):
    if v is None or v is False:
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _flag(v):
    if v in (None, 0, "0", False, "false", "False", "no", ""):
        return False
    return True


def _pair(obj):
    if not isinstance(obj, dict):
        return None
    h, a = _as_int(obj.get("home")), _as_int(obj.get("away"))
    if h is None or a is None:
        return None
    return h, a


def _void_match(m):
    if not m or not isinstance(m, dict):
        return True
    for k in ("walkover", "retired", "canceled", "cancelled", "postponed",
              "tobeannounced", "disqualified"):
        if _flag(m.get(k)):
            return True
    return False


def lastx_matches(data):
    if not data or not isinstance(data, dict):
        return []
    return [m for m in (data.get("matches") or []) if isinstance(m, dict)]


def parse_lastx(data, sport):
    """Zwraca listę zakończonych meczów z polami needed do overów."""
    out = []
    for m in lastx_matches(data):
        if _void_match(m):
            continue
        periods = m.get("periods") or {}
        if not isinstance(periods, dict):
            periods = {}
        res = _pair(m.get("result")) or _pair(periods.get("ft"))
        parts = {}
        for i in range(1, 6):
            pr = _pair(periods.get("p%d" % i))
            if pr:
                parts[i] = pr
        if not res and not parts:
            continue
        rec = {"result": res, "period_pairs": parts}
        if sport == "tennis":
            rec["sets"] = (res[0] + res[1]) if res else None
            rec["set_games"] = {i: a + b for i, (a, b) in parts.items()}
            rec["games"] = sum(rec["set_games"].values()) if rec["set_games"] else None
        elif sport == "volleyball":
            rec["sets"] = (res[0] + res[1]) if res else None
            rec["set_points"] = {i: a + b for i, (a, b) in parts.items()}
            rec["points"] = sum(rec["set_points"].values()) if rec["set_points"] else None
        elif sport == "basketball":
            rec["points"] = (res[0] + res[1]) if res else (
                sum(a + b for a, b in parts.values()) if parts else None)
            rec["quarters"] = {i: a + b for i, (a, b) in parts.items() if i <= 4}
            rec["h2"] = None
            if 3 in rec["quarters"] and 4 in rec["quarters"]:
                rec["h2"] = rec["quarters"][3] + rec["quarters"][4]
            qsum = sum(rec["quarters"].values()) if rec["quarters"] else None
            rec["ot_points"] = None
            if rec["points"] is not None and qsum is not None and rec["points"] > qsum:
                rec["ot_points"] = rec["points"] - qsum
        elif sport == "hockey":
            rec["goals_final"] = (res[0] + res[1]) if res else None
            if any(i in parts for i in (1, 2, 3)):
                rec["goals_reg"] = sum(a + b for i, (a, b) in parts.items() if i <= 3)
            else:
                rec["goals_reg"] = rec["goals_final"]
            rec["periods"] = {i: a + b for i, (a, b) in parts.items() if i <= 3}
            rec["ot_goals"] = None
            if rec["goals_final"] is not None and rec["goals_reg"] is not None and rec["goals_final"] > rec["goals_reg"]:
                rec["ot_goals"] = rec["goals_final"] - rec["goals_reg"]
        else:
            continue
        out.append(rec)
    return out


def side_n(matches, field, part=None):
    n = 0
    for m in matches or []:
        if _field_val(m, field, part) is not None:
            n += 1
    return n


def _field_val(m, field, part=None):
    if not m:
        return None
    if field == "set_games":
        return (m.get("set_games") or {}).get(part)
    if field == "set_points":
        return (m.get("set_points") or {}).get(part)
    if field == "quarters":
        return (m.get("quarters") or {}).get(part)
    if field == "periods":
        return (m.get("periods") or {}).get(part)
    return m.get(field)


def emp_chance(k, n):
    if not n:
        return None
    p = (k + 1) / (n + 2.0)
    return {
        "k": k, "n": n, "p": p, "pct": int(round(100 * p)),
        "frac": "%d/%d" % (k, n),
    }


def _count_over(matches, field, line, part=None, current=None):
    ok = []
    for m in matches or []:
        v = _field_val(m, field, part)
        if v is None:
            continue
        if current is not None and v < current:
            continue
        ok.append(v)
    k = sum(1 for v in ok if v > line)
    return k, len(ok)


def enough_sides(home_ms, away_ms, field, part=None):
    return side_n(home_ms, field, part) >= MIN_SIDE and side_n(away_ms, field, part) >= MIN_SIDE


def has_main_history(home_ms, away_ms, sport):
    field = MAIN_FIELD.get(sport)
    if not field:
        return False
    # hokej: wystarczy regulamin albo z dogrywką
    if sport == "hockey":
        return enough_sides(home_ms, away_ms, "goals_reg") or enough_sides(home_ms, away_ms, "goals_final")
    # siatkówka: sety albo punkty
    if sport == "volleyball":
        return enough_sides(home_ms, away_ms, "sets") or enough_sides(home_ms, away_ms, "points")
    return enough_sides(home_ms, away_ms, field)


# ---------- aktualny stan live ----------
def _period_rows(periods):
    rows = []
    for p in periods or []:
        if not isinstance(p, dict):
            continue
        h, a = _as_int(p.get("homeTeamScore")), _as_int(p.get("awayTeamScore"))
        if h is None or a is None:
            continue
        typ = str(p.get("type") or p.get("name") or "")
        num = _as_int(p.get("num") or p.get("number"))
        rows.append({"home": h, "away": a, "total": h + a, "type": typ, "num": num})
    return rows


def current_state(ev):
    """Aktualne sumy z metadanych Superbet. Nic nie zgadujemy."""
    sport = ev.get("sport")
    rows = _period_rows(ev.get("periods"))
    hs = _as_int(ev.get("home_score") if ev.get("home_score") is not None else ev.get("hs"))
    aws = _as_int(ev.get("away_score") if ev.get("away_score") is not None else ev.get("as_"))
    if hs is None:
        hs = _as_int(ev.get("homeTeamScore"))
    if aws is None:
        aws = _as_int(ev.get("awayTeamScore"))
    st = {
        "sets": None, "games": None, "set_games": {},
        "points": None, "set_points": {},
        "goals_reg": None, "goals_final": None, "periods": {},
        "quarters": {}, "h2": None,
        "score": ev.get("score"),
        "period": ev.get("period") or ev.get("periodStatus"),
        "minutes": ev.get("minutes"),
        "remaining": ev.get("remainingPeriodTime") or ev.get("remaining"),
    }
    if hs is not None and aws is not None:
        st["score"] = st["score"] or ("%s:%s" % (hs, aws))
    if sport == "tennis":
        if hs is not None and aws is not None:
            st["sets"] = hs + aws
        if rows:
            st["games"] = sum(r["total"] for r in rows)
            for r in rows:
                n = r["num"] or (len(st["set_games"]) + 1)
                st["set_games"][n] = r["total"]
    elif sport == "volleyball":
        if hs is not None and aws is not None:
            st["sets"] = hs + aws
        if rows:
            st["points"] = sum(r["total"] for r in rows)
            for r in rows:
                n = r["num"] or (len(st["set_points"]) + 1)
                st["set_points"][n] = r["total"]
    elif sport == "basketball":
        if hs is not None and aws is not None:
            st["points"] = hs + aws
        elif rows:
            st["points"] = sum(r["total"] for r in rows)
        for r in rows:
            n = r["num"]
            typ = r["type"].upper()
            if n is None:
                if "FIRST_QUARTER" in typ or typ.startswith("1"):
                    n = 1
                elif "SECOND_QUARTER" in typ or typ.startswith("2"):
                    n = 2
                elif "THIRD_QUARTER" in typ or typ.startswith("3"):
                    n = 3
                elif "FOURTH_QUARTER" in typ or typ.startswith("4"):
                    n = 4
            if n:
                st["quarters"][n] = r["total"]
        if 3 in st["quarters"] and 4 in st["quarters"]:
            st["h2"] = st["quarters"][3] + st["quarters"][4]
    elif sport == "hockey":
        if hs is not None and aws is not None:
            st["goals_final"] = hs + aws
            st["goals_reg"] = hs + aws
        if rows:
            tot = 0
            for r in rows:
                typ = r["type"].lower()
                if "overtime" in typ or "shootout" in typ or "dogryw" in typ:
                    continue
                n = r["num"] or (len(st["periods"]) + 1)
                if n <= 3:
                    st["periods"][n] = r["total"]
                    tot += r["total"]
            if tot:
                st["goals_reg"] = tot
                if st["goals_final"] is None:
                    st["goals_final"] = tot
    attach_clock(st, ev, sport)
    return st


def parse_mmss(s):
    """'5:03' albo '7:52' -> sekundy. Nic nie zgadujemy."""
    if s is None or s is False or s == "":
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(float(parts[1]))
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        return int(float(s))
    except (TypeError, ValueError):
        return None


def period_num_from_label(period, sport, rows=None):
    """Numer bieżącej kwarty/tercji/seta i czy to przerwa (część gry właśnie się skończyła)."""
    p = str(period or "").strip().upper().replace(" ", "")
    if p in ("HT", "PRZERWA", "BREAK", "INT", "PAUSE") or "PRZERW" in p:
        if sport == "basketball":
            return 2, True
        return None, True
    m = re.match(r"^(\d+)([A-Z]*)$", p)
    if m:
        n = int(m.group(1))
        suf = m.group(2)
        # 2B / 1B: po tercji, przerwa (nie liczę już goli w skończonej części)
        if suf == "B":
            return n, True
        return n, False
    if rows:
        nums = [r["num"] for r in rows if r.get("num")]
        if nums:
            return max(nums), False
    return None, False


def period_seconds(sport, rem_sec):
    if sport == "hockey":
        return 20 * 60
    if sport == "basketball":
        base = 10 * 60
        if rem_sec is not None and rem_sec > 10 * 60:
            base = 12 * 60
        if rem_sec is not None and rem_sec > 12 * 60:
            base = 20 * 60
        return base
    return None


def attach_clock(st, ev, sport):
    rem = parse_mmss(st.get("remaining") or (ev or {}).get("remainingPeriodTime") or (ev or {}).get("remaining"))
    period = st.get("period") or (ev or {}).get("period") or (ev or {}).get("periodStatus")
    rows = _period_rows((ev or {}).get("periods"))
    pnum, inter = period_num_from_label(period, sport, rows)
    if pnum is None:
        if sport == "basketball" and st.get("quarters"):
            pnum = max(st["quarters"])
        elif sport == "hockey" and st.get("periods"):
            pnum = max(st["periods"])
        elif sport == "volleyball" and st.get("set_points"):
            pnum = max(st["set_points"])
        elif sport == "tennis" and st.get("set_games"):
            pnum = max(st["set_games"])
    period_sec = period_seconds(sport, rem)
    rem_frac = None
    if inter:
        rem_frac = 0.0
        rem = 0
    elif rem is not None and period_sec:
        rem_frac = max(0.0, min(1.0, rem / float(period_sec)))
    st["clock"] = {
        "period_num": pnum,
        "intermission": inter,
        "rem_sec": rem,
        "period_sec": period_sec,
        "rem_frac": rem_frac,
        "time_unknown": (not inter) and rem_frac is None and sport in ("basketball", "hockey"),
    }
    return st


def time_label(ev, st=None):
    if (ev.get("mode") or ev.get("state")) == "live" or ev.get("mode") == "live":
        bits = ["LIVE"]
        if ev.get("score"):
            bits.append(str(ev["score"]))
        if ev.get("period") or ev.get("periodStatus"):
            bits.append(str(ev.get("period") or ev.get("periodStatus")))
        if ev.get("minutes") is not None:
            bits.append("%s'" % ev["minutes"])
        if ev.get("remainingPeriodTime") or ev.get("remaining"):
            bits.append("zostaje %s" % (ev.get("remainingPeriodTime") or ev.get("remaining")))
        return " · ".join(bits)
    t = ev.get("start_warsaw") or ""
    d = ev.get("day_warsaw") or ""
    if d and t:
        parts = str(d).split("-")
        short = ("%s.%s" % (parts[2], parts[1])) if len(parts) == 3 else d
        return "%s (%s, Warszawa)" % (t, short)
    if t:
        return "%s (Warszawa)" % t
    return ""


# ---------- kursy Superbet: tylko zwykłe overy ----------
_SKIP_MKT = (
    "&", "handicap", "nieparzyst", "parzyst", "dokładny", "zwycięzca",
    "podwójna", "pierwszy", "1. gol", "wygra seta", "mecz &",
)


def _norm_mkt(name):
    s = (name or "").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = s.replace("1.połowa", "1. połowa").replace("2.połowa", "2. połowa")
    return s


def _line_from_name(name):
    m = re.search(r"(?:powyżej|poniżej)\s*(\d+(?:[.,]\d+)?)", (name or "").lower())
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def is_over_outcome(name):
    n = (name or "").strip().lower()
    return n.startswith("powyżej") or n.startswith("above") or n.startswith("over")


def is_under_outcome(name):
    n = (name or "").strip().lower()
    return n.startswith("poniżej") or n.startswith("below") or n.startswith("under")


def market_key(market_name, sport):
    """Zwraca (key, field, part, play_unit) albo None gdy to nie jest zwykły over."""
    m = _norm_mkt(market_name)
    if not m:
        return None
    if any(x in m for x in _SKIP_MKT):
        return None
    # typ drużynowy: "Nazwa - liczba …" (myślnik nie będący "N. set - …")
    if sport == "tennis":
        if m == "liczba gemów":
            return "games", "games", None, "gema w meczu"
        mm = re.fullmatch(r"(\d+)\. set - liczba gemów", m)
        if mm:
            n = int(mm.group(1))
            return "set_games_%d" % n, "set_games", n, "gema w %d. secie" % n
        return None
    if sport == "volleyball":
        if m == "liczba setów":
            return "sets", "sets", None, "seta w meczu"
        if m == "liczba punktów":
            return "points", "points", None, "punktu w meczu"
        mm = re.fullmatch(r"(\d+)\. set - liczba punktów", m)
        if mm:
            n = int(mm.group(1))
            return "set_points_%d" % n, "set_points", n, "punktu w %d. secie" % n
        return None
    if sport == "basketball":
        if m in ("liczba punktów", "liczba punktów (z dogrywką)"):
            return "points", "points", None, "punktu w meczu"
        mm = re.fullmatch(r"(\d+)\. kwarta - liczba punktów", m)
        if mm:
            n = int(mm.group(1))
            return "q%d" % n, "quarters", n, "punktu w %d. kwarcie" % n
        if m == "2. połowa - liczba punktów":
            return "h2", "h2", None, "punktu w 2. połowie"
        return None
    if sport == "hockey":
        if m == "liczba goli":
            return "goals_reg", "goals_reg", None, "gola w meczu"
        if m == "liczba goli (z dogrywką i rzutami karnymi)":
            return "goals_final", "goals_final", None, "gola w meczu (z dogrywką)"
        mm = re.fullmatch(r"(\d+)\. tercja - liczba goli", m)
        if mm:
            n = int(mm.group(1))
            return "p%d" % n, "periods", n, "gola w %d. tercji" % n
        return None
    return None


def parse_other_odds(odds, sport):
    """Mapa klucz_linii -> kurs over (i under, gdy jest), tylko zwykłe rynki."""
    got = {}
    unders = {}
    for o in odds or []:
        if not isinstance(o, dict):
            continue
        stt = (o.get("status") or "active").lower()
        if stt in ("block", "blocked", "suspended", "inactive"):
            continue
        mk = market_key(o.get("marketName") or "", sport)
        if not mk:
            continue
        base, _field, _part, unit = mk
        on = o.get("name") or ""
        over, under = is_over_outcome(on), is_under_outcome(on)
        if not over and not under:
            continue
        line = _line_from_name(on)
        if line is None:
            continue
        try:
            price = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1:
            continue
        key = "%s_%.1f" % (base, line)
        if under:
            prev_u = unders.get(key)
            if prev_u is None or price < prev_u:
                unders[key] = price
            continue
        prev = got.get(key)
        if prev is None or price < prev["odds"]:
            got[key] = {
                "key": key, "base": base, "field": _field, "part": _part,
                "line": line, "odds": price, "unit": unit,
                "play": "Over %s %s" % (("%g" % line).replace(".", ","), unit),
            }
    for key, rec in got.items():
        if key in unders:
            rec["odds_under"] = unders[key]
    return got


def _current_for(st, field, part):
    if not st:
        return None
    if field == "set_games":
        return (st.get("set_games") or {}).get(part)
    if field == "set_points":
        return (st.get("set_points") or {}).get(part)
    if field == "quarters":
        return (st.get("quarters") or {}).get(part)
    if field == "periods":
        return (st.get("periods") or {}).get(part)
    return st.get(field)


def remaining_clock(m, sport, field, part, st):
    """Ile jeszcze punktów/goli z tego historycznego meczu w pozostałym czasie (tempo z tej części gry)."""
    clock = st.get("clock") or {}
    pnum = clock.get("period_num")
    rem_frac = clock.get("rem_frac")
    if sport == "basketball":
        parts = m.get("quarters") or {}
        ot = m.get("ot_points") or 0
        last_reg = 4
    else:
        parts = m.get("periods") or {}
        ot = m.get("ot_goals") or 0
        last_reg = 3
    if not parts:
        return None
    if field in ("quarters", "periods") and part:
        if pnum is None:
            return None
        if pnum > part:
            return 0.0
        if pnum < part:
            return float(parts.get(part) or 0)
        if rem_frac is None:
            return None
        return rem_frac * float(parts.get(part) or 0)
    if field == "h2":
        if pnum is None:
            return None
        if pnum <= 2:
            return float(parts.get(3) or 0) + float(parts.get(4) or 0)
        if pnum == 3:
            f = 0.0 if rem_frac is None else rem_frac
            return f * float(parts.get(3) or 0) + float(parts.get(4) or 0)
        if rem_frac is None:
            return None
        return rem_frac * float(parts.get(4) or 0)
    if pnum is None:
        return None
    f = 0.0 if rem_frac is None else rem_frac
    rem = f * float(parts.get(pnum) or 0)
    for j in range(pnum + 1, last_reg + 1):
        rem += float(parts.get(j) or 0)
    if field == "goals_final" or (sport == "basketball" and field == "points"):
        rem += float(ot or 0)
    return rem


def remaining_sets(m, sport, field, part, st):
    """Ile jeszcze gemów/punktów/setów z historii, licząc od bieżącego seta i jego wyniku."""
    if sport == "volleyball":
        smap = m.get("set_points") or {}
        cur_map = st.get("set_points") or {}
    else:
        smap = m.get("set_games") or {}
        cur_map = st.get("set_games") or {}
    if cur_map:
        S = max(cur_map)
        C = cur_map.get(S) or 0
    else:
        S = (st.get("sets") or 0) + 1
        C = 0
    if field in ("set_points", "set_games") and part:
        hv = smap.get(part)
        if hv is None:
            return None
        if part < S:
            return 0.0
        if part > S:
            return float(hv)
        if hv < C:
            return None
        return float(hv - C)
    if field == "sets":
        hs = m.get("sets")
        if hs is None:
            return None
        started = S
        completed = st.get("sets") if st.get("sets") is not None else max(0, S - 1)
        if hs < started:
            return None
        return float(hs - completed)
    if field in ("points", "games"):
        if S not in smap or smap[S] < C:
            return None
        rem = smap[S] - C
        for j in smap:
            if j > S:
                rem += smap[j]
        return float(rem)
    return None


def remaining_from_hist(m, sport, field, part, st):
    if sport in ("basketball", "hockey"):
        return remaining_clock(m, sport, field, part, st)
    if sport in ("volleyball", "tennis"):
        return remaining_sets(m, sport, field, part, st)
    return None


def live_over_count(matches, field, part, line, st, sport):
    """k/n: w ilu historycznych meczach w pozostałym czasie padło dość, by przebić linię."""
    current = _current_for(st, field, part) or 0
    k = n = 0
    for m in matches or []:
        rem = remaining_from_hist(m, sport, field, part, st)
        if rem is None:
            continue
        n += 1
        if current + rem > line:
            k += 1
    return k, n


def _fmt_need(v):
    if v is None:
        return "?"
    if abs(v - int(v)) < 1e-9:
        return str(int(v))
    return ("%g" % v).replace(".", ",")


def live_why(k, n, ev, st, field, part, line, current, sport):
    need = line - (current or 0)
    clock = (st or {}).get("clock") or {}
    rem = clock.get("rem_sec")
    pnum = clock.get("period_num")
    where = ""
    if sport == "basketball" and pnum:
        where = "w %d. kwarcie" % pnum
    elif sport == "hockey" and pnum:
        where = "w %d. tercji" % pnum
    elif sport in ("volleyball", "tennis") and (part or pnum):
        where = "w %d. secie" % (part or pnum)
    bits = []
    if rem is not None and where:
        mm, ss = divmod(int(rem), 60)
        bits.append("zostało %d:%02d %s" % (mm, ss, where))
    elif clock.get("intermission") and where:
        bits.append("przerwa po %s" % where)
    elif clock.get("time_unknown"):
        bits.append("nie mam dokładnego czasu, liczę tylko pełne pozostałe części gry")
    elif where:
        bits.append("w tym momencie %s" % where)
    if need > 0:
        bits.append("trzeba jeszcze ponad %s" % _fmt_need(need))
    extra = (", ".join(bits) + ", ") if bits else ""
    return ("w %d z %d meczów tych drużyn w pozostałym czasie (%s) padło tyle, ile trzeba do over %s" % (
        k, n, extra.rstrip(", "), _fmt_need(line)))


def make_line(home_ms, away_ms, offer, ev, st):
    field, part, line = offer["field"], offer["part"], offer["line"]
    if not enough_sides(home_ms, away_ms, field, part):
        return None
    current = None
    live = (ev.get("mode") == "live")
    sport = ev.get("sport")
    if live:
        current = _current_for(st, field, part)
        if current is not None and current > line:
            return None  # zakład już by wygrał — nie typuję
        kh, nh = live_over_count(home_ms, field, part, line, st, sport)
        ka, na = live_over_count(away_ms, field, part, line, st, sport)
    else:
        kh, nh = _count_over(home_ms, field, line, part, None)
        ka, na = _count_over(away_ms, field, line, part, None)
    k, n = kh + ka, nh + na
    if n < MIN_SIDE:
        return None
    ch = emp_chance(k, n)
    if not ch:
        return None
    why = "w %d z %d ostatnich meczów tych drużyn było ponad %s" % (
        k, n, offer["play"].replace("Over ", "", 1))
    if live:
        why = live_why(k, n, ev, st, field, part, line, current, sport)
    ln = {
        "key": offer["key"], "base": offer["base"], "field": field, "part": part,
        "scope": ev.get("sport"), "line": line, "play": offer["play"],
        "chance": ch["p"], "chance_pct": ch["pct"], "frac": ch["frac"],
        "k": k, "n": n, "kh": kh, "nh": nh, "ka": ka, "na": na,
        "why": why, "odds": offer.get("odds"),
        "odds_under": offer.get("odds_under"),
        "current": current,
    }
    if offer.get("odds"):
        omap = {ln["key"]: offer["odds"]}
        if offer.get("odds_under"):
            omap[ln["key"] + "_under"] = offer["odds_under"]
        ln = g.apply_odds_to_line(ln, omap)
    else:
        ln = g.fill_offer(ln)
    ln["summary"] = g.offer_text(ln, why=why)
    return ln


def match_card(home_ms, away_ms, ev, offers):
    """Karta meczu albo None, gdy brak historii / brak linii."""
    sport = ev.get("sport")
    if not has_main_history(home_ms, away_ms, sport):
        return None
    st = current_state(ev) if ev.get("mode") == "live" else current_state({"sport": sport})
    lines = []
    for off in (offers or {}).values():
        ln = make_line(home_ms, away_ms, off, ev, st)
        if ln:
            lines.append(ln)
    if not lines:
        return None
    home = ev.get("home") or ev.get("home_name") or "?"
    away = ev.get("away") or ev.get("away_name") or "?"
    if not isinstance(home, str):
        home = str(home)
    if not isinstance(away, str):
        away = str(away)
    card = {
        "eventId": ev.get("eventId"),
        "home": home, "away": away, "home_name": home, "away_name": away,
        "sport": sport, "sport_pl": SPORT_PL.get(sport, sport),
        "sportId": ev.get("sportId"),
        "mode": ev.get("mode") or "prematch",
        "tournament": ev.get("tournament") or ev.get("league"),
        "category": ev.get("category") or ev.get("country"),
        "start_warsaw": ev.get("start_warsaw"),
        "day_warsaw": ev.get("day_warsaw"),
        "ts": ev.get("ts"),
        "score": ev.get("score") or st.get("score"),
        "period": ev.get("period") or ev.get("periodStatus"),
        "minutes": ev.get("minutes"),
        "remainingPeriodTime": ev.get("remainingPeriodTime") or ev.get("remaining"),
        "time_label": time_label(ev, st),
        "lines": lines,
        "n_home": side_n(home_ms, MAIN_FIELD.get(sport) or "points"),
        "n_away": side_n(away_ms, MAIN_FIELD.get(sport) or "points"),
        "warnings": [],
    }
    g.sanitize_team_names(card)
    return card


def best_rank_line(card):
    cands = []
    for ln in card.get("lines") or []:
        if ln.get("chance") is None or ln["chance"] < P_MIN:
            continue
        odds = ln.get("odds")
        if not odds or odds < MIN_ODDS - 1e-12:
            continue
        if (ln.get("n") or 0) < MIN_SIDE:
            continue
        if ln.get("unreliable") or ln.get("verdict") == "UNRELIABLE":
            continue
        cands.append(ln)
    if not cands:
        return None
    cands.sort(key=lambda x: (
        -(x.get("edge") if x.get("edge") is not None else -99),
        -(x.get("chance") or 0),
        -(x.get("n") or 0),
    ))
    return cands[0]


def rank_cards(cards, limit=MAX_PICKS):
    rows = []
    seen = set()
    for m in cards or []:
        if not m:
            continue
        eid = m.get("eventId")
        uniq = eid if eid is not None else id(m)
        if uniq in seen:
            continue
        ln = best_rank_line(m)
        if not ln:
            continue
        seen.add(uniq)
        row = g._row_from_match_line(m, ln)
        row["sport"] = m.get("sport")
        row["sport_pl"] = m.get("sport_pl")
        row["mode"] = m.get("mode")
        row["time_label"] = m.get("time_label")
        row["summary"] = g.offer_text(ln, why=ln.get("why"),
                                      header="%s – %s" % (m.get("home") or "?", m.get("away") or "?"))
        g.fill_offer(row)
        g.sanitize_team_names(row, m)
        rows.append(row)
    rows.sort(key=lambda r: (
        -(r.get("edge") if r.get("edge") is not None else -99),
        -(r.get("chance") or 0),
        -(r.get("n") or 0),
        r.get("ts") or 0,
    ))
    for i, r in enumerate(rows[:limit], 1):
        r["rank"] = i
    return rows[:limit]


def public_match_card(c):
    if not c:
        return c
    out = g.public_match_card(c) if hasattr(g, "public_match_card") else dict(c)
    out["sport"] = c.get("sport")
    out["sport_pl"] = c.get("sport_pl")
    out["time_label"] = c.get("time_label")
    out["mode"] = c.get("mode")
    for k in ("state_ts", "state_warsaw", "state_age_seconds", "state_label",
              "remainingPeriodTime", "score"):
        if c.get(k) is not None:
            out[k] = c.get(k)
    g.sanitize_team_names(out, c)
    rec = None
    best = best_rank_line(c)
    if best:
        rec = g.offer_text(best, why=best.get("why"))
    else:
        rec = "Nic tu nie typuję przy obecnych kursach (albo jeszcze nie ma kursu ok. 1,30)."
    out["recommend"] = rec
    out["detail"] = {
        "state": c.get("time_label") or "",
        "offer": rec,
        "recommend": rec,
    }
    return out


def public_line_card(c):
    out = g.public_line_card(c) if hasattr(g, "public_line_card") else dict(c)
    out["sport"] = c.get("sport")
    out["sport_pl"] = c.get("sport_pl")
    out["mode"] = c.get("mode")
    out["time_label"] = c.get("time_label")
    for k in ("state_ts", "state_warsaw", "state_age_seconds", "state_label", "score"):
        if c.get(k) is not None:
            out[k] = c.get(k)
    g.sanitize_team_names(out, c)
    return out
