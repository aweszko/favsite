"""Następny gol – twarde sita top 3 + snapshot typów + settlement + analiza + wnioski.
Czyste funkcje. Nie zmienia dziurawych obron (sortowanie/progi/sekcje)."""
from __future__ import annotations

import math

MIN_LO = 25  # dolny próg top 3; BEZ górnego limitu (gole 85–90+ częste)
DEAD_MIN = 50
LEARN_MIN_SETTLED = 15
LEARN_BUCKET_MIN = 8
LEARN_CAP = 12  # max |korekta| punktów next_goal_score z nauki


def parse_minute(m):
    """HT/przerwa=45, '45+2'=47, '67''=67. Nieznane -> None."""
    if m is None:
        return None
    s = str(m).strip().lower().replace("'", "")
    if s in ("ht", "przerwa", "half time", "halftime"):
        return 45
    if "+" in s:
        left, right = s.split("+", 1)
        base = "".join(ch for ch in left if ch.isdigit())
        extra = "".join(ch for ch in right if ch.isdigit())
        if base:
            return int(base) + (int(extra) if extra else 0)
    d = ""
    for ch in s:
        if ch.isdigit():
            d += ch
        elif d:
            break
    return int(d) if d else None


def parse_score(score):
    if score is None:
        return None
    s = str(score).strip().replace("–", ":").replace("-", ":")
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None


def leaky_rival_goals(row):
    """Z perspektywy dziurawej: (leaky_gf, rival_gf). score w API = home:away."""
    sc = parse_score(row.get("score"))
    if not sc:
        return None
    home, away = sc
    if row.get("side") == "D":
        return home, away
    if row.get("side") == "W":
        return away, home
    return None


def leaky_side_key(side):
    """D = gospodarz dziurawy = home; W = gość = away."""
    return "home" if side == "D" else "away" if side == "W" else None


def rival_side_key(side):
    ls = leaky_side_key(side)
    return None if ls is None else ("away" if ls == "home" else "home")


def _pair(live, key):
    v = (live or {}).get(key)
    if not v or len(v) < 2 or v[0] is None or v[1] is None:
        return None
    try:
        return int(v[0] or 0), int(v[1] or 0)
    except (TypeError, ValueError):
        return None


def next_live_pressure(live):
    """Udział rywala (indeks 1): celne 55%, groźne 30%, rożne 15%. Brakujące pomiń i przeskaluj."""
    weights = (("on", 0.55), ("da", 0.30), ("cor", 0.15))
    parts = []
    for k, w in weights:
        p = _pair(live, k)
        if not p:
            continue
        a, b = p
        share = b / (a + b) if a + b > 0 else 0.5
        parts.append((w, share, k))
    if not parts:
        return None
    W = sum(w for w, _, _ in parts)
    return {
        "share": sum(w * s for w, s, _ in parts) / W,
        "parts": {k: round(s, 4) for w, s, k in parts},
        "weights_used": {k: round(w / W, 4) for w, s, k in parts},
    }


def _on_share(live):
    p = _pair(live, "on")
    if not p:
        return None
    a, b = p
    return (b / (a + b) if a + b > 0 else 0.5), a, b


def _da_share(live):
    p = _pair(live, "da")
    if not p:
        return None
    a, b = p
    return (b / (a + b) if a + b > 0 else 0.5), a, b


def extreme_live(live):
    o = _on_share(live)
    if not o:
        return False
    share, _, b = o
    return share >= 0.70 and b >= 3


def sieve_minute(row):
    """Top 3: minuta ≥ 25. Bez max 75 — końcówka (88', 90+) może wejść przy silnej presji."""
    mi = parse_minute(row.get("minutes"))
    return mi is not None and mi >= MIN_LO


def sieve_live_on(row):
    return _pair(row.get("live"), "on") is not None


def sieve_balanced(row):
    return row.get("balanced") is False


def sieve_score_gd(row):
    g = leaky_rival_goals(row)
    if not g:
        return False
    leaky_gf, rival_gf = g
    return (rival_gf - leaky_gf) < 2


def sieve_pressure(row):
    live = row.get("live")
    o = _on_share(live)
    if not o:
        return False
    share, a, b = o
    if share >= 0.60 and b >= 2:
        return True
    mi = parse_minute(row.get("minutes"))
    d = _da_share(live)
    cor = _pair(live, "cor")
    if (
        a + b <= 2
        and mi is not None
        and mi >= 35
        and d is not None
        and d[0] >= 0.65
        and cor is not None
        and cor[1] >= 2
        and b >= 1
    ):
        return True
    return False


def sieve_strong_rival(row):
    ga = (row.get("opp_att") or {}).get("gf_avg")
    if ga is not None and ga >= 1.5:
        return True
    return extreme_live(row.get("live"))


def sieve_shots(row):
    t = row.get("t") or {}
    if t.get("shots") is True:
        return True
    return extreme_live(row.get("live"))


def sieve_dead(row):
    mi = parse_minute(row.get("minutes"))
    if mi is None or mi < DEAD_MIN:
        return True
    live = row.get("live")
    o = _pair(live, "on")
    d = _pair(live, "da")
    on_sum = (o[0] + o[1]) if o else 0
    da_sum = (d[0] + d[1]) if d else 0
    if on_sum <= 2 and da_sum < 40:
        return False
    return True


SIEVES = (
    ("minuta ≥ 25", sieve_minute),
    ("dane LIVE o celnych", sieve_live_on),
    ("nie wyrównany", sieve_balanced),
    ("rywal nie prowadzi 2+", sieve_score_gd),
    ("presja LIVE (celne ≥60% / wyjątek)", sieve_pressure),
    ("silny rywal (≥1,5 gola/mecz lub ekstremalne LIVE)", sieve_strong_rival),
    ("temperatura ze strzałów lub ekstremalne LIVE", sieve_shots),
    ("nie martwy mecz", sieve_dead),
)


def score_components(row, pressure=None):
    """Składniki scoringu (0–1) przed wagami. pressure = dict z next_live_pressure lub float share."""
    if isinstance(pressure, dict):
        share = pressure.get("share")
    else:
        share = pressure
    if share is None:
        pr = next_live_pressure(row.get("live"))
        share = None if pr is None else pr["share"]
        pressure = pr
    if share is None:
        return None
    comps = [("presja", 0.50, float(share))]
    ga = (row.get("opp_att") or {}).get("gf_avg")
    if ga is not None:
        comps.append(("atak", 0.20, min(1.0, float(ga) / 2.0)))
    temp = (row.get("t") or {}).get("temp")
    if temp is not None:
        comps.append(("temp", 0.15, float(temp) / 100.0))
    mi = parse_minute(row.get("minutes"))
    g = leaky_rival_goals(row)
    time_state = None
    if mi is not None and g is not None:
        leaky_gf, rival_gf = g
        rywal_gd = rival_gf - leaky_gf
        # Zapas na doliczony czas (~98'): przy 88' remaining≈10/90, nie ~0.
        remaining = max(0.0, (98 - mi) / 90.0)
        state_m = {-1: 1.15, 0: 1.10, 1: 1.00}.get(rywal_gd, 1.0)
        # Lekki mnożnik końcówki 80–90+, gdy gra otwarta (nie prowadzenie rywala 2+ — i tak odsiane sitami).
        late = 1.0
        if mi >= 80 and rywal_gd < 2:
            open_game = (share is not None and share >= 0.60) or rywal_gd in (-1, 0)
            if open_game:
                late = 1.20 if mi < 90 else 1.15
        time_state = min(1.0, remaining * state_m * late)
        comps.append(("czas_stan", 0.15, time_state))
    W = sum(w for _, w, _ in comps)
    parts = [{"name": n, "weight": round(w / W * 100), "value": int(round(x * 100))} for n, w, x in comps]
    raw = int(round(100 * sum(w * x for _, w, x in comps) / W))
    return {"raw": raw, "parts": parts, "pressure": pressure if isinstance(pressure, dict) else {"share": share}}


def next_goal_score(row, pressure=None, lessons=None):
    c = score_components(row, pressure)
    if not c:
        return None
    adj = learning_adjustment(row, c, lessons or [])
    return max(0, min(100, c["raw"] + adj["delta"])), c, adj


def passed_sieves(row):
    return [name for name, fn in SIEVES if fn(row)]


def failed_sieves(row):
    return [name for name, fn in SIEVES if not fn(row)]


def _tempo(row):
    """Tempo LIVE vs baseline (~8 celnych/90 i ~80 groźnych/90 = 1.0). Clamp 0.35–2.2."""
    live = row.get("live") or {}
    mi = parse_minute(row.get("minutes"))
    if mi is None:
        mi = 45
    elapsed = max(mi, 1) / 90.0
    parts = []
    on = _pair(live, "on")
    if on:
        rate = (on[0] + on[1]) / elapsed
        parts.append(rate / 8.0)
    da = _pair(live, "da")
    if da:
        rate = (da[0] + da[1]) / elapsed
        parts.append(rate / 80.0)
    if not parts:
        return 1.0
    return max(0.35, min(2.2, sum(parts) / len(parts)))


def hit_probability(row):
    """P(kolejny gol) × P(rywal go strzeli) → int 0–100. Publiczna „Szanse wygranej”."""
    mi = parse_minute(row.get("minutes"))
    if mi is None:
        mi = 45
    remaining = max(0.0, (98 - mi) / 90.0)
    tempo = _tempo(row)
    g = leaky_rival_goals(row)
    if g is None:
        rywal_gd = 0
    else:
        rywal_gd = g[1] - g[0]
    state = {-1: 1.15, 0: 1.10, 1: 1.00}.get(rywal_gd, 1.0)
    pr = next_live_pressure(row.get("live"))
    pressure = 0.50 if pr is None else float(pr["share"])
    late = 1.0
    if mi >= 80 and rywal_gd < 2:
        open_game = (pressure >= 0.60) or rywal_gd in (-1, 0)
        if open_game:
            late = 1.20 if mi < 90 else 1.15
    lam = 2.4 * remaining * tempo * state * late
    p_more = 1.0 - math.exp(-lam) if lam > 0 else 0.0
    p_rival = 0.50 + 0.70 * (pressure - 0.50)
    p_rival = max(0.12, min(0.88, p_rival))
    return int(round(100.0 * p_more * p_rival))


def sieve_closeness(name, row):
    """0–1: 1 = pass; ułamek gdy blisko. Używane do nearest-miss."""
    fn = dict(SIEVES).get(name)
    if fn and fn(row):
        return 1.0
    if name == "minuta ≥ 25":
        mi = parse_minute(row.get("minutes"))
        if mi is None:
            return 0.0
        return max(0.0, min(1.0, mi / float(MIN_LO)))
    if name == "dane LIVE o celnych":
        return 0.0
    if name == "nie wyrównany":
        return 0.0
    if name == "rywal nie prowadzi 2+":
        g = leaky_rival_goals(row)
        if not g:
            return 0.0
        gd = g[1] - g[0]
        if gd < 2:
            return 1.0
        # gd=2 → ~0.35, gd≥4 → 0
        return max(0.0, 1.0 - (gd - 1) / 3.0)
    if name == "presja LIVE (celne ≥60% / wyjątek)":
        live = row.get("live")
        o = _on_share(live)
        if not o:
            return 0.0
        share, a, b = o
        # blisko 60% i ≥2 celnych
        share_c = max(0.0, min(1.0, share / 0.60))
        shots_c = max(0.0, min(1.0, b / 2.0))
        return 0.6 * share_c + 0.4 * shots_c
    if name == "silny rywal (≥1,5 gola/mecz lub ekstremalne LIVE)":
        if extreme_live(row.get("live")):
            return 1.0
        ga = (row.get("opp_att") or {}).get("gf_avg")
        if ga is None:
            return 0.0
        return max(0.0, min(1.0, float(ga) / 1.5))
    if name == "temperatura ze strzałów lub ekstremalne LIVE":
        if extreme_live(row.get("live")):
            return 1.0
        t = row.get("t") or {}
        if t.get("shots") is True:
            return 1.0
        # bez strzałów – lekka bliskość z temp
        temp = t.get("temp")
        if temp is None:
            return 0.0
        return max(0.0, min(0.7, float(temp) / 100.0))
    if name == "nie martwy mecz":
        mi = parse_minute(row.get("minutes"))
        if mi is None or mi < DEAD_MIN:
            return 1.0
        live = row.get("live")
        o = _pair(live, "on")
        d = _pair(live, "da")
        on_sum = (o[0] + o[1]) if o else 0
        da_sum = (d[0] + d[1]) if d else 0
        # im więcej aktywności, tym bliżej przejścia
        on_c = max(0.0, min(1.0, on_sum / 3.0))
        da_c = max(0.0, min(1.0, da_sum / 40.0))
        return 0.5 * on_c + 0.5 * da_c
    return 0.0


def closeness_map(row):
    out = {}
    for name, fn in SIEVES:
        out[name] = 1.0 if fn(row) else sieve_closeness(name, row)
    return out


def _pick(row, pressure, score, comps, adj, *, nearest_miss=False, sieves_failed=None, chance_pct=None):
    g = leaky_rival_goals(row) or (None, None)
    leaky_gf, rival_gf = g
    live = row.get("live") or {}
    opp = row.get("opponent") or "?"
    pr = pressure if isinstance(pressure, dict) else next_live_pressure(live)
    share = pr["share"] if pr else 0.5
    if chance_pct is None:
        chance_pct = hit_probability(row)
    failed = list(sieves_failed) if sieves_failed is not None else failed_sieves(row)
    return {
        "team": row.get("team"),
        "opponent": row.get("opponent"),
        "side": row.get("side"),
        "minutes": row.get("minutes"),
        "score": row.get("score"),
        "leaky_gf": leaky_gf,
        "rival_gf": rival_gf,
        "rywal_gd": (None if leaky_gf is None else rival_gf - leaky_gf),
        "live": {"on": live.get("on"), "cor": live.get("cor"), "da": live.get("da")},
        "pressure": int(round(share * 100)),
        "pressure_share": round(share, 4),
        "pressure_parts": (pr or {}).get("parts"),
        "temp": (row.get("t") or {}).get("temp"),
        "shots": (row.get("t") or {}).get("shots"),
        "gf_avg": (row.get("opp_att") or {}).get("gf_avg"),
        "next_goal_score": score if score is not None else chance_pct,
        "chance_pct": int(chance_pct),
        "score_parts": (comps or {}).get("parts") if comps else [],
        "learning_delta": (adj or {}).get("delta", 0),
        "learning_notes": (adj or {}).get("notes") or [],
        "sieves_passed": passed_sieves(row),
        "sieves_failed": failed,
        "nearest_miss": bool(nearest_miss),
        "tip": "%s — następny gol" % opp,
        "eventId": row.get("eventId"),
        "betradarId": row.get("betradarId"),
        "tournament": row.get("tournament"),
        "category": row.get("category"),
        "balanced": bool(row.get("balanced")),
        "uid": row.get("uid"),
    }


def _annotate_row(row, lessons=None):
    passed = passed_sieves(row)
    failed = failed_sieves(row)
    close = {name: (1.0 if name in passed else sieve_closeness(name, row)) for name, _ in SIEVES}
    chance = hit_probability(row)
    return {
        "eventId": row.get("eventId"),
        "side": row.get("side"),
        "uid": row.get("uid"),
        "chance_pct": chance,
        "sieves_passed": passed,
        "sieves_failed": failed,
        "closeness": {k: round(v, 4) for k, v in close.items()},
        "closeness_sum": round(sum(close.values()), 4),
        "tip": "%s — następny gol" % (row.get("opponent") or "?"),
        "temp": (row.get("t") or {}).get("temp"),
        "minutes": row.get("minutes"),
        "live": {
            "on": (row.get("live") or {}).get("on"),
            "cor": (row.get("live") or {}).get("cor"),
            "da": (row.get("live") or {}).get("da"),
        },
        "nearest_miss": False,
    }


def build_next_goal(lst, max_picks=3, lessons=None):
    """Lejek sit + adnotacje całej listy + max 3 picki (albo 1 nearest-miss)."""
    rows = list(lst or [])
    funnel = [{"name": "start (temperatura ≥ 40)", "before": len(rows), "after": len(rows)}]
    cur = rows
    for name, fn in SIEVES:
        before = len(cur)
        cur = [r for r in cur if fn(r)]
        funnel.append({"name": name, "before": before, "after": len(cur)})

    annotated = []
    for r in rows:
        ann = _annotate_row(r, lessons)
        annotated.append(ann)

    scored = []
    for r in cur:
        pr = next_live_pressure(r.get("live"))
        got = next_goal_score(r, pr, lessons)
        chance = hit_probability(r)
        if got is None:
            # pełne sito wymaga LIVE celnych, więc pressure zwykle jest; fallback
            scored.append(_pick(r, pr, chance, {"parts": []}, {"delta": 0, "notes": []},
                                nearest_miss=False, sieves_failed=[], chance_pct=chance))
            continue
        sc, comps, adj = got
        scored.append(_pick(r, pr, sc, comps, adj, nearest_miss=False, sieves_failed=[], chance_pct=chance))
    scored.sort(key=lambda p: (-p["chance_pct"], -(p.get("temp") or 0), p.get("team") or ""))
    picks = scored[:max_picks]

    if not picks and rows:
        # dokładnie jeden nearest-miss: max closeness_sum, potem chance_pct, temp
        best_i = max(
            range(len(rows)),
            key=lambda i: (
                annotated[i]["closeness_sum"],
                annotated[i]["chance_pct"],
                annotated[i].get("temp") or 0,
            ),
        )
        r = rows[best_i]
        ann = annotated[best_i]
        pr = next_live_pressure(r.get("live"))
        got = next_goal_score(r, pr, lessons)
        chance = ann["chance_pct"]
        if got is None:
            pick = _pick(r, pr, chance, {"parts": []}, {"delta": 0, "notes": []},
                         nearest_miss=True, sieves_failed=ann["sieves_failed"], chance_pct=chance)
        else:
            sc, comps, adj = got
            pick = _pick(r, pr, sc, comps, adj, nearest_miss=True,
                         sieves_failed=ann["sieves_failed"], chance_pct=chance)
        picks = [pick]
        annotated[best_i] = dict(ann)
        annotated[best_i]["nearest_miss"] = True

    return {"funnel": funnel, "picks": picks, "list": annotated}

def snapshot_from_pick(pick, time_warsaw):
    """Pełny snapshot w chwili typowania (do analizy retrospectivy)."""
    sc = parse_score(pick.get("score")) or (0, 0)
    return {
        "eventId": pick.get("eventId"),
        "betradarId": pick.get("betradarId"),
        "match": "%s – %s" % (pick.get("team") or "?", pick.get("opponent") or "?"),
        "team": pick.get("team"),
        "opponent": pick.get("opponent"),
        "tip": pick.get("tip") or ("%s — następny gol" % (pick.get("opponent") or "?")),
        "side": pick.get("side"),
        "leaky_side": leaky_side_key(pick.get("side")),
        "rival_side": rival_side_key(pick.get("side")),
        "tournament": pick.get("tournament"),
        "category": pick.get("category"),
        "minute": parse_minute(pick.get("minutes")),
        "minutes_raw": pick.get("minutes"),
        "score": pick.get("score"),
        "score_h": sc[0],
        "score_a": sc[1],
        "leaky_gf": pick.get("leaky_gf"),
        "rival_gf": pick.get("rival_gf"),
        "rywal_gd": pick.get("rywal_gd"),
        "live": pick.get("live"),
        "pressure": pick.get("pressure"),
        "pressure_share": pick.get("pressure_share"),
        "pressure_parts": pick.get("pressure_parts"),
        "temp": pick.get("temp"),
        "shots": pick.get("shots"),
        "gf_avg": pick.get("gf_avg"),
        "next_goal_score": pick.get("next_goal_score"),
        "score_parts": pick.get("score_parts"),
        "learning_delta": pick.get("learning_delta", 0),
        "sieves_passed": pick.get("sieves_passed") or [],
        "sieves_failed": pick.get("sieves_failed") or [],
        "nearest_miss": bool(pick.get("nearest_miss")),
        "chance_pct": pick.get("chance_pct"),
        "balanced": False,
        "time_warsaw": time_warsaw,
        "status": "pending",
        "result": None,
        "goal_minute": None,
        "goal_side": None,
        "final": None,
        "settled_warsaw": None,
        "cause": None,
        "analysis": None,
    }


# ---------- settlement ----------
def _tl_events(tl):
    return [e for e in ((tl or {}).get("events") or []) if not e.get("disabled")]


def _tl_ended(tl):
    m = (tl or {}).get("match") or {}
    st = m.get("status") or {}
    return (
        st.get("_id") == 100
        or (m.get("timeinfo") or {}).get("ended") not in (None, "")
        or m.get("matchstatus") in ("result", "ended")
        or any(e.get("type") == "match_ended" for e in _tl_events(tl))
    )


def _tl_score(tl):
    r = ((tl or {}).get("match") or {}).get("result") or {}
    try:
        h, a = int(r.get("home")), int(r.get("away"))
        return h, a
    except (TypeError, ValueError):
        pass
    gs = [e for e in _tl_events(tl) if e.get("type") == "goal" and e.get("result")]
    if gs:
        g = gs[-1]["result"]
        try:
            return int(g.get("home") or 0), int(g.get("away") or 0)
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def settle_tip(entry, tl):
    """Rozlicza typ „rywal strzeli następnego gola”.

    Konsekwentna reguła minuty: bierzemy PIERWSZY gol z timeline, którego minuta (time)
    jest > minute tipu. Gole z minutą ≤ tipu są ignorowane (nawet jeśli wynik w tipie
    jeszcze ich nie zawierał – chroni przed lagiem score). Gdy gol nie ma time, używamy
    wzrostu wyniku (home+away > tip) jako fallback tylko jeśli nie było wcześniejszego
    kandydata z minutą.

    Win = zmiana wyniku na korzyść rywala (w tym samobój dziurawej).
    Loss = gol dziurawej albo koniec meczu bez kolejnego gola.
    Pending = mecz trwa i brak gola po tipie.
    """
    tip_min = entry.get("minute")
    if tip_min is None:
        tip_min = -1
    h0 = int(entry.get("score_h") or 0)
    a0 = int(entry.get("score_a") or 0)
    tot0 = h0 + a0
    rival = entry.get("rival_side")
    leaky = entry.get("leaky_side")
    chosen = None  # (minute, benefiting_side, gh, ga, owngoal)
    for e in _tl_events(tl):
        if e.get("type") != "goal" or not e.get("result"):
            continue
        t = _to_int(e.get("time"))
        gh = _to_int(e["result"].get("home"))
        ga = _to_int(e["result"].get("away"))
        if gh is None or ga is None:
            continue
        if t is not None and t <= tip_min:
            continue
        if gh + ga <= tot0:
            continue
        if gh > h0 and ga == a0:
            benef = "home"
        elif ga > a0 and gh == h0:
            benef = "away"
        else:
            tm = e.get("team")
            if e.get("owngoal") and tm in ("home", "away"):
                benef = "away" if tm == "home" else "home"
            else:
                benef = tm if tm in ("home", "away") else None
        if t is None and chosen is not None:
            continue
        if chosen is None or (t is not None and (chosen[0] is None or t < chosen[0])):
            chosen = (t, benef, gh, ga, bool(e.get("owngoal")))
            if t is not None:
                # pierwszy z minutą > tip – bierzemy i kończymy skan w kolejności timeline
                break
    ended = _tl_ended(tl)
    fh, fa = _tl_score(tl)
    final = "%d:%d" % (fh, fa) if ended else None
    if chosen is not None:
        t, benef, gh, ga, own = chosen
        if benef == rival:
            status = "win"
        elif benef == leaky:
            status = "loss"
        else:
            status = "loss"
        return {
            "status": status,
            "result": status,
            "goal_minute": t,
            "goal_side": benef,
            "owngoal": own,
            "final": final or ("%d:%d" % (gh, ga)),
            "pending": False,
        }
    if ended:
        return {
            "status": "loss",
            "result": "loss",
            "goal_minute": None,
            "goal_side": None,
            "owngoal": False,
            "final": final,
            "pending": False,
            "no_goal": True,
        }
    return {"status": "pending", "result": None, "pending": True, "final": None}


# ---------- analysis ----------
def analyze_tip(entry, settlement=None):
    """Rzetelna rozpiska A/B/C/D po polsku."""
    st = settlement or {}
    status = st.get("status") or entry.get("status") or "pending"
    mi = entry.get("minute")
    pr = entry.get("pressure_share")
    if pr is None and entry.get("pressure") is not None:
        pr = (entry.get("pressure") or 0) / 100.0
    temp = entry.get("temp")
    gd = entry.get("rywal_gd")
    live = entry.get("live") or {}
    on = live.get("on") or [None, None]
    cor = live.get("cor") or [None, None]
    da = live.get("da") or [None, None]
    shots = entry.get("shots")
    gf = entry.get("gf_avg")

    A = (
        "W chwili typowania: %s' wynik home:away %s (dziurawa %s:%s, rywal_gd=%s). "
        "LIVE celne %s–%s, rożne %s–%s, groźne %s–%s. Presja LIVE (55/30/15) %s%%, "
        "temp. obrony %s%s, atak rywala %s gola/mecz. Szanse wygranej: %s%%."
        % (
            mi if mi is not None else "?",
            entry.get("score") or "?",
            entry.get("leaky_gf"),
            entry.get("rival_gf"),
            gd,
            on[0],
            on[1],
            cor[0],
            cor[1],
            da[0],
            da[1],
            entry.get("pressure") if entry.get("pressure") is not None else "?",
            temp if temp is not None else "?",
            "" if shots else " (bez historycznych strzałów)",
            ("%.1f" % gf).replace(".", ",") if gf is not None else "?",
            entry.get("next_goal_score"),
        )
    )

    if status == "pending":
        B = "Mecz jeszcze trwa albo brak rozliczenia – nie było jeszcze gola po minucie typowania."
        C = {"code": "pending", "text": "Brak werdyktu – czekamy na następnego gola albo koniec meczu."}
        D = "Za wcześnie na kontrfaktyczne sito."
    elif st.get("no_goal") or (status == "loss" and st.get("goal_minute") is None and st.get("goal_side") is None):
        B = "Do końca meczu nie padł żaden gol po minucie typowania (wynik końcowy %s)." % (st.get("final") or entry.get("final") or "?")
        reasons = []
        if mi is not None and mi >= 70:
            reasons.append("mało czasu do końca (typ po 70.)")
        on_sum = (on[0] or 0) + (on[1] or 0) if on[0] is not None else None
        da_sum = (da[0] or 0) + (da[1] or 0) if da[0] is not None else None
        if on_sum is not None and on_sum <= 3:
            reasons.append("niskie tempo (mało celnych w meczu)")
        if da_sum is not None and da_sum < 50 and (mi or 0) >= 55:
            reasons.append("słabe tempo groźnych ataków w późnej fazie")
        if gd == 1:
            reasons.append("rywal prowadził 1 bramką – częsty scenariusz „usiadł i dowiózł”")
        if not reasons:
            reasons.append("mecz domknął się bez kolejnego gola mimo sygnałów LIVE")
        C = {"code": "no_goal", "text": "Pudło „brak gola”: " + "; ".join(reasons) + "."}
        D = (
            "Kontrfakt: przy typie po %s' i rywal_gd≥1 warto było mocniej ważyć ryzyko „usiadł i dowiózł” / martwego tempa – nie samą minutę."
            % (mi if mi is not None else "?")
            if (mi or 0) >= 80 or gd == 1
            else "Kontrfakt: twarde sita przepuściły typ zasadnie; problemem był brak zdarzenia, nie mylący kierunek."
        )
    elif status == "loss":
        B = (
            "Następny gol po typie: minuta %s, korzyść strony %s (dziurawa strzeliła / wynik poszedł w jej stronę). Końcowy %s."
            % (st.get("goal_minute"), "dziurawej" if st.get("goal_side") == leaky_side_key(entry.get("side")) or st.get("goal_side") == entry.get("leaky_side") else st.get("goal_side"), st.get("final") or "?")
        )
        reasons = []
        o_share = None
        if on[0] is not None and on[1] is not None and (on[0] + on[1]) > 0:
            o_share = on[1] / (on[0] + on[1])
        c_share = None
        if cor[0] is not None and cor[1] is not None and (cor[0] + cor[1]) > 0:
            c_share = cor[1] / (cor[0] + cor[1])
        if c_share is not None and o_share is not None and c_share >= 0.6 and o_share < 0.55:
            reasons.append("presja LIVE myliła – dużo rożnych rywala przy słabszych celnych")
        if on[0] is not None and on[1] is not None and on[0] >= on[1]:
            reasons.append("dziurawa sama miała ≥ celnych co rywal – sama atakowała mimo temperatury")
        if gd == 1:
            reasons.append("stan wyniku (rywal +1) zachęcał gonioną dziurawą do ryzyka")
        if gd == -1:
            reasons.append("dziurawa prowadziła – mogła dostać kontrę mimo presji rywala")
        if not reasons:
            reasons.append("kierunek gola przeciwny do typowania mimo sygnałów")
        C = {"code": "leaky_scored", "text": "Pudło „dziurawa strzeliła”: " + "; ".join(reasons) + "."}
        D = (
            "Kontrfakt: sito presji (wymóg ≥60% celnych i ≥2 celne) powinno było zatrzymać ten typ – celne nie dominowały."
            if o_share is not None and o_share < 0.60
            else "Kontrfakt: sita nie blokowały; warto ważyć stan wyniku mocniej gdy dziurawa goni."
        )
    else:  # win
        B = (
            "Następny gol po typie: minuta %s na korzyść rywala%s. Wynik końcowy %s."
            % (
                st.get("goal_minute"),
                " (samobój dziurawej)" if st.get("owngoal") else "",
                st.get("final") or "?",
            )
        )
        goods = []
        if pr is not None and pr >= 0.7:
            goods.append("silna dominacja LIVE (presja ≥70%)")
        if on[1] is not None and on[0] is not None and on[1] >= on[0] + 2:
            goods.append("wyraźna przewaga celnych rywala")
        if gd == -1:
            goods.append("dziurawa prowadziła 1 – typowy układ pod gola rywala przy presji")
        if gd == 0:
            goods.append("remis – obie strony otwarte, rywal domykał")
        if temp is not None and temp >= 60:
            goods.append("wysoka temperatura obrony")
        if mi is not None and mi <= 60:
            goods.append("wystarczająco dużo czasu do końca")
        if not goods:
            goods.append("sygnały scoingu i sit były spójne z przebiegiem")
        C = {"code": "win", "text": "Trafienie: " + "; ".join(goods) + "."}
        D = "Kontrfakt: twarde sita słusznie wpuściły typ – nie widać powodu blokady ex post."

    return {
        "A_snapshot": A,
        "B_outcome": B,
        "C_cause": C,
        "D_counterfactual": D,
        "status": status,
    }


# ---------- learning ----------
def _bucket_minute(mi):
    if mi is None:
        return None
    if mi < 40:
        return "25-40"
    if mi < 60:
        return "40-60"
    return "60+"


def _bucket_state(gd):
    if gd == -1:
        return "dziurawa_+1"
    if gd == 0:
        return "remis"
    if gd == 1:
        return "rywal_+1"
    return "inny"


def _bucket_pressure(pr):
    if pr is None:
        return None
    if pr >= 0.80:
        return "presja_ge_80"
    if pr >= 0.60:
        return "presja_60_80"
    return "presja_lt_60"


def tip_features(entry):
    pr = entry.get("pressure_share")
    if pr is None and entry.get("pressure") is not None:
        pr = entry["pressure"] / 100.0
    return {
        "minute_bucket": _bucket_minute(entry.get("minute")),
        "state_bucket": _bucket_state(entry.get("rywal_gd")),
        "pressure_bucket": _bucket_pressure(pr),
        "shots": bool(entry.get("shots")),
        "cause": ((entry.get("cause") or {}).get("code") if isinstance(entry.get("cause"), dict) else entry.get("cause")),
    }


def aggregate_insights(log):
    """Statystyki + teksty. Korekty wag tylko gdy settled≥15 i kubełek≥8."""
    settled = [e for e in log if e.get("status") in ("win", "loss")]
    wins = sum(1 for e in settled if e["status"] == "win")
    losses = len(settled) - wins
    buckets = {}

    def add(key, e):
        b = buckets.setdefault(key, {"n": 0, "wins": 0, "losses": 0})
        b["n"] += 1
        if e["status"] == "win":
            b["wins"] += 1
        else:
            b["losses"] += 1

    for e in settled:
        f = tip_features(e)
        if f["minute_bucket"]:
            add("minuta_" + f["minute_bucket"], e)
        if f["state_bucket"]:
            add("stan_" + f["state_bucket"], e)
        if f["pressure_bucket"]:
            add(f["pressure_bucket"], e)
        add("shots_tak" if f["shots"] else "shots_nie_ekstremalne_LIVE", e)
        if e["status"] == "loss":
            code = f.get("cause") or ("no_goal" if e.get("no_goal") or e.get("goal_minute") is None else "leaky_scored")
            add("pudlo_" + code, e)

    texts = []
    if len(settled) < LEARN_MIN_SETTLED:
        texts.append(
            "Próba za mała, żeby zmieniać reguły: rozliczonych %d/%d (próg %d). Poniżej tylko surowe liczby."
            % (len(settled), LEARN_MIN_SETTLED, LEARN_MIN_SETTLED)
        )
    rate = round(100.0 * wins / len(settled), 1) if settled else None
    texts.append(
        "Skuteczność ogółem: %dW / %dP%s."
        % (wins, losses, ("" if rate is None else " · %s%%" % str(rate).replace(".", ",")))
    )
    labels = {
        "minuta_25-40": "minuta 25–40",
        "minuta_40-60": "minuta 40–60",
        "minuta_60+": "minuta 60+",
        "stan_dziurawa_+1": "dziurawa prowadzi 1",
        "stan_remis": "remis",
        "stan_rywal_+1": "rywal prowadzi 1",
        "presja_ge_80": "presja ≥ 80%",
        "presja_60_80": "presja 60–80%",
        "shots_tak": "z historycznymi strzałami",
        "shots_nie_ekstremalne_LIVE": "bez historycznych strzałów (ekstremalne LIVE)",
        "pudlo_no_goal": "pudło: brak gola",
        "pudlo_leaky_scored": "pudło: gol dziurawej",
    }
    for k, b in sorted(buckets.items()):
        lab = labels.get(k, k)
        texts.append("%s: %d/%d." % (lab, b["wins"], b["n"]))

    lessons = []
    if len(settled) >= LEARN_MIN_SETTLED:
        for k, b in buckets.items():
            if b["n"] < LEARN_BUCKET_MIN:
                continue
            rate_b = b["wins"] / b["n"]
            if rate_b < 0.35:
                lessons.append(
                    {
                        "bucket": k,
                        "n": b["n"],
                        "wins": b["wins"],
                        "rate": round(100 * rate_b, 1),
                        "delta": -8,
                        "why": "skuteczność < 35%% na ≥%d próbach – lekka kara scoringu" % LEARN_BUCKET_MIN,
                    }
                )
            elif rate_b > 0.75:
                lessons.append(
                    {
                        "bucket": k,
                        "n": b["n"],
                        "wins": b["wins"],
                        "rate": round(100 * rate_b, 1),
                        "delta": 5,
                        "why": "skuteczność > 75%% na ≥%d próbach – lekki bonus scoringu" % LEARN_BUCKET_MIN,
                    }
                )
        if lessons:
            texts.append(
                "Zastosowano lekkie korekty scoringu (nie sit) na podstawie ≥%d rozliczonych: %s."
                % (LEARN_MIN_SETTLED, "; ".join("%s %+d (n=%d)" % (L["bucket"], L["delta"], L["n"]) for L in lessons))
            )
        else:
            texts.append("≥%d rozliczonych, ale żaden kubełek nie ma ≥%d prób z wyraźnym odstępstwem – bez korekt wag." % (LEARN_MIN_SETTLED, LEARN_BUCKET_MIN))

    return {
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "pending": sum(1 for e in log if e.get("status") == "pending"),
        "hit_rate": rate,
        "buckets": buckets,
        "texts": texts,
        "lessons": lessons,
        "sample_too_small": len(settled) < LEARN_MIN_SETTLED,
    }


def learning_adjustment(row, comps, lessons):
    """Lekka korekta next_goal_score z lekcji; hard sita nietknięte. Cap ±LEARN_CAP."""
    if not lessons:
        return {"delta": 0, "notes": []}
    # zbuduj fake entry features z row
    g = leaky_rival_goals(row)
    gd = None if not g else g[1] - g[0]
    pr = comps.get("pressure") if isinstance(comps, dict) else None
    share = pr.get("share") if isinstance(pr, dict) else None
    fake = {
        "minute": parse_minute(row.get("minutes")),
        "rywal_gd": gd,
        "pressure_share": share,
        "shots": (row.get("t") or {}).get("shots"),
    }
    f = tip_features(fake)
    keys = []
    if f["minute_bucket"]:
        keys.append("minuta_" + f["minute_bucket"])
    if f["state_bucket"]:
        keys.append("stan_" + f["state_bucket"])
    if f["pressure_bucket"]:
        keys.append(f["pressure_bucket"])
    keys.append("shots_tak" if f["shots"] else "shots_nie_ekstremalne_LIVE")
    delta = 0
    notes = []
    by = {L["bucket"]: L for L in lessons}
    for k in keys:
        L = by.get(k)
        if not L:
            continue
        delta += L.get("delta", 0)
        notes.append("%s %+d (n=%d, %s%%)" % (k, L["delta"], L["n"], L.get("rate")))
    if delta > LEARN_CAP:
        delta = LEARN_CAP
    if delta < -LEARN_CAP:
        delta = -LEARN_CAP
    return {"delta": delta, "notes": notes}


def history_view(log, limit=40):
    out = []
    for e in reversed(list(log)[-limit:]):
        out.append(
            {
                "id": e.get("id") or e.get("eventId"),
                "eventId": e.get("eventId"),
                "time_warsaw": e.get("time_warsaw"),
                "match": e.get("match"),
                "tip": e.get("tip"),
                "opponent": e.get("opponent"),
                "minute": e.get("minute"),
                "score": e.get("score"),
                "status": e.get("status") or "pending",
                "goal_minute": e.get("goal_minute"),
                "final": e.get("final"),
                "next_goal_score": e.get("next_goal_score"),
            }
        )
    return out


def detail_view(entry):
    an = entry.get("analysis") or analyze_tip(entry)
    return {
        "entry": entry,
        "analysis": an,
        "snapshot": {
            "minute": entry.get("minute"),
            "score": entry.get("score"),
            "leaky_gf": entry.get("leaky_gf"),
            "rival_gf": entry.get("rival_gf"),
            "live": entry.get("live"),
            "pressure": entry.get("pressure"),
            "pressure_parts": entry.get("pressure_parts"),
            "temp": entry.get("temp"),
            "shots": entry.get("shots"),
            "gf_avg": entry.get("gf_avg"),
            "next_goal_score": entry.get("next_goal_score"),
            "score_parts": entry.get("score_parts"),
            "sieves_passed": entry.get("sieves_passed"),
        },
    }
