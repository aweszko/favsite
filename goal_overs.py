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
        "any_1h": (ht_h + ht_a) >= 1,
        "any_2h": tot_2h >= 1, "any_late": any(g["bucket"] == "76-90+" for g in goals),
        "scored_2h": sum(1 for g in scored if g["half"] == 2) >= 1,
        "first": first, "first_match": first_match,
        "first_1h": min((g["minute"] for g in goals if g["half"] == 1), default=None),
        # znamy minutę 1. gola w 1H albo wiemy, że gola w 1H nie było (także z p1=0:0 bez timeline)
        "first_1h_known": bool(timeline_ok) or (ht_h + ht_a) == 0,
        "first_2h": min((g["minute"] for g in goals if g["half"] == 2), default=None),
        "first_2h_known": bool(timeline_ok) or tot_2h == 0,
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

    g1, n1 = cnt(lambda m: m.get("any_1h"))
    g2, n2 = cnt(lambda m: m.get("any_2h"))
    over15, n15 = cnt(lambda m: (m.get("tot") is not None) and m["tot"] >= 2)
    over25, n25 = cnt(lambda m: (m.get("tot") is not None) and m["tot"] >= 3)
    btts, nbtts = cnt(lambda m: (m.get("gf") is not None and m.get("ga") is not None) and m["gf"] >= 1 and m["ga"] >= 1)
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
        "any_1h": (g1, n1), "any_2h": (g2, n2), "over15": (over15, n15), "over25": (over25, n25),
        "btts": (btts, nbtts), "any_late": (late, nl),
        "scored_2h": (s2, ns2), "no_goal_1h": (no1, nn1),
        "cond_2h": (c2, nc2), "cond00_2h": (c2_00, nc2_00),
        "lam_tot": _mean([m.get("tot") for m in ms]),
        "lam_1h": _mean([m.get("tot_1h") for m in ms if m.get("tot_1h") is not None]),
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


def _1h_remaining_sample(matches, t):
    """Mecze, o których wiemy, że o minucie t było 0:0, i czy potem padł gol w 1. połowie.

    Mecze z golem w 1H, ale bez znanej minuty, są pomijane (nie zniekształcać próby).
    """
    try:
        t = int(t)
    except (TypeError, ValueError):
        return []
    sample = []
    for m in matches or []:
        if not m:
            continue
        if not m.get("first_1h_known"):
            continue
        first = m.get("first_1h")
        if first is not None and first <= t:
            continue
        rem = 0
        if first is not None and first > t:
            rem = sum(1 for g in (m.get("goals") or []) if g.get("half") == 1 and (g.get("minute") or 0) > t)
        sample.append((rem >= 1, rem))
    return sample


def chance_1h_at_minute(home, away, t):
    """Szansa na gola w pozostałym czasie 1. połowy, gdy o minucie t jest 0:0."""
    sample = _1h_remaining_sample((home.get("matches") or []) + (away.get("matches") or []), t)
    if not sample:
        return chance_block(0, 0, None, 0.5)
    k = sum(1 for ok, _ in sample if ok)
    n = len(sample)
    lam = _mean([rem for _, rem in sample])
    return chance_block(k, n, lam, 0.5)


def _2h_remaining_sample(matches, t):
    """Mecze, o których wiemy, że do minuty t nie było gola w 2. połowie, i czy potem padł."""
    try:
        t = int(t)
    except (TypeError, ValueError):
        return []
    sample = []
    for m in matches or []:
        if not m:
            continue
        if not m.get("first_2h_known"):
            continue
        first = m.get("first_2h")
        if first is not None and first <= t:
            continue
        rem = 0
        if first is not None and first > t:
            rem = sum(1 for g in (m.get("goals") or []) if g.get("half") == 2 and (g.get("minute") or 0) > t)
        sample.append((rem >= 1, rem))
    return sample


def chance_2h_at_minute(home, away, t):
    """Szansa na gola w pozostałym czasie 2. połowy, gdy do minuty t nie było gola w 2H."""
    sample = _2h_remaining_sample((home.get("matches") or []) + (away.get("matches") or []), t)
    if not sample:
        return chance_block(0, 0, None, 0.5)
    k = sum(1 for ok, _ in sample if ok)
    n = len(sample)
    lam = _mean([rem for _, rem in sample])
    return chance_block(k, n, lam, 0.5)


def _goal_after_sample(matches, t):
    """Mecze z znanymi minutami: czy po minucie t padł jeszcze jakiś gol."""
    try:
        t = int(t)
    except (TypeError, ValueError):
        return []
    sample = []
    for m in matches or []:
        if not m or not m.get("timeline_ok"):
            continue
        rem = sum(1 for g in (m.get("goals") or []) if (g.get("minute") or 0) > t)
        sample.append((rem >= 1, rem))
    return sample


def chance_goal_after_minute(home, away, t):
    """Szansa, że po minucie t padnie jeszcze co najmniej jeden gol w meczu."""
    sample = _goal_after_sample((home.get("matches") or []) + (away.get("matches") or []), t)
    if not sample:
        return chance_block(0, 0, None, 0.5)
    k = sum(1 for ok, _ in sample if ok)
    n = len(sample)
    lam = _mean([rem for _, rem in sample])
    return chance_block(k, n, lam, 0.5)


def _1h_enter(uncond, by_minute):
    """Prosty warunek wejścia z uczciwych szans na start i w 20./25. minucie."""
    bits = []
    # najpóźniejszy próg z n≥8 i szansą ≥ 70%
    latest = None
    for t in (15, 20, 25, 30):
        ch = by_minute.get(t) or {}
        if (ch.get("n") or 0) >= MIN_N and (ch.get("p") or 0) >= P_MIN:
            latest = t
    if latest:
        bits.append("do %d. minuty przy 0:0" % latest)
    else:
        bits.append("na start meczu, przy 0:0")
        ch20 = by_minute.get(20) or {}
        if (ch20.get("n") or 0) >= MIN_N and (ch20.get("p") is not None) and (ch20["p"] < P_MIN):
            bits.append("w 20. minucie przy 0:0 szansa spada do ok. %d%% — nie czekać" % ch20["pct"])
    return "; ".join(bits)


def _event_phrase(play):
    p = (play or "").lower()
    if "1. połowie" in p:
        return "Gol w 1. połowie"
    if "2. połowie" in p and "drużyny" in p:
        return "Gol tej drużyny w 2. połowie"
    if "2. połowie" in p:
        return "Gol w 2. połowie"
    if "75" in p:
        return "Gol po 75. minucie"
    if "2.5" in p:
        return "Co najmniej trzy gole"
    if "1.5" in p:
        return "Co najmniej dwa gole"
    if "btts" in p or "obie drużyny" in p:
        return "Obie drużyny strzeliły"
    return "Ten typ"

def _summary(play, used, enter, extra=None):
    raw = used.get("p_raw")
    raw_s = ("%d%%" % int(round(100 * raw))) if raw is not None else "—"
    fr = used.get("frac") or "—"
    pct_s = used.get("pct")
    chance_s = ("ok. %d%%" % pct_s) if pct_s is not None else "—"
    typ = (play[0].lower() + play[1:]) if play else "ten typ"
    text = "Zagraj %s. %s padł w %s spotkań tych drużyn (%s). Szansa: %s. Kiedy wejść: %s." % (
        typ, _event_phrase(play), raw_s, fr, chance_s, enter)
    # "Co najmniej dwa gole padł" is ugly — special-case
    if "1.5" in (play or ""):
        text = "Zagraj %s. Co najmniej dwa gole padły w %s spotkań tych drużyn (%s). Szansa: %s. Kiedy wejść: %s." % (
            typ, raw_s, fr, chance_s, enter)
    if extra:
        text += " " + extra
    return text


def build_markets(home, away, meta=None):
    """Kandydaci overów dla pary drużyn. Szansa z połączonych prób obu stron."""
    meta = meta or {}
    out = []
    # 0) Over 0.5 w 1. połowie — na start i warunkowo od minuty przy 0:0
    k, n = _pool(home.get("any_1h"), away.get("any_1h"))
    ch1 = chance_block(k, n, _mean2(home.get("lam_1h"), away.get("lam_1h")), 0.5)
    by_min = {t: chance_1h_at_minute(home, away, t) for t in (15, 20, 25, 30)}
    ch20 = by_min[20]
    extra_1h = None
    if (ch20.get("n") or 0) >= MIN_N and ch20.get("pct") is not None:
        extra_1h = "Szansa na start ok. %d%%; w 20. minucie przy 0:0 ok. %d%% (%s)." % (
            ch1.get("pct") if ch1.get("pct") is not None else 0, ch20["pct"], ch20.get("frac") or "—")
    enter_1h = _1h_enter(ch1, by_min)
    out.append({
        "key": "over05_1h",
        "play": "Over 0.5 gola w 1. połowie",
        "enter": enter_1h,
        "uncond": ch1, "cond": ch20 if (ch20.get("n") or 0) >= MIN_N else None,
        "use_cond": False,  # pre-match ranking wg szansy na start; live może podmienić
        "by_minute": by_min,
        "chance_kickoff": ch1,
        "chance_min20": ch20 if (ch20.get("n") or 0) >= MIN_N else None,
        "extra_why": extra_1h,
    })
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
    # 2b) Over 2.5 w meczu
    k, n = _pool(home.get("over25"), away.get("over25"))
    lam = _mean2(home.get("lam_tot"), away.get("lam_tot"))
    ch25 = chance_block(k, n, lam, 2.5)
    out.append({
        "key": "over25",
        "play": "Over 2.5 gola w meczu",
        "enter": "przed meczem",
        "uncond": ch25, "cond": None,
        "use_cond": False,
    })
    # 2c) BTTS
    k, n = _pool(home.get("btts"), away.get("btts"))
    p_h = pois_over(0.5, home.get("lam_gf")) if home.get("lam_gf") is not None else None
    p_a = pois_over(0.5, away.get("lam_gf")) if away.get("lam_gf") is not None else None
    p_pois_btts = (p_h * p_a) if (p_h is not None and p_a is not None) else None
    p_emp = laplace(k, n) if n else None
    p = combine(p_emp, p_pois_btts)
    p_raw = (k / n) if n else None
    ch_b = {
        "k": k, "n": n, "frac": frac(k, n), "p_raw": p_raw,
        "p_emp": p_emp, "p_pois": p_pois_btts, "p": p,
        "pct": pct(p), "lam": None,
        "spread": None if p_emp is None or p_pois_btts is None else abs(p_emp - p_pois_btts),
    }
    out.append({
        "key": "btts",
        "play": "BTTS (obie drużyny strzelą)",
        "enter": "przed meczem",
        "uncond": ch_b, "cond": None,
        "use_cond": False,
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
    if key == "over05_1h":
        raw = used.get("p_raw")
        raw_s = ("%d%%" % int(round(100 * raw))) if raw is not None else "—"
        return "gol w 1. połowie padł w %s spotkań tych drużyn (%s)" % (raw_s, fr)
    if key == "over05_2h":
        if market.get("use_cond"):
            return "gdy do przerwy był najwyżej 1 gol, gol w 2. połowie padł w %s ostatnich meczów obu drużyn" % fr
        return "gol w 2. połowie padł w %s ostatnich meczów obu drużyn" % fr
    if key == "over15":
        if market.get("use_cond"):
            return "przy najwyżej 1 golu do przerwy over 1.5 wchodził w %s ostatnich meczów obu drużyn" % fr
        return "w meczu padały co najmniej 2 gole w %s ostatnich meczów obu drużyn" % fr
    if key == "over25":
        return "w meczu padały co najmniej 3 gole w %s ostatnich meczów obu drużyn" % fr
    if key == "btts":
        return "obie drużyny strzeliły w %s ostatnich meczów tych drużyn" % fr
    if key == "over_next":
        return "po tej minucie padł jeszcze co najmniej jeden gol w %s meczach tych drużyn" % fr
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


def _parse_score(score):
    if score is None or score == "":
        return None
    if isinstance(score, (list, tuple)) and len(score) == 2:
        try:
            return int(score[0]), int(score[1])
        except (TypeError, ValueError):
            return None
    s = str(score).replace("–", ":").replace("-", ":").replace(" ", "")
    if ":" not in s:
        return None
    a, b = s.split(":", 1)
    try:
        return int(a), int(b)
    except (TypeError, ValueError):
        return None


def _live_minute(meta):
    if not meta:
        return None
    m = meta.get("minutes")
    if m is None or m == "":
        return None
    try:
        return int(str(m).split("+")[0].split(".")[0])
    except (TypeError, ValueError):
        return None


def qualifying_cards(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Wszystkie overy spełniające próg (n i szansa), jeszcze bez wyboru najmocniejszego."""
    meta = meta or {}
    cards = []
    live_min = _live_minute(meta)
    live_score = _parse_score(meta.get("score"))
    for m in build_markets(home, away, meta):
        used = m["cond"] if m.get("use_cond") and m.get("cond") and m["cond"].get("p") is not None else m["uncond"]
        # live: over 0.5 1H tylko przy 0:0 w 1. połowie, szansa z pozostałego czasu
        if m["key"] == "over05_1h" and live_min is not None:
            if live_min > 45:
                continue
            if live_score and (live_score[0] + live_score[1]) >= 1:
                continue
            ch_now = chance_1h_at_minute(home, away, live_min)
            if (ch_now.get("n") or 0) < min_n or ch_now.get("p") is None:
                continue
            used = ch_now
            m = dict(m)
            m["enter"] = "teraz, przy 0:0 w %d. minucie" % live_min
            m["use_cond"] = True
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
            "summary": _summary(m["play"], used, m["enter"], m.get("extra_why")),
            "chance_kickoff_pct": (m.get("chance_kickoff") or {}).get("pct") if m["key"] == "over05_1h" else None,
            "chance_min20_pct": (m.get("chance_min20") or {}).get("pct") if m["key"] == "over05_1h" else None,
            "frac_min20": (m.get("chance_min20") or {}).get("frac") if m["key"] == "over05_1h" else None,
            "n_min20": (m.get("chance_min20") or {}).get("n") if m["key"] == "over05_1h" else None,
        }
        card.update({k: meta.get(k) for k in (
            "eventId", "start_warsaw", "day_warsaw", "ts", "home_name", "away_name",
            "tournament", "category", "mode", "minutes", "score", "br") if k in meta or True})
        if meta:
            for k, v in meta.items():
                card.setdefault(k, v)
        cards.append(card)
    return cards


def pick_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Najmocniejszy over dla meczu albo None. Sortowane potem globalnie po szansie."""
    best = None
    for card in qualifying_cards(home, away, meta, p_min=p_min, min_n=min_n):
        if best is None or (card["chance"], card["n"]) > (best["chance"], best["n"]):
            best = card
    return best


def pick_1h_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Over 0.5 w 1. połowie, jeśli sam spełnia próg — nawet gdy 2H jest mocniejszy."""
    for card in qualifying_cards(home, away, meta, p_min=p_min, min_n=min_n):
        if card.get("key") == "over05_1h":
            return card
    return None


PRE_KEYS = ("over15", "over25", "over05_1h", "over05_2h", "btts")


def pick_pre_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Najmocniejszy typ przedmeczowy (1.5 / 2.5 / 0.5 1H / 0.5 2H / BTTS)."""
    best = None
    for card in qualifying_cards(home, away, meta, p_min=p_min, min_n=min_n):
        if card.get("key") not in PRE_KEYS:
            continue
        card = dict(card)
        card["summary"] = pre_summary(card)
        if best is None or (card["chance"], card["n"]) > (best["chance"], best["n"]):
            best = card
    return best


def pre_summary(card):
    play = card.get("play") or "ten typ"
    typ = play[0].lower() + play[1:] if play else "ten typ"
    fr = card.get("frac") or "—"
    pct_s = card.get("chance_pct")
    chance_s = ("ok. %d%%" % pct_s) if pct_s is not None else "—"
    key = card.get("key")
    if key == "over15":
        body = "Padły 2+ gole w %s meczach tych drużyn." % fr
    elif key == "over25":
        body = "Padły 3+ gole w %s meczach tych drużyn." % fr
    elif key == "over05_1h":
        body = "Gol w 1. połowie padł w %s meczach tych drużyn." % fr
        ch20 = card.get("frac_min20")
        if card.get("chance_min20_pct") is not None and ch20:
            body += " Przy 0:0 po 20. min w %s." % ch20
    elif key == "over05_2h":
        body = "Gol w 2. połowie padł w %s meczach tych drużyn." % fr
    elif key == "btts":
        body = "Obie drużyny strzeliły w %s meczach tych drużyn." % fr
    else:
        body = "Schemat wszedł w %s meczach tych drużyn." % fr
    text = "Warto rozważyć %s. %s Szansa: %s." % (typ, body, chance_s)
    if card.get("odds"):
        text += " Kurs Superbet: %s." % _fmt_odds(card["odds"])
    return text


def _fmt_odds(x):
    try:
        return ("%.2f" % float(x)).replace(".", ",")
    except (TypeError, ValueError):
        return str(x)


def _intish(x):
    try:
        return int(float(str(x).strip()))
    except (TypeError, ValueError):
        return None


def phase_of(period, minute):
    p = (period or "").upper()
    if p in ("FT", "ENDED", "END", "AET", "FIN"):
        return "ft"
    if p in ("HT", "PRZ"):
        return "ht"
    if p in ("1P", "1H", "H1"):
        return "1h"
    if p in ("2P", "2H", "H2"):
        return "2h"
    if minute is not None:
        return "1h" if minute <= 45 else "2h"
    return None


def time_label(phase, minute, period=None):
    if phase == "ht":
        return "przerwa"
    if phase == "1h":
        return ("%d' (1. połowa)" % minute) if minute is not None else "1. połowa"
    if phase == "2h":
        return ("%d' (2. połowa)" % minute) if minute is not None else "2. połowa"
    if minute is not None:
        return "%d'" % minute
    return period or "live"


def _period_score(periods, which):
    for p in periods or []:
        typ = (p.get("type") or "").upper()
        num = _intish(p.get("num"))
        h, a = _intish(p.get("homeTeamScore")), _intish(p.get("awayTeamScore"))
        if h is None or a is None:
            continue
        if which == 1 and (typ in ("FIRST_HALF",) or (typ in ("REGULARPERIOD", "REGULAR") and num == 1) or num == 1):
            if typ in ("SECOND_HALF",):
                continue
            if num == 1 or typ == "FIRST_HALF":
                return (h, a)
        if which == 2 and (typ in ("SECOND_HALF",) or (typ in ("REGULARPERIOD", "REGULAR") and num == 2) or num == 2):
            if typ in ("FIRST_HALF",):
                continue
            if num == 2 or typ == "SECOND_HALF":
                return (h, a)
    # second pass: num only
    for p in periods or []:
        num = _intish(p.get("num"))
        typ = (p.get("type") or "").upper()
        h, a = _intish(p.get("homeTeamScore")), _intish(p.get("awayTeamScore"))
        if h is None or a is None:
            continue
        if which == 1 and num == 1 and typ != "SECOND_HALF":
            return (h, a)
        if which == 2 and num == 2 and typ != "FIRST_HALF":
            return (h, a)
    return None


def parse_live_state(meta):
    """Aktualna minuta, wynik, faza i gole w połowach — tylko z danych live, bez zgadywania 2H."""
    meta = meta or {}
    minute = _live_minute(meta)
    period = (meta.get("period") or "")
    score = _parse_score(meta.get("score"))
    phase = phase_of(period, minute)
    hs = aws = tot = None
    if score:
        hs, aws = score
        tot = hs + aws
    p1 = _period_score(meta.get("periods"), 1)
    p2 = _period_score(meta.get("periods"), 2)
    goals_1h = goals_2h = None
    if phase in ("1h", "ht"):
        goals_1h = tot
        goals_2h = 0
        if phase == "ht" and p1 is not None:
            goals_1h = p1[0] + p1[1]
        if phase == "1h" and p1 is not None and tot is None:
            goals_1h = p1[0] + p1[1]
            tot = goals_1h
    elif phase == "2h":
        if p1 is not None and tot is not None:
            goals_1h = p1[0] + p1[1]
            goals_2h = tot - goals_1h
            if goals_2h < 0:
                goals_2h = None
        elif p2 is not None:
            goals_2h = p2[0] + p2[1]
            if tot is not None:
                goals_1h = tot - goals_2h
        # bez p1/p2 nie zgadujemy, czy gol w 2H już padł
    t_ref = minute
    if phase == "ht":
        t_ref = 45
    elif t_ref is None and phase == "1h":
        t_ref = 0
    score_s = None if hs is None else "%d:%d" % (hs, aws)
    return {
        "phase": phase, "minute": minute, "t_ref": t_ref, "period": period,
        "score": (hs, aws) if hs is not None else None, "score_s": score_s,
        "tot": tot, "goals_1h": goals_1h, "goals_2h": goals_2h,
        "p1": p1, "p2": p2, "time_label": time_label(phase, minute, period),
    }


def _live_card(home, away, meta, market, used, why_bits, st):
    play = market["play"]
    enter = market.get("enter") or ("teraz, %s" % st["time_label"])
    why = "; ".join(why_bits)
    card = {
        "key": market["key"],
        "play": play,
        "enter": enter,
        "why": why,
        "chance": used["p"],
        "chance_pct": used["pct"],
        "frac": used["frac"],
        "p_emp": used["p_emp"],
        "p_pois": used["p_pois"],
        "p_raw": used["p_raw"],
        "lam": used.get("lam"),
        "n": used["n"],
        "k": used["k"],
        "conditional": True,
        "warnings": _warnings(market, used, home, away, meta),
        "home": home.get("name"), "away": away.get("name"),
        "score": st.get("score_s"),
        "minutes": st.get("minute"),
        "period": st.get("period"),
        "phase": st.get("phase"),
        "time_label": st.get("time_label"),
        "mode": "live",
    }
    if meta:
        for k, v in meta.items():
            card.setdefault(k, v)
    card["summary"] = live_summary(card, why)
    return card


def live_summary(card, why=None):
    home = card.get("home") or card.get("home_name") or "?"
    away = card.get("away") or card.get("away_name") or "?"
    sc = card.get("score") or "–"
    tl = card.get("time_label") or (("%d'" % card["minutes"]) if card.get("minutes") is not None else "live")
    play = card.get("play") or "ten typ"
    typ = play[0].lower() + play[1:] if play else "ten typ"
    pct_s = card.get("chance_pct")
    chance_s = ("ok. %d%%" % pct_s) if pct_s is not None else "—"
    why = why or card.get("why") or ""
    why_s = why[0].lower() + why[1:] if why else ""
    if why_s and not why_s.endswith("."):
        why_s += "."
    return "%s – %s, %s, %s. Zagraj %s. Szansa: %s. %s" % (
        home, away, sc, tl, typ, chance_s, why_s)


def pick_live_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Jeden typ na mecz live, wg aktualnej minuty i wyniku. Pomija typy, które już weszły."""
    meta = meta or {}
    st = parse_live_state(meta)
    if st.get("phase") in (None, "ft"):
        return None
    if (home.get("n") or 0) < min_n or (away.get("n") or 0) < min_n:
        return None
    period_card = None
    # 1) Over 0.5 1H — tylko 1. połowa i 0:0
    if st["phase"] == "1h" and st.get("tot") == 0 and st.get("t_ref") is not None:
        ch = chance_1h_at_minute(home, away, st["t_ref"])
        base_k, base_n = _pool(home.get("any_1h"), away.get("any_1h"))
        if ch.get("p") is not None and (ch.get("n") or 0) >= min_n and ch["p"] >= p_min:
            why = ["gol w 1. połowie padł w %s meczów tych drużyn" % (frac(base_k, base_n) or "—")]
            why.append("przy 0:0 po %d. min w %s" % (st["t_ref"], ch.get("frac") or "—"))
            m = {"key": "over05_1h", "play": "Over 0.5 gola w 1. połowie",
                 "enter": "teraz, przy 0:0 w %s" % st["time_label"], "use_cond": True, "cond": ch}
            period_card = _live_card(home, away, meta, m, ch, why, st)
            period_card["frac_base"] = frac(base_k, base_n)
            period_card["frac_cond"] = ch.get("frac")
    # 2) Over 0.5 2H — przerwa albo 2H bez gola w 2H
    if st["phase"] in ("ht", "2h") and st.get("goals_2h") == 0 and st.get("t_ref") is not None:
        ch = chance_2h_at_minute(home, away, st["t_ref"])
        base_k, base_n = _pool(home.get("any_2h"), away.get("any_2h"))
        if ch.get("p") is not None and (ch.get("n") or 0) >= min_n and ch["p"] >= p_min:
            why = ["gol w 2. połowie padł w %s meczów tych drużyn" % (frac(base_k, base_n) or "—")]
            if st["phase"] == "ht":
                why.append("do przerwy bez gola w 2. połowie w %s" % (ch.get("frac") or "—"))
            else:
                why.append("bez gola w 2. połowie po %d. min w %s" % (st["t_ref"], ch.get("frac") or "—"))
            m = {"key": "over05_2h", "play": "Over 0.5 gola w 2. połowie",
                 "enter": "teraz, %s, bez gola w 2. połowie" % st["time_label"], "use_cond": True, "cond": ch}
            period_card = _live_card(home, away, meta, m, ch, why, st)
            period_card["frac_base"] = frac(base_k, base_n)
            period_card["frac_cond"] = ch.get("frac")
    if period_card:
        return period_card
    # 3) ewentualnie over aktualnej sumy + 0.5 (następny gol), gdy typ połowy już wszedł albo nie ma sensu
    if st.get("tot") is None or st.get("t_ref") is None:
        return None
    ch = chance_goal_after_minute(home, away, st["t_ref"])
    if ch.get("p") is None or (ch.get("n") or 0) < min_n or ch["p"] < p_min:
        return None
    line = st["tot"] + 0.5
    play = "Over %s gola w meczu" % str(line).replace(".0", "")
    # 0.5 -> "0.5"; 1.5 stays
    if float(line) == int(line) + 0.5:
        play = "Over %s gola w meczu" % (("%0.1f" % line).replace(".", ","))
    why = ["po %d. minucie padł jeszcze co najmniej jeden gol w %s meczach tych drużyn" % (
        st["t_ref"], ch.get("frac") or "—")]
    m = {"key": "over_next", "play": play,
         "enter": "teraz, %s, wynik %s" % (st["time_label"], st.get("score_s") or "–"),
         "use_cond": True, "cond": ch}
    card = _live_card(home, away, meta, m, ch, why, st)
    card["line"] = line
    return card


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
            "play", "enter", "why", "summary", "chance", "chance_pct", "frac", "p_emp", "p_pois",
            "p_raw", "lam", "n", "k", "conditional", "warnings", "key", "start_pl",
            "chance_kickoff_pct", "chance_min20_pct", "frac_min20", "n_min20",
            "odds", "time_label", "phase", "period", "frac_base", "frac_cond", "line")
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
    "oraz 45% rozkładu Poissona ze średniej liczby goli w tym okresie (1. połowa, 2. połowa, cały mecz albo 76–90+). "
    "Gdy jest co najmniej 8 meczów z tym samym stanem (do przerwy albo 0:0 w danej minucie 1. połowy), liczona jest szansa warunkowa. "
    "Dla over 0.5 w 1. połowie podawana jest szansa na start i — o ile próba pozwala — w 20. minucie przy 0:0, z rozkładu minut pierwszych goli. "
    "W zakładce live szansa jest liczona od aktualnej minuty i wyniku (gol w pozostałym czasie danej połowy albo over aktualnej sumy + 0.5). "
    "Podawany jest procent i ułamek źródłowy k/n. To częstość + model, nie gwarancja."
)
