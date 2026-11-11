# -*- coding: utf-8 -*-
"""Overy goli z minut strzelonych i straconych.

Czysta logika (bez sieci). Szansa = częstość z ost. meczów (wygładzenie Laplace/Beta(1,1))
łączona z Poissonem ze średnich goli w danym okresie. Przy warunku live — szansa warunkowa.
Nic nie jest zmyślane: brak danych = brak typów.
"""
from __future__ import annotations
import math

BUCKETS = ("0-15", "16-30", "31-45+", "46-60", "61-75", "76-90+")
MIN_N = 8
P_MIN = 0.70
LASTX_N = 15
W_EMP, W_POIS = 0.55, 0.45
MAX_PICKS = 10
# Mecze z HOJNJEGO okna (dziś+jutro), jeden typ na mecz.


def bucket(minute, injury=0):
    try:
        m = int(minute)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    if m <= 15:
        return "0-15"
    if m <= 30:
        return "16-30"
    if m <= 45:
        return "31-45+"
    if m <= 60:
        return "46-60"
    if m <= 75:
        return "61-75"
    return "76-90+"


def half_of(minute):
    try:
        m = int(minute)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return 1 if m <= 45 else 2


def pois_pmf(k, mu):
    if k < 0:
        return 0.0
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))


def pois_over(line, mu):
    """P(X > line) dla linii .5."""
    if mu is None or mu < 0:
        return None
    return 1.0 - sum(pois_pmf(i, mu) for i in range(0, int(math.floor(line)) + 1))


def laplace(k, n, a=1.0, b=1.0):
    """Średnia posterior Beta(a,b) dla częstości k/n. None gdy brak próby."""
    if n is None or n <= 0 or k is None or k < 0:
        return None
    return (k + a) / (n + a + b)


def combine(p_emp, p_pois, w_emp=W_EMP, w_pois=W_POIS):
    vals = [(p, w) for p, w in ((p_emp, w_emp), (p_pois, w_pois)) if p is not None]
    if not vals:
        return None
    return sum(p * w for p, w in vals) / sum(w for _, w in vals)


def frac(k, n):
    if n is None or n <= 0 or k is None:
        return None
    return "%d/%d" % (int(k), int(n))


def pct(p):
    if p is None:
        return None
    return int(round(100 * p))


def extract_goals(events):
    """Cele z timeline: minuta, połowa, kubełek, strona która dostała gola (z przyrostu result)."""
    raw = []
    for e in events or []:
        if e.get("disabled") or e.get("type") != "goal":
            continue
        raw.append(e)
    raw.sort(key=lambda e: (e.get("seconds") if e.get("seconds") is not None else 10**9,
                            e.get("uts") or 0))
    prev = (0, 0)
    out = []
    for e in raw:
        res = e.get("result") or {}
        try:
            rh, ra = int(res["home"]), int(res["away"])
        except (KeyError, TypeError, ValueError):
            side = e.get("team")
            if side not in ("home", "away"):
                continue
            if e.get("owngoal"):
                side = "away" if side == "home" else "home"
            rh = prev[0] + (1 if side == "home" else 0)
            ra = prev[1] + (1 if side == "away" else 0)
        if rh == prev[0] + 1 and ra == prev[1]:
            scored = "home"
        elif ra == prev[1] + 1 and rh == prev[0]:
            scored = "away"
        else:
            # niespójny result — pomiń ten wpis
            prev = (rh, ra) if rh is not None else prev
            continue
        try:
            minute = int(e.get("time"))
        except (TypeError, ValueError):
            continue
        if minute <= 0:
            continue
        try:
            injury = int(e.get("injurytime") or 0)
        except (TypeError, ValueError):
            injury = 0
        out.append({
            "minute": minute, "injury": injury, "half": 1 if minute <= 45 else 2,
            "bucket": bucket(minute), "scored": scored,
            "owngoal": bool(e.get("owngoal")),
        })
        prev = (rh, ra)
    return out, prev


def match_view(uid, is_home, goals, ft=None, p1=None, timeline_ok=True, mid=None, opp_uid=None, uts=None):
    """Jedno spojrzenie drużyny na mecz: gole strzelone/stracone z minutami."""
    my, opp = ("home", "away") if is_home else ("away", "home")
    scored = [g for g in goals if g["scored"] == my]
    conceded = [g for g in goals if g["scored"] == opp]
    if p1 and p1[0] is not None and p1[1] is not None:
        ht_h, ht_a = int(p1[0]), int(p1[1])
    else:
        ht_h = sum(1 for g in goals if g["half"] == 1 and g["scored"] == "home")
        ht_a = sum(1 for g in goals if g["half"] == 1 and g["scored"] == "away")
    if ft and ft[0] is not None and ft[1] is not None:
        ft_h, ft_a = int(ft[0]), int(ft[1])
    else:
        ft_h = sum(1 for g in goals if g["scored"] == "home")
        ft_a = sum(1 for g in goals if g["scored"] == "away")
    gf_ht, ga_ht = (ht_h, ht_a) if is_home else (ht_a, ht_h)
    gf_ft, ga_ft = (ft_h, ft_a) if is_home else (ft_a, ft_h)
    tot_2h = (ft_h + ft_a) - (ht_h + ht_a)
    first = min((g["minute"] for g in scored), default=None)
    first_match = min((g["minute"] for g in goals), default=None)
    return {
        "mid": mid, "uid": uid, "opp_uid": opp_uid, "is_home": is_home, "uts": uts,
        "goals_mismatch": bool(ft and ft[0] is not None and ft[1] is not None and goals is not None and (ft_h + ft_a) != len(goals)),
        "timeline_ok": bool(timeline_ok) and not (ft and ft[0] is not None and ft[1] is not None and goals is not None and (ft_h + ft_a) != len(goals)),
        "gf_ht": gf_ht, "ga_ht": ga_ht, "gf": gf_ft, "ga": ga_ft,
        "ht_home": ht_h, "ht_away": ht_a, "ft_home": ft_h, "ft_away": ft_a,
        "tot": gf_ft + ga_ft, "tot_1h": ht_h + ht_a, "tot_2h": tot_2h,
        "scored": scored, "conceded": conceded, "goals": goals,
        "gf_1h": sum(1 for g in scored if g["half"] == 1),
        "gf_2h": sum(1 for g in scored if g["half"] == 2),
        "ga_1h": sum(1 for g in conceded if g["half"] == 1),
        "ga_2h": sum(1 for g in conceded if g["half"] == 2),
        "gf_late": sum(1 for g in scored if g["bucket"] == "76-90+"),
        "ga_late": sum(1 for g in conceded if g["bucket"] == "76-90+"),
        "tot_late": sum(1 for g in goals if g["bucket"] == "76-90+"),
        "any_2h": tot_2h >= 1, "any_late": any(g["bucket"] == "76-90+" for g in goals),
        "scored_2h": sum(1 for g in scored if g["half"] == 2) >= 1,
        "first": first, "first_match": first_match,
        "trailing_ht": gf_ht < ga_ht, "leading_ht": gf_ht > ga_ht, "level_ht": gf_ht == ga_ht,
        "ht_00": ht_h == 0 and ht_a == 0,
        "ht_le1": (ht_h + ht_a) <= 1,  # 0:0, 1:0, 0:1
    }


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def team_profile(uid, matches, name=None):
    """Agregat z ost. 10–15 meczów z minutami (albo choćby połowami z p1)."""
    ms = [m for m in matches if m]
    n = len(ms)
    tl = [m for m in ms if m.get("timeline_ok") and (m.get("goals") is not None)]
    # nawet bez pełnych minut, p1/ft wystarcza do over 0.5 2H / over 1.5
    def cnt(pred):
        ok = [m for m in ms if pred(m) is not None]
        return sum(1 for m in ok if pred(m)), len(ok)

    g2, n2 = cnt(lambda m: m.get("any_2h"))
    over15, n15 = cnt(lambda m: (m.get("tot") is not None) and m["tot"] >= 2)
    late, nl = cnt(lambda m: m.get("any_late") if m.get("timeline_ok") else None)
    s2, ns2 = cnt(lambda m: m.get("scored_2h"))
    no1, nn1 = cnt(lambda m: m.get("gf_1h") == 0 if m.get("gf_1h") is not None else (m.get("gf_ht") == 0))
    # warunek live: do przerwy 0:0 albo łącznie ≤1 gol
    cond = [m for m in ms if m.get("ht_le1")]
    cond00 = [m for m in ms if m.get("ht_00")]
    c2, nc2 = (sum(1 for m in cond if m.get("any_2h")), len(cond)) if cond else (0, 0)
    c2_00, nc2_00 = (sum(1 for m in cond00 if m.get("any_2h")), len(cond00)) if cond00 else (0, 0)

    def bucket_counts(key):
        out = {b: 0 for b in BUCKETS}
        tot = 0
        src = tl if tl else []
        for m in src:
            for g in m.get(key) or []:
                b = g.get("bucket")
                if b in out:
                    out[b] += 1
                    tot += 1
        return out, tot

    sc_b, sc_n = bucket_counts("scored")
    cc_b, cc_n = bucket_counts("conceded")
    firsts = [m["first"] for m in ms if m.get("first") is not None]
    return {
        "uid": uid, "name": name, "n": n, "n_tl": len(tl),
        "any_2h": (g2, n2), "over15": (over15, n15), "any_late": (late, nl),
        "scored_2h": (s2, ns2), "no_goal_1h": (no1, nn1),
        "cond_2h": (c2, nc2), "cond00_2h": (c2_00, nc2_00),
        "lam_tot": _mean([m.get("tot") for m in ms]),
        "lam_2h": _mean([m.get("tot_2h") for m in ms if m.get("tot_2h") is not None]),
        "lam_late": _mean([m.get("tot_late") for m in tl]),
        "lam_gf": _mean([m.get("gf") for m in ms]),
        "lam_gf_2h": _mean([m.get("gf_2h") if m.get("gf_2h") is not None else None for m in ms]),
        "lam_gf_late": _mean([m.get("gf_late") for m in tl]),
        "scored_buckets": sc_b, "conceded_buckets": cc_b,
        "scored_n": sc_n, "conceded_n": cc_n,
        "avg_first": _mean(firsts), "n_first": len(firsts),
        "matches": ms,
    }


def chance_block(k, n, lam, line=0.5):
    """Uczciwa szansa: Laplace z k/n + Poisson P(X>line | λ)."""
    p_raw = (k / n) if (n and n > 0 and k is not None) else None
    p_emp = laplace(k, n) if n and n > 0 else None
    p_pois = pois_over(line, lam) if lam is not None else None
    p = combine(p_emp, p_pois)
    spread = None
    if p_emp is not None and p_pois is not None:
        spread = abs(p_emp - p_pois)
    return {
        "k": k, "n": n, "frac": frac(k, n), "p_raw": p_raw,
        "p_emp": p_emp, "p_pois": p_pois, "p": p,
        "pct": pct(p), "lam": None if lam is None else round(lam, 3),
        "spread": spread,
    }


def _pool(a, b):
    """Sumuje ułamki dwóch drużyn (mecze są rozłączne)."""
    ka, na = a if a else (0, 0)
    kb, nb = b if b else (0, 0)
    return ka + kb, na + nb


def _mean2(a, b):
    xs = [x for x in (a, b) if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _sum2(a, b):
    xs = [x for x in (a, b) if x is not None]
    return sum(xs) if xs else None


def build_markets(home, away):
    """Kandydaci overów dla pary drużyn. Szansa z połączonych prób obu stron."""
    out = []
    # 1) Over 0.5 w 2. połowie (bezwarunkowo i warunkowo: HT ≤1 gola)
    k, n = _pool(home.get("any_2h"), away.get("any_2h"))
    ch = chance_block(k, n, _mean2(home.get("lam_2h"), away.get("lam_2h")), 0.5)
    ck, cn = _pool(home.get("cond_2h"), away.get("cond_2h"))
    ch_c = chance_block(ck, cn, None, 0.5)  # λ warunkowe liczymy niżej, jeśli jest próba
    # Poisson warunkowy: średnia goli 2H w meczach z HT ≤1
    def cond_lam(prof):
        ms = [m for m in (prof.get("matches") or []) if m.get("ht_le1") and m.get("tot_2h") is not None]
        return _mean([m["tot_2h"] for m in ms])
    ch_c = chance_block(ck, cn, _mean2(cond_lam(home), cond_lam(away)), 0.5)
    out.append({
        "key": "over05_2h",
        "play": "Over 0.5 gola w 2. połowie",
        "enter": "jeśli do przerwy 0:0 lub 1:0 (łącznie najwyżej 1 gol)",
        "uncond": ch, "cond": ch_c,
        "use_cond": cn >= MIN_N,
    })
    # 2) Over 1.5 w meczu
    k, n = _pool(home.get("over15"), away.get("over15"))
    lam = _mean2(home.get("lam_tot"), away.get("lam_tot"))
    ch = chance_block(k, n, lam, 1.5)
    # warunek: jeśli do przerwy 0:0 lub 1 gol — wtedy over 1.5 wymaga 2 albo 1 gola w 2H
    # P(tot>=2 | HT≤1) z historii
    def over15_given(prof, pred):
        ms = [m for m in (prof.get("matches") or []) if pred(m) and m.get("tot") is not None]
        return sum(1 for m in ms if m["tot"] >= 2), len(ms), _mean([m["tot"] for m in ms])
    h15 = over15_given(home, lambda m: m.get("ht_le1"))
    a15 = over15_given(away, lambda m: m.get("ht_le1"))
    ck, cn = h15[0] + a15[0], h15[1] + a15[1]
    ch_c = chance_block(ck, cn, _mean2(h15[2], a15[2]), 1.5)
    out.append({
        "key": "over15",
        "play": "Over 1.5 gola w meczu",
        "enter": "jeśli do przerwy 0:0 lub łącznie 1 gol (zostaje do dociągnięcia over 1.5)",
        "uncond": ch, "cond": ch_c,
        "use_cond": cn >= MIN_N,
    })
    # 3) Gol po 75. min (over 0.5 w 76–90+)
    k, n = _pool(home.get("any_late"), away.get("any_late"))
    ch = chance_block(k, n, _mean2(home.get("lam_late"), away.get("lam_late")), 0.5)
    out.append({
        "key": "over05_late",
        "play": "Gol po 75. minucie (over 0.5 w 76–90+)",
        "enter": "jeśli po 75. minucie mecz nadal jest otwarty albo wynik jest niski (0:0 / 1:0 / 0:1)",
        "uncond": ch, "cond": None,
        "use_cond": False,
    })
    # 4) Gol drużyny w 2. połowie — osobno gospodarz i gość
    for side, prof, label in (("home", home, home.get("name") or "gospodarz"),
                              ("away", away, away.get("name") or "gość")):
        k, n = prof.get("scored_2h") or (0, 0)
        ch = chance_block(k, n, prof.get("lam_gf_2h"), 0.5)
        # warunek: jeśli ta drużyna nie strzeliła do przerwy / albo przegrywa / 0:0
        ms = [m for m in (prof.get("matches") or []) if m.get("gf_ht") == 0]
        ck, cn = sum(1 for m in ms if m.get("scored_2h")), len(ms)
        ch_c = chance_block(ck, cn, _mean([m.get("gf_2h") for m in ms]), 0.5)
        out.append({
            "key": "team2h_" + side,
            "play": "Gol drużyny %s w 2. połowie" % label,
            "enter": "jeśli do przerwy %s nie strzeliła (0 goli tej drużyny po 1. połowie)" % label,
            "uncond": ch, "cond": ch_c,
            "use_cond": cn >= MIN_N,
            "team": label, "side": side,
        })
    return out


def _why(market, used):
    k, n, fr = used.get("k"), used.get("n"), used.get("frac")
    key = market["key"]
    if key == "over05_2h":
        if market.get("use_cond"):
            return "gdy do przerwy był najwyżej 1 gol, gol w 2. połowie padł w %s ostatnich meczów obu drużyn" % fr
        return "gol w 2. połowie padł w %s ostatnich meczów obu drużyn" % fr
    if key == "over15":
        if market.get("use_cond"):
            return "przy najwyżej 1 golu do przerwy over 1.5 wchodził w %s ostatnich meczów obu drużyn" % fr
        return "w meczu padały co najmniej 2 gole w %s ostatnich meczów obu drużyn" % fr
    if key == "over05_late":
        return "gol po 75. minucie padł w %s ostatnich meczów obu drużyn z znanymi minutami" % fr
    if key.startswith("team2h_"):
        name = market.get("team") or "ta drużyna"
        if market.get("use_cond"):
            return "%s, gdy nie strzeliła do przerwy, zdobyła gola w 2. połowie w %s swoich meczów" % (name, fr)
        return "%s strzeliła w 2. połowie w %s swoich ostatnich meczów" % (name, fr)
    return "schemat %s w %s meczach" % (fr, n)


def context_warnings(meta):
    """Ostrzeżenia z nazw/rozgrywek (kobiety, młodzież, rezerwy, amatorzy)."""
    if not meta:
        return []
    text = " ".join(str(meta.get(k) or "") for k in
                    ("home", "away", "home_name", "away_name", "tournament", "category"))
    w = []
    if "(K)" in text or "Kobiet" in text or "(W)" in text:
        w.append("piłka kobiet — schemat może być mniej stabilny niż w lidze mężczyzn")
    if any(s in text for s in ("U21", "U19", "U17", "U18", "Młodzież")):
        w.append("młodzież — wyniki bywają bardziej losowe, składy rotują")
    if "(R)" in text or " rezerwy" in text.lower() or text.endswith(" II") or " II " in text or " (R)" in text:
        w.append("rezerwy — składy często rotują")
    if "Am." in text or "Amateur" in text:
        w.append("liga amatorska / semi-pro — ostrożniej z regularnością")
    return w


def _warnings(market, used, home, away, meta=None):
    w = list(context_warnings(meta))
    n = used.get("n") or 0
    if n < 12:
        w.append("próba %d meczów — to nadal mało, traktuj ostrożnie" % n)
    if market.get("use_cond"):
        cu = market.get("cond") or {}
        if (cu.get("n") or 0) < 12:
            w.append("szansa warunkowa z %s meczów z podobnym stanem po 1. połowie" % (cu.get("frac") or cu.get("n")))
    else:
        cu = market.get("cond") or {}
        if cu and (cu.get("n") or 0) < MIN_N:
            w.append("za mało meczów z tym stanem po przerwie (%s) — podaję szansę bez tego filtra" % (cu.get("frac") or "0"))
    if (used.get("spread") or 0) >= 0.15:
        w.append("częstość i model Poissona różnią się o %d pkt — ostrożnie" % int(round(100 * used["spread"])))
    for prof, lab in ((home, "gospodarz"), (away, "gość")):
        if prof and (prof.get("n") or 0) < MIN_N:
            w.append("mała próba %s: %d meczów" % (lab, prof.get("n") or 0))
        if prof and (prof.get("n_tl") or 0) < (prof.get("n") or 0):
            miss = (prof.get("n") or 0) - (prof.get("n_tl") or 0)
            if miss and market["key"] == "over05_late":
                w.append("brak minut w %d meczach %s" % (miss, lab))
    return w


def pick_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Najmocniejszy over dla meczu albo None. Sortowane potem globalnie po szansie."""
    meta = meta or {}
    best = None
    for m in build_markets(home, away):
        used = m["cond"] if m.get("use_cond") and m.get("cond") and m["cond"].get("p") is not None else m["uncond"]
        if not used or used.get("p") is None or (used.get("n") or 0) < min_n:
            continue
        if used["p"] < p_min:
            continue
        # każda strona (albo ta jedna przy typie na gola drużyny) musi mieć własną próbę ≥ min_n
        if m["key"].startswith("team2h_"):
            side_prof = home if m.get("side") == "home" else away
            if (side_prof.get("n") or 0) < min_n:
                continue
        elif (home.get("n") or 0) < min_n or (away.get("n") or 0) < min_n:
            continue
        card = {
            "key": m["key"],
            "play": m["play"],
            "enter": m["enter"],
            "why": _why(m, used),
            "chance": used["p"],
            "chance_pct": used["pct"],
            "frac": used["frac"],
            "p_emp": used["p_emp"],
            "p_pois": used["p_pois"],
            "p_raw": used["p_raw"],
            "lam": used.get("lam"),
            "n": used["n"],
            "k": used["k"],
            "conditional": bool(m.get("use_cond")),
            "warnings": _warnings(m, used, home, away, meta),
            "home": home.get("name"), "away": away.get("name"),
        }
        card.update({k: meta.get(k) for k in (
            "eventId", "start_warsaw", "day_warsaw", "ts", "home_name", "away_name",
            "tournament", "category", "mode", "minutes", "score", "br") if k in meta or True})
        if meta:
            for k, v in meta.items():
                card.setdefault(k, v)
        if best is None or (card["chance"], card["n"]) > (best["chance"], best["n"]):
            best = card
    return best


def rank_picks(cards, limit=MAX_PICKS):
    cards = [c for c in cards if c and c.get("chance") is not None]
    cards.sort(key=lambda c: (-c["chance"], -c.get("n", 0), c.get("ts") or 0))
    for i, c in enumerate(cards[:limit], 1):
        c["rank"] = i
    return cards[:limit]


def public_card(c):
    """Pola na kartę w UI — bez surowych list meczów."""
    keys = ("rank", "eventId", "start_warsaw", "day_warsaw", "ts", "home", "away",
            "home_name", "away_name", "tournament", "category", "mode", "minutes", "score",
            "play", "enter", "why", "chance", "chance_pct", "frac", "p_emp", "p_pois",
            "p_raw", "lam", "n", "k", "conditional", "warnings", "key", "start_pl")
    out = {k: c.get(k) for k in keys}
    if out.get("p_emp") is not None:
        out["p_emp"] = round(out["p_emp"], 4)
    if out.get("p_pois") is not None:
        out["p_pois"] = round(out["p_pois"], 4)
    if out.get("p_raw") is not None:
        out["p_raw"] = round(out["p_raw"], 4)
    if out.get("chance") is not None:
        out["chance"] = round(out["chance"], 4)
    return out


CHANCE_RULE = (
    "Szansa = 55% częstości z ostatnich meczów obu drużyn po wygładzeniu Laplace’a (Beta(1,1): (k+1)/(n+2)) "
    "oraz 45% rozkładu Poissona ze średniej liczby goli w tym okresie (2. połowa, cały mecz albo 76–90+). "
    "Gdy jest co najmniej 8 meczów z tym samym stanem po 1. połowie, liczona jest szansa warunkowa. "
    "Podawany jest procent i ułamek źródłowy k/n. To częstość + model, nie gwarancja."
)
