"""Następny gol – twarde sita na top 3 (rywal dziurawej strzeli następnego).
Czyste funkcje nad listą z /api/worst (temp ≥ 40). Nie zmienia dziurawych obron."""
from __future__ import annotations

MIN_LO, MIN_HI = 25, 75
DEAD_MIN = 50


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
        parts.append((w, share))
    if not parts:
        return None
    W = sum(w for w, _ in parts)
    return sum(w * s for w, s in parts) / W


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
    """Udział celnych rywala ≥ 70% i ≥ 3 celne."""
    o = _on_share(live)
    if not o:
        return False
    share, _, b = o
    return share >= 0.70 and b >= 3


def sieve_minute(row):
    mi = parse_minute(row.get("minutes"))
    return mi is not None and MIN_LO <= mi <= MIN_HI


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
    ("minuta 25–75", sieve_minute),
    ("dane LIVE o celnych", sieve_live_on),
    ("nie wyrównany", sieve_balanced),
    ("rywal nie prowadzi 2+", sieve_score_gd),
    ("presja LIVE (celne ≥60% / wyjątek)", sieve_pressure),
    ("silny rywal (≥1,5 gola/mecz lub ekstremalne LIVE)", sieve_strong_rival),
    ("temperatura ze strzałów lub ekstremalne LIVE", sieve_shots),
    ("nie martwy mecz", sieve_dead),
)


def next_goal_score(row, pressure=None):
    """0–100: 50% nowa presja, 20% atak rywala, 15% temp, 15% czas×stan."""
    if pressure is None:
        pressure = next_live_pressure(row.get("live"))
    if pressure is None:
        return None
    comps = [("Presja LIVE", 0.50, float(pressure))]
    ga = (row.get("opp_att") or {}).get("gf_avg")
    if ga is not None:
        comps.append(("Atak rywala", 0.20, min(1.0, float(ga) / 2.0)))
    temp = (row.get("t") or {}).get("temp")
    if temp is not None:
        comps.append(("Temperatura", 0.15, float(temp) / 100.0))
    mi = parse_minute(row.get("minutes"))
    g = leaky_rival_goals(row)
    if mi is not None and g is not None:
        leaky_gf, rival_gf = g
        rywal_gd = rival_gf - leaky_gf
        time_f = max(0.0, (85 - mi) / 60.0)
        state_m = {-1: 1.15, 0: 1.10, 1: 1.00}.get(rywal_gd, 1.0)
        comps.append(("Czas × stan", 0.15, min(1.0, time_f * state_m)))
    W = sum(w for _, w, _ in comps)
    return int(round(100 * sum(w * x for _, w, x in comps) / W))


def _pick(row, pressure, score):
    g = leaky_rival_goals(row) or (None, None)
    leaky_gf, rival_gf = g
    live = row.get("live") or {}
    opp = row.get("opponent") or "?"
    return {
        "team": row.get("team"),
        "opponent": row.get("opponent"),
        "side": row.get("side"),
        "minutes": row.get("minutes"),
        "score": row.get("score"),
        "leaky_gf": leaky_gf,
        "rival_gf": rival_gf,
        "rywal_gd": (None if leaky_gf is None else rival_gf - leaky_gf),
        "live": {
            "on": live.get("on"),
            "cor": live.get("cor"),
            "da": live.get("da"),
        },
        "pressure": int(round(pressure * 100)),
        "temp": (row.get("t") or {}).get("temp"),
        "shots": (row.get("t") or {}).get("shots"),
        "gf_avg": (row.get("opp_att") or {}).get("gf_avg"),
        "next_goal_score": score,
        "tip": "%s — następny gol" % opp,
        "eventId": row.get("eventId"),
        "tournament": row.get("tournament"),
        "category": row.get("category"),
    }


def build_next_goal(lst, max_picks=3):
    """Z listy dziurawych (temp ≥ 40) buduje lejek sit i max 3 picki."""
    rows = list(lst or [])
    funnel = [{"name": "start (temperatura ≥ 40)", "before": len(rows), "after": len(rows)}]
    cur = rows
    for name, fn in SIEVES:
        before = len(cur)
        cur = [r for r in cur if fn(r)]
        funnel.append({"name": name, "before": before, "after": len(cur)})
    scored = []
    for r in cur:
        pr = next_live_pressure(r.get("live"))
        sc = next_goal_score(r, pr)
        if pr is None or sc is None:
            continue
        scored.append(_pick(r, pr, sc))
    scored.sort(key=lambda p: (-p["next_goal_score"], -(p.get("temp") or 0), p.get("team") or ""))
    return {"funnel": funnel, "picks": scored[:max_picks]}
