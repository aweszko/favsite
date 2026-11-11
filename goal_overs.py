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
EDGE_PLUS = 0.03          # PLUS, gdy szansa bije 1/kurs o >= 3 pp
SANITY_EDGE = 0.15        # powyżej tego vs kurs (bez marży, gdy się da) = niepewne dane
LIVE_RANK_P_MIN = 0.60    # próg wejścia linii do rankingu LIVE (wymaga też kursu)
LASTX_N = 15
W_EMP, W_POIS = 0.55, 0.45
MAX_PICKS = 10
SIMILAR_MIN_N = 5  # minimum podobnych meczów zanim rozluźniamy warunki
PRE_MIN_ODDS = 1.30  # przed meczem: nie typujemy bardzo krótkich kursów
# Kotwica rynkowa i poprawki live (kalibracja: lastx gismo z 2026-10-29, 2278 połówek z p1).
# Historia sama przy pokazie 80–90% trafiała wyraźnie rzadziej (ok. 67–73%).
MARKET_WEIGHT = 0.55       # waga kursu bez marży; historia tylko koryguje
NO_ODDS_CAP = 0.70         # bez kursu nie pokazuję wyższej szansy niż to i nie typuję TAK
LATE_NEED1_MINUTE = 30     # od tej minuty w połowie: nie pokazuję >80% na 1 gol, chyba że kurs się zgadza
LATE_NEED1_CAP = 0.80
RECENT_N = 5
RECENT_W = 2.0             # ost. 5 meczów waży 2× względem starszych
COMP_SHRINK = 0.35         # waga ligowej/pucharowej bazy 1H wobec historii drużyn
HALF_LEN = 45
# Bazy P(gol w 1H) z tej samej próby lastx (nie zmyślane).
BASE_1H_P = 0.71           # ligi mężczyzn / ogół, n≈1991
BASE_1H_P_WOMEN = 0.81     # n=129
BASE_1H_P_EURO = 0.71      # puchary UEFA, n=97 (w próbie nie niżej niż liga)
BASE_1H_P_CUP = 0.77       # inne puchary, n=118
BASE_1H_P_FRIENDLY = 0.60  # n=70
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


def frac_words(k, n):
    """Ułamek po polsku: '5 z 8' — do zdań, nie do pól danych."""
    if n is None or n <= 0 or k is None:
        return None
    return "%d z %d" % (int(k), int(n))


def _pl_nom_mecze(n):
    n = int(n)
    if n == 1:
        return "1 mecz"
    if 12 <= (n % 100) <= 14:
        return "%d meczów" % n
    if (n % 10) in (2, 3, 4):
        return "%d mecze" % n
    return "%d meczów" % n


def _w_meczach(n):
    n = int(n)
    if n == 1:
        return "w 1 meczu"
    return "w %d meczach" % n


def _mala_proba(n):
    n = int(n or 0)
    if n <= 0:
        return "Nie ma podobnych meczów — tego nie da się uczciwie policzyć."
    if n == 1:
        return "To tylko 1 mecz — to za mało, traktuj ostrożnie."
    return "To tylko %s — to mało, traktuj ostrożnie." % _pl_nom_mecze(n)


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


def match_view(uid, is_home, goals, ft=None, p1=None, timeline_ok=True, mid=None, opp_uid=None, uts=None, tournament=None):
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
        "tournament": tournament,
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


def _half_goal_minutes(m, half):
    out = []
    for g in m.get("goals") or []:
        if g.get("half") != half:
            continue
        try:
            out.append(int(g["minute"]))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _half_remaining_sample(matches, t, half, goals_so_far):
    """Mecze, które o minucie t miały w tej połowie dokładnie `goals_so_far` goli, i czy potem padł kolejny.

    Bez znanych minut: tylko stan 0:0 / 0 goli w połowie (pewnie wiemy, że gola nie było).
    """
    try:
        t = int(t)
        goals_so_far = int(goals_so_far)
    except (TypeError, ValueError):
        return []
    if goals_so_far < 0:
        return []
    known = "first_1h_known" if half == 1 else "first_2h_known"
    any_h = "any_1h" if half == 1 else "any_2h"
    sample = []
    for m in matches or []:
        if not m:
            continue
        if m.get("timeline_ok"):
            mins = _half_goal_minutes(m, half)
            by_t = sum(1 for x in mins if x <= t)
            if by_t != goals_so_far:
                continue
            rem = sum(1 for x in mins if x > t)
            sample.append((rem >= 1, rem))
            continue
        if goals_so_far != 0 or not m.get(known):
            continue
        if m.get(any_h):
            continue
        sample.append((False, 0))
    return sample


def chance_half_at_minute(home, away, t, half, goals_so_far=0):
    """Szansa na kolejny gol w pozostałym czasie danej połowy, przy aktualnej liczbie goli w tej połowie."""
    sample = _half_remaining_sample(
        (home.get("matches") or []) + (away.get("matches") or []), t, half, goals_so_far)
    if not sample:
        return chance_block(0, 0, None, 0.5)
    k = sum(1 for ok, _ in sample if ok)
    n = len(sample)
    lam = _mean([rem for _, rem in sample])
    return chance_block(k, n, lam, 0.5)


def _frac_block(sample, need):
    k = sum(1 for _, rem in sample if rem >= need)
    n = len(sample)
    return {"k": k, "n": n, "frac": frac(k, n)}


def is_women_comp(name):
    s = " ".join(str(x) for x in ((name,) if not isinstance(name, (list, tuple)) else name) if x)
    sl = s.lower()
    if "(k)" in sl or " (w)" in sl or sl.endswith("(w)"):
        return True
    return any(w in sl for w in ("women", "woman", "ladies", "femen", "feminin", "damen", "kobiet", "kvinne", "dame "))


def competition_kind(name):
    s = (name or "").lower()
    if any(k in s for k in ("uefa", "champions league", "europa league", "europa cup",
                            "conference league", "liga mistrz", "liga europy",
                            "superpuchar europy", "uecl")):
        return "european_cup"
    if any(k in s for k in ("world cup", "copa america", "nations league", "euro 20",
                            "african cup", "afcon", "qualif")):
        return "intl"
    if any(k in s for k in ("friendly", "towarzysk", "international friend")):
        return "friendly"
    if any(k in s for k in ("puchar", "coupe ", "coppa", "league cup", "fa cup",
                            "dfb-pokal", "dfb pokal", "copa ", "cup")):
        return "cup"
    return "league"


def competition_base_p(meta, half=1):
    """Bazowa szansa na co najmniej 1 gol w połowie, z kalibracji lastx (nie zmyślana)."""
    if half != 1:
        # 2H w tej próbie nie liczyłem osobno — zostawiam ogólną bazę ligową.
        pass
    meta = meta or {}
    name = " ".join(str(meta.get(k) or "") for k in ("tournament", "category", "league"))
    kind = competition_kind(name)
    if is_women_comp(name) or is_women_comp(meta.get("home")) or is_women_comp(meta.get("away")):
        p = BASE_1H_P_WOMEN
        label = "piłka kobiet"
    elif kind == "european_cup":
        p = BASE_1H_P_EURO
        label = "puchar europejski"
    elif kind == "cup":
        p = BASE_1H_P_CUP
        label = "puchar"
    elif kind == "friendly":
        p = BASE_1H_P_FRIENDLY
        label = "mecz towarzyski"
    else:
        p = BASE_1H_P
        label = "liga"
    return p, kind, label


def remaining_minutes(t, half, phase=None):
    try:
        t = int(t)
    except (TypeError, ValueError):
        t = 0 if half == 1 else 45
    if t < 0:
        t = 0
    if half == 1:
        return max(0, HALF_LEN - min(t, HALF_LEN))
    if phase == "ht" or t <= 45:
        return HALF_LEN
    return max(0, 90 - min(t, 90))


def _recency_weights(matches, recent_n=RECENT_N, w=RECENT_W):
    ms = [m for m in (matches or []) if m]
    if any((m.get("uts") or 0) for m in ms):
        ordered = sorted(ms, key=lambda m: m.get("uts") or 0, reverse=True)
    else:
        ordered = list(ms)
    return {id(m): (w if i < recent_n else 1.0) for i, m in enumerate(ordered)}


def _weighted_mean(pairs):
    """pairs: (value, weight)."""
    xs = [(v, w) for v, w in pairs if v is not None and w]
    if not xs:
        return None
    return sum(v * w for v, w in xs) / sum(w for _, w in xs)


def _team_bucket_lam(team, t, half):
    n = team.get("n_tl") or 0
    if n <= 0:
        return None
    if half == 1:
        ranges = (("0-15", 0, 15), ("16-30", 15, 30), ("31-45+", 30, 45))
        end = 45
    else:
        ranges = (("46-60", 45, 60), ("61-75", 60, 75), ("76-90+", 75, 90))
        end = 90
    counts = {}
    for key in ("scored_buckets", "conceded_buckets"):
        for b, c in (team.get(key) or {}).items():
            counts[b] = counts.get(b, 0) + (c or 0)
    try:
        t = int(t)
    except (TypeError, ValueError):
        t = 45 if half == 2 else 0
    lam = 0.0
    any_b = False
    for b, lo, hi in ranges:
        width = hi - lo
        if width <= 0:
            continue
        overlap = max(0, min(hi, end) - max(lo, t))
        if overlap <= 0:
            continue
        any_b = True
        lam += (counts.get(b, 0) / n) * (overlap / width)
    return lam if any_b else None


def remaining_rate_lambda(home, away, t, half, goals_so_far=0):
    """Oczekiwane gole w pozostałym czasie połowy: tempo na minutę (kubełki albo λ połowy × udział minut)."""
    rem = remaining_minutes(t, half)
    field = "tot_1h" if half == 1 else "tot_2h"
    lams_b = []
    lams_u = []
    for team in (home, away):
        bl = _team_bucket_lam(team, t, half)
        if bl is not None:
            lams_b.append(bl)
        wmap = _recency_weights(team.get("matches") or [])
        pairs = []
        for m in (team.get("matches") or []):
            if not m or m.get(field) is None:
                continue
            pairs.append((float(m[field]), wmap.get(id(m), 1.0)))
        full = _weighted_mean(pairs)
        if full is not None:
            lams_u.append(full * (rem / float(HALF_LEN)))
    if lams_b:
        return _mean(lams_b), rem
    if lams_u:
        return _mean(lams_u), rem
    return None, rem


def _comp_sample_lambda(home, away, half, meta):
    """λ i P(1+) z historii obu drużyn w podobnym typie rozgrywek, gdy jest dość meczów."""
    field = "tot_1h" if half == 1 else "tot_2h"
    want = competition_kind(" ".join(str((meta or {}).get(k) or "") for k in ("tournament", "category", "league")))
    rows = []
    for team in (home, away):
        for m in (team.get("matches") or []):
            if not m or m.get(field) is None:
                continue
            k = competition_kind(m.get("tournament"))
            if k != want:
                continue
            rows.append(float(m[field]))
    if len(rows) < 6:
        return None, None, len(rows)
    lam = sum(rows) / len(rows)
    p = sum(1 for x in rows if x >= 1) / len(rows)
    return lam, p, len(rows)


def remaining_goals_dist(home, away, t, half, goals_so_far=0, meta=None):
    """Rozkład liczby goli w pozostałym czasie połowy: 1+/2+/3+.

    Poisson z tempa na minutę (pozostały czas), empiria z podobnej minuty,
    ost. 5 meczów ważone mocniej, lekka zbieżność do bazy ligi/pucharu.
    """
    sample_h = _half_remaining_sample(home.get("matches") or [], t, half, goals_so_far)
    sample_a = _half_remaining_sample(away.get("matches") or [], t, half, goals_so_far)
    # wagi recencji wg meczów w profilu, nie tylko w próbce
    wh = _recency_weights(home.get("matches") or [])
    wa = _recency_weights(away.get("matches") or [])
    # próbka nie niesie id meczu — _half_remaining_sample zwraca tylko (hit, rem).
    # Wagi przyłożymy równo w obrębie drużyny przez powtórne przejście poniżej.
    sample = sample_h + sample_a
    n = len(sample)
    lam_emp = _mean([rem for _, rem in sample]) if sample else None
    lam_rate, rem = remaining_rate_lambda(home, away, t, half, goals_so_far)
    lam = lam_rate if lam_rate is not None else lam_emp
    base_p, kind, kind_label = competition_base_p(meta, half)
    # próba tych samych drużyn w tym typie rozgrywek NIE zastępuje bazy z kalibracji
    # (to byłoby błędne koło: 20/20 w lidze ustawia bazę na 100% i podbija szansę).
    _, _, comp_n = _comp_sample_lambda(home, away, half, meta)
    # baza na pozostały czas: stałe tempo, λ_baza × rem/45
    try:
        lam_base_full = -math.log(max(1e-6, 1.0 - float(base_p)))
    except (TypeError, ValueError):
        lam_base_full = None
    lam_base_rem = None if lam_base_full is None else lam_base_full * (rem / float(HALF_LEN))
    # empiria ważona: para mecz ↔ waga recencji
    def weighted_emp_paired(need):
        ks = ns = 0.0
        k_raw = n_raw = 0
        for team in (home, away):
            wmap = _recency_weights(team.get("matches") or [])
            for m in (team.get("matches") or []):
                sm = _half_remaining_sample([m], t, half, goals_so_far)
                if not sm:
                    continue
                rem_g = sm[0][1]
                hit = rem_g >= need
                w = wmap.get(id(m), 1.0)
                ks += w if hit else 0.0
                ns += w
                k_raw += int(hit)
                n_raw += 1
        return ks, ns, k_raw, n_raw

    by_need = {}
    for need in (1, 2, 3):
        k_w, n_w, k, n_need = weighted_emp_paired(need)
        ch = chance_block(k, n_need, lam, need - 0.5)
        # tempo na minutę jest trzonem; częstość „gol po tej minucie w X/N” tylko lekko koryguje,
        # i tym mniej, im mniej minut zostało (inaczej późny gol w historii trzyma 80%+ do 44.')
        p_emp_w = laplace(k_w, n_w) if n_w and n_w > 0 else ch.get("p_emp")
        p_pois = pois_over(need - 0.5, lam) if lam is not None else ch.get("p_pois")
        if need == 1:
            # tempo na minutę jest trzonem przy 1 golu; empiria słabnie, gdy zostaje mało minut
            share_emp = 0.35 * (float(rem) / float(HALF_LEN)) if rem is not None else 0.35
            share_emp = max(0.0, min(0.45, share_emp))
        else:
            # 2+/3+: ogon Poissona jest mniej pewny — zostawiam klasyczną mieszankę
            share_emp = W_EMP
        if p_emp_w is not None and p_pois is not None:
            p_hist = share_emp * p_emp_w + (1.0 - share_emp) * p_pois
        else:
            p_hist = combine(p_emp_w, p_pois)
        p_base_need = pois_over(need - 0.5, lam_base_rem) if lam_base_rem is not None else None
        # baza z kalibracji dotyczy gola w 1. połowie; w 2H nie ściągam do niej
        if half == 1 and p_hist is not None and p_base_need is not None:
            p = (1.0 - COMP_SHRINK) * p_hist + COMP_SHRINK * p_base_need
        else:
            p = p_hist
        ch = dict(ch)
        ch["p_emp"] = p_emp_w
        ch["p_pois"] = p_pois
        ch["p_hist"] = p_hist
        ch["p_base"] = p_base_need
        ch["p"] = p
        ch["pct"] = pct(p)
        ch["k_w"] = None if not n_w else round(k_w, 2)
        ch["n_w"] = None if not n_w else round(n_w, 2)
        ch["home"] = _frac_block(sample_h, need)
        ch["away"] = _frac_block(sample_a, need)
        by_need[need] = ch
    return {
        "n": n, "lam": None if lam is None else round(lam, 3),
        "lam_rate": None if lam_rate is None else round(lam_rate, 3),
        "half": half, "t": t, "goals_so_far": int(goals_so_far),
        "remaining_minutes": rem,
        "comp_kind": kind, "comp_label": kind_label,
        "comp_base_p": base_p, "comp_n": comp_n,
        "by_need": by_need,
    }


def chance_1h_at_minute(home, away, t):
    """Szansa na gola w pozostałym czasie 1. połowy, gdy o minucie t jest 0:0."""
    return chance_half_at_minute(home, away, t, 1, 0)


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
    text = "Zagraj %s. %s padł w %s ostatnich meczów obu drużyn (każda z różnymi rywalami) (%s). Szansa: %s. Kiedy wejść: %s." % (
        typ, _event_phrase(play), raw_s, fr, chance_s, enter)
    # "Co najmniej dwa gole padł" is ugly — special-case
    if "1.5" in (play or ""):
        text = "Zagraj %s. Co najmniej dwa gole padły w %s ostatnich meczów obu drużyn (każda z różnymi rywalami) (%s). Szansa: %s. Kiedy wejść: %s." % (
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
        return "gol w 1. połowie padł w %s ostatnich meczów obu drużyn (każda z różnymi rywalami) (%s)" % (raw_s, fr)
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
        return "obie drużyny strzeliły w %s ostatnich meczów obu drużyn (każda z różnymi rywalami)" % fr
    if key == "over_next":
        return "po tej minucie padł jeszcze co najmniej jeden gol w %s ostatnich meczach obu drużyn (każda z różnymi rywalami)" % fr
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
        w.append(_mala_proba(n))
    if market.get("use_cond"):
        cu = market.get("cond") or {}
        if (cu.get("n") or 0) < 12:
            w.append("szansa warunkowa z %s meczów z podobnym stanem po 1. połowie" % (cu.get("frac") or cu.get("n")))
    else:
        cu = market.get("cond") or {}
        if cu and (cu.get("n") or 0) < MIN_N:
            w.append("za mało meczów z tym stanem po przerwie (%s) — podaję szansę bez tego filtra" % (cu.get("frac") or "0"))
    if (used.get("spread") or 0) >= 0.15:
        w.append("dwa sposoby liczenia dają inny wynik (różnica ok. %d na 100) — ostrożnie" % int(round(100 * used["spread"])))
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

# Zakładka przedmeczowa: WYŁĄCZNIE over w 1. połowie (0.5 / 1.5 / 2.5).
PRE_OVER_SPEC = (
    ("over05_1h", "1h", 0.5, "tot_1h", "Over 0.5 gola w 1. połowie", "Gol w 1. połowie padł"),
    ("over15_1h", "1h", 1.5, "tot_1h", "Over 1.5 gola w 1. połowie", "2+ gole w 1. połowie padły"),
    ("over25_1h", "1h", 2.5, "tot_1h", "Over 2.5 gola w 1. połowie", "3+ gole w 1. połowie padły"),
)
PRE_1H_KEYS = tuple(s[0] for s in PRE_OVER_SPEC)


def _thresh_sample(matches, field, line):
    """k/n i średnia dla P(X > line), np. over 1.5 => tot >= 2."""
    need = int(line) + 1
    ok = [m for m in (matches or []) if m and m.get(field) is not None]
    k = sum(1 for m in ok if m[field] >= need)
    lam = _mean([m[field] for m in ok])
    return k, len(ok), lam


def pre_over_lines(home, away):
    """Tylko over 0.5 / 1.5 / 2.5 w 1. połowie, z ułamkiem łącznym i osobno na drużynę."""
    lines = []
    for key, scope, line, field, play, why_verb in PRE_OVER_SPEC:
        kh, nh, lamh = _thresh_sample(home.get("matches") or [], field, line)
        ka, na, lama = _thresh_sample(away.get("matches") or [], field, line)
        k, n = kh + ka, nh + na
        ch = chance_block(k, n, _mean2(lamh, lama), line)
        if ch.get("p") is None or not n:
            continue
        why = "%s w %s ostatnich meczach obu drużyn (każda z różnymi rywalami)" % (why_verb, ch.get("frac") or "—")
        if nh or na:
            why += " (osobno gospodarze %s, goście %s)" % (frac(kh, nh) or "—", frac(ka, na) or "—")
        lines.append({
            "need": int(line) + 1, "scope": scope, "line": line, "key": key, "play": play,
            "chance": ch["p"], "chance_pct": ch["pct"],
            "frac": ch["frac"], "k": ch["k"], "n": ch["n"],
            "p_emp": ch["p_emp"], "p_pois": ch["p_pois"], "p_raw": ch["p_raw"],
            "lam": ch.get("lam"), "why": why,
            "home_side": {"k": kh, "n": nh, "frac": frac(kh, nh)},
            "away_side": {"k": ka, "n": na, "frac": frac(ka, na)},
            "odds": None, "implied": None, "implied_pct": None,
            "edge": None, "edge_pp": None, "verdict": None,
        })
    return lines


def _pre_when(card):
    t = card.get("start_warsaw") or ""
    d = card.get("day_warsaw") or ""
    if d and t:
        parts = str(d).split("-")
        short = ("%s.%s" % (parts[2], parts[1])) if len(parts) == 3 else d
        return "%s (%s, Warszawa)" % (t, short)
    if t:
        return "%s (Warszawa)" % t
    if d:
        return "%s (Warszawa)" % d
    return ""


def _team_str(card, side):
    v = card.get(side)
    if isinstance(v, str) and v:
        return v
    v2 = card.get(side + "_name")
    if isinstance(v2, str) and v2:
        return v2
    return "?"


def chance_win_label(pct):
    if pct is None:
        return "Szansa na wygraną zakładu: nie wiadomo"
    return "Szansa na wygraną zakładu: %d%%" % int(pct)


def worth_because(line):
    if not line or line.get("verdict") not in ("PLUS", "MINUS"):
        return None
    ch, imp = line.get("chance_pct"), line.get("implied_pct")
    if ch is None or imp is None:
        return None
    if line["verdict"] == "PLUS":
        return "bo nasza szansa (%d%%) jest wyraźnie większa niż to, co sugeruje kurs (%d%%)" % (ch, imp)
    return "bo kurs jest za niski na taką szansę (szansa %d%%, a kurs sugeruje %d%%)" % (ch, imp)


def worth_text(line):
    if not line or not line.get("odds"):
        return "Opłaca się? Jeszcze nie wiadomo (nie ma kursu Superbet)."
    if line.get("unreliable") or line.get("verdict") == "UNRELIABLE":
        ch, imp = line.get("chance_pct"), line.get("implied_pct")
        if ch is not None and imp is not None:
            return ("Opłaca się? Nie typuję (niepewne dane: nasza szansa %d%% wyszła dużo wyżej "
                    "niż to, co sugeruje kurs %d%%)." % (ch, imp))
        return "Opłaca się? Nie typuję (niepewne dane: szansa mocno mija się z kursem)."
    bec = worth_because(line)
    if line.get("verdict") == "PLUS":
        return "Opłaca się? TAK (%s)." % bec if bec else "Opłaca się? TAK."
    if line.get("verdict") == "MINUS":
        return "Opłaca się? NIE (%s)." % bec if bec else "Opłaca się? NIE."
    return "Opłaca się? Jeszcze nie wiadomo (nie ma kursu Superbet)."


def fill_offer(line):
    if not line:
        return line
    line["chance_label"] = chance_win_label(line.get("chance_pct"))
    if line.get("unreliable") or line.get("verdict") == "UNRELIABLE":
        line["worth"] = "niepewne dane"
    else:
        line["worth"] = "TAK" if line.get("verdict") == "PLUS" else ("NIE" if line.get("verdict") == "MINUS" else None)
    line["worth_because"] = worth_because(line)
    line["worth_text"] = worth_text(line)
    return line


def offer_text(line, why=None, header=None):
    """Prosty opis: co zagrać, szansa na wygraną, kurs, czy się opłaca, dlaczego."""
    line = line or {}
    bits = []
    if header:
        h = header.strip()
        if h and not h.endswith("."):
            h += "."
        bits.append(h)
    play = line.get("play") or "ten typ"
    bits.append("Co zagrać: %s." % play)
    bits.append(chance_win_label(line.get("chance_pct")) + ".")
    if line.get("odds"):
        bits.append("Kurs: %s." % _fmt_odds(line["odds"]))
    else:
        bits.append("Kurs: brak kursu Superbet.")
    bits.append(worth_text(line))
    why = why if why is not None else line.get("why")
    if why:
        w = str(why).strip()
        if not w.lower().startswith("dlaczego"):
            if w:
                w = w[0].lower() + w[1:]
            w = "Dlaczego: " + w
        if w and not w.endswith("."):
            w += "."
        bits.append(w)
    return " ".join(bits)


def pre_line_summary(card, line=None):
    line = fill_offer(dict(line or card))
    home, away = _team_str(card, "home"), _team_str(card, "away")
    head = "%s – %s" % (home, away)
    lg = card.get("tournament") or card.get("category") or ""
    when = _pre_when(card)
    header = ", ".join(x for x in (head, lg, when) if x)
    return offer_text(line, why=line.get("why"), header=header)


def pre_summary(card):
    """Podpis karty przedmeczowej — zawsze z nazwami, ligą i godziną (Warszawa)."""
    return pre_line_summary(card, card)


def pre_match_card(home, away, meta=None):
    """Mecz przedmeczowy: tylko over 0.5 / 1.5 / 2.5 w 1. połowie."""
    meta = meta or {}
    lines = pre_over_lines(home, away)
    if not lines:
        return None
    card = {
        "home": home.get("name") or meta.get("home"),
        "away": away.get("name") or meta.get("away"),
        "mode": "prematch",
        "lines": lines,
        "warnings": context_warnings(meta),
        "conditional": False,
    }
    if meta:
        for k, v in meta.items():
            card.setdefault(k, v)
    # pola „główne” z najmocniejszej linii wg szansy (kurs dochodzi później)
    top = max(lines, key=lambda ln: (ln.get("chance") or 0, ln.get("n") or 0))
    for k in ("key", "play", "chance", "chance_pct", "frac", "k", "n",
              "p_emp", "p_pois", "p_raw", "lam", "why", "line"):
        card[k] = top.get(k)
    # ostrzeżenie o małej próbie na głównej linii
    if (top.get("n") or 0) < 12:
        card["warnings"] = list(card["warnings"]) + [
            _mala_proba(top.get("n") or 0)]
    for prof, lab in ((home, "gospodarz"), (away, "gość")):
        if prof and (prof.get("n") or 0) < MIN_N:
            card["warnings"].append("mała próba %s: %d meczów" % (lab, prof.get("n") or 0))
    card["summary"] = pre_line_summary(card, top)
    card["recommend"] = live_recommend(card)
    return card


def pick_pre_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Karta przedmeczowa; None tylko gdy żadna linia nie spełnia starego progu (zgodność testów)."""
    card = pre_match_card(home, away, meta)
    if not card:
        return None
    # jeśli jest linia z n i szansą powyżej progu, zostaw kartę (główna = najmocniejsza ogółem)
    strong = [ln for ln in (card.get("lines") or [])
              if (ln.get("n") or 0) >= min_n and ln.get("chance") is not None and ln["chance"] >= p_min
              and (home.get("n") or 0) >= min_n and (away.get("n") or 0) >= min_n]
    if not strong:
        return None
    return card




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


def _over_half_play(goals_so_far, half):
    line = "%d.5" % int(goals_so_far)
    side = "1. połowie" if half == 1 else "2. połowie"
    return "Over %s gola w %s" % (line, side)


def _over_half_key(goals_so_far, half):
    return "over%d5_%dh" % (int(goals_so_far), half)


def _fmt_line(line):
    try:
        line = float(line)
    except (TypeError, ValueError):
        return str(line)
    if abs(line - (int(line) + 0.5)) < 1e-9:
        return "%d.5" % int(line)
    s = ("%.2f" % line).rstrip("0").rstrip(".")
    return s


def _odds_key(scope, line):
    n = int(float(line))
    if scope == "match":
        return "over%d5" % n
    half = 1 if scope in (1, "1h", "half1") else 2
    if scope == "half":
        half = None
    if half is None:
        return "over%d5" % n
    return "over%d5_%dh" % (n, half)


def _need_why(need, half, phase, t, fr, k=None, n=None):
    words = frac_words(k, n) if (k is not None and n is not None) else None
    if not words and fr:
        words = str(fr).replace("/", " z ")
    words = words or "—"
    if need == 1:
        what = "padł kolejny gol"
    else:
        what = "padły jeszcze co najmniej %d gole" % need
    if half == 1:
        where = "do przerwy"
    else:
        where = "w 2. połowie"
    return "w %s ostatnich meczów obu drużyn (każda z różnymi rywalami) %s %s" % (words, what, where)


def make_live_lines(dist, st):
    """Linie over w połowie oraz — gdy pozostały już tylko gole bieżącej połowy (przerwa/2H) — w meczu."""
    lines = []
    half = dist["half"]
    tot = st.get("tot")
    gsf = dist["goals_so_far"]
    phase = st.get("phase")
    for need, ch in (dist.get("by_need") or {}).items():
        if ch.get("p") is None or not (ch.get("n") or 0):
            continue
        why = _need_why(need, half, phase, dist.get("t"), ch.get("frac"), ch.get("k"), ch.get("n"))
        half_line = gsf + need - 0.5
        items = [(
            "half", half_line,
            "over%d5_%dh" % (int(half_line), half),
            "Over %s gola w %s" % (_fmt_line(half_line), "1. połowie" if half == 1 else "2. połowie"),
        )]
        # W 1. połowie over w meczu NIE równa się golom do przerwy (może paść w 2H) — nie zmyślamy.
        if phase in ("ht", "2h") and tot is not None:
            match_line = tot + need - 0.5
            items.append((
                "match", match_line,
                "over%d5" % int(match_line),
                "Over %s w meczu" % _fmt_line(match_line),
            ))
        for scope, line, key, play in items:
            lines.append({
                "need": need, "scope": scope, "line": line, "key": key, "play": play,
                "chance": ch["p"], "chance_pct": ch["pct"],
                "frac": ch["frac"], "k": ch["k"], "n": ch["n"],
                "p_emp": ch["p_emp"], "p_pois": ch["p_pois"], "p_raw": ch["p_raw"],
                "lam": ch.get("lam"), "why": why,
                "chance_hist": ch.get("p_hist", ch["p"]),
                "odds": None, "implied": None, "implied_pct": None,
                "edge": None, "edge_pp": None, "verdict": None,
            })
    return lines


def implied_from_odds(over, under=None):
    """Szansa sugerowana przez kurs. Gdy jest też kurs na poniżej, zdejmuję marżę bukmachera."""
    try:
        over = float(over)
    except (TypeError, ValueError):
        return None
    if not over or over <= 1:
        return None
    p = 1.0 / over
    try:
        under = float(under) if under is not None else None
    except (TypeError, ValueError):
        under = None
    if under and under > 1:
        s = p + 1.0 / under
        if s > 0:
            return p / s
    return p


def _odds_under_from_map(odds_map, key, line=None):
    om = odds_map or {}
    if key:
        for k in (str(key) + "_under", (key, "under")):
            if om.get(k):
                return om.get(k)
    return (line or {}).get("odds_under")


def apply_odds_to_line(line, odds_map=None):
    """Dokleja kurs, porównanie z szansą i TAK/NIE (wewnętrznie PLUS/MINUS). Bez kursu — bez oceny."""
    out = dict(line)
    om = odds_map or {}
    key = out.get("key")
    price = None
    if key is not None and om.get(key) is not None:
        try:
            price = float(om.get(key))
        except (TypeError, ValueError):
            price = None
    if price is None and out.get("odds") is not None:
        try:
            price = float(out.get("odds"))
        except (TypeError, ValueError):
            price = None
    if not price or price <= 1:
        out["odds"] = out["implied"] = out["implied_pct"] = out["edge"] = out["edge_pp"] = out["verdict"] = None
        out["unreliable"] = False
        return fill_offer(out)
    under = _odds_under_from_map(om, key, out)
    implied = implied_from_odds(price, under)
    chance = out.get("chance")
    edge = (chance - implied) if chance is not None and implied is not None else None
    out["odds"] = price
    if under:
        try:
            out["odds_under"] = float(under)
        except (TypeError, ValueError):
            pass
    out["implied"] = None if implied is None else round(implied, 4)
    out["implied_pct"] = None if implied is None else pct(implied)
    out["edge"] = None if edge is None else round(edge, 4)
    out["edge_pp"] = None if edge is None else int(round(100 * edge))
    if edge is not None and edge > SANITY_EDGE + 1e-12:
        out["verdict"] = "UNRELIABLE"
        out["unreliable"] = True
    else:
        out["unreliable"] = False
        out["verdict"] = "PLUS" if (edge is not None and edge >= EDGE_PLUS - 1e-12) else "MINUS"
    return fill_offer(out)


def _best_offer_line(card):
    lines = [ln for ln in (card.get("lines") or []) if ln]
    plus = [ln for ln in lines if ln.get("verdict") == "PLUS" and not ln.get("unreliable")]
    if plus:
        pool = plus
    else:
        ok = [ln for ln in lines if ln.get("odds") and not ln.get("unreliable") and ln.get("verdict") != "UNRELIABLE"]
        pool = ok or [ln for ln in lines if ln.get("odds")] or lines
    if not pool:
        return card
    return max(pool, key=lambda ln: (
        ln.get("edge") if ln.get("edge") is not None else -99,
        ln.get("n") or 0, ln.get("chance") or 0))


def live_recommend(card):
    best = _best_offer_line(card)
    why = (card.get("detail") or {}).get("similar_why") or (best or {}).get("why")
    return offer_text(best, why=why)



def tempo_from_live(stats, minute=None, half=1):
    """Tempo z bieżących strzałów, celnych, rożnych i groźnych ataków. Brak danych = bez zmiany."""
    stats = stats or {}
    def tot_pair(v):
        if not v:
            return None
        if isinstance(v, (list, tuple)) and len(v) >= 2 and v[0] is not None and v[1] is not None:
            try:
                return int(v[0]) + int(v[1])
            except (TypeError, ValueError):
                return None
        return None
    shots = tot_pair(stats.get("tot") or stats.get("shots"))
    sot = tot_pair(stats.get("on"))
    cor = tot_pair(stats.get("cor"))
    da = tot_pair(stats.get("da"))
    try:
        m = int(str(minute).split("+")[0])
    except (TypeError, ValueError):
        m = None
    # za wcześnie, żeby karać brak wydarzeń
    if m is None or m < 8:
        if all(x is None or x == 0 for x in (shots, sot, cor, da)):
            return {"mult": 1.0, "label": None, "note": None, "early": True}
    # oczekiwane łącznie (obie bramki) na 45 min, ostrożne progi
    scale = 1.0
    if m and m > 0:
        scale = min(1.0, max(0.2, float(m) / float(HALF_LEN if half == 1 else max(m, 1))))
    # niskie: mało wszystkiego względem czasu
    low = False
    high = False
    bits = []
    if m and m >= 12:
        if shots is not None:
            bits.append("%d strzałów" % shots)
        if sot is not None:
            bits.append("%d celnych" % sot)
        if cor is not None:
            bits.append("%d rożnych" % cor)
        # po 15+ min 1H: niskie gdy strzały <= 3 i celne <= 1 i rożne <= 1
        if shots is not None:
            exp_shots = 8.0 * (m / 45.0)   # typowe ~8 strzałów łącznie do przerwy (orientacyjnie)
            if (shots <= max(3, 0.65 * exp_shots)
                    and (sot is None or sot <= 1)
                    and (cor is None or cor <= 2)):
                low = True
            if shots >= max(10, 1.6 * exp_shots) or (sot is not None and sot >= 6) or (da is not None and da >= 40 and m <= 30):
                high = True
        elif da is not None and m >= 15:
            if da <= 8:
                low = True
            elif da >= 40:
                high = True
    if low and not high:
        return {"mult": 0.75, "label": "niskie", "note": ", ".join(bits) or None, "early": False}
    if high and not low:
        return {"mult": 1.12, "label": "wysokie", "note": ", ".join(bits) or None, "early": False}
    if shots is None and sot is None and cor is None and da is None:
        return {"mult": 1.0, "label": None, "note": None, "early": False}
    return {"mult": 1.0, "label": "umiarkowane", "note": ", ".join(bits) or None, "early": False}


def _apply_tempo_to_p(p, need, lam, mult):
    if p is None or mult is None or abs(mult - 1.0) < 1e-9:
        return p
    if need == 1:
        # p = 1-e^{-λ} → przelicz λ, przeskaluj tempo
        try:
            lam_p = -math.log(max(1e-9, 1.0 - min(0.999, float(p))))
        except (ValueError, TypeError):
            lam_p = lam
        if lam_p is None:
            return p
        return max(0.0, min(0.99, 1.0 - math.exp(-max(0.0, lam_p) * float(mult))))
    if lam is not None:
        return pois_over(need - 0.5, max(0.0, float(lam) * float(mult)))
    return p


def factor_lines(card, tempo=None):
    """Proste zdania do szczegółów: tempo, kurs, typ rozgrywek, forma, czas."""
    out = []
    det_src = (card.get("dist_meta") or {})
    if not det_src and card.get("half"):
        det_src = {"comp_label": None}
    kind_label = None
    dist = card.get("dist") or {}
    # meta stored on card
    kind_label = card.get("comp_label")
    if kind_label:
        base_pct = card.get("comp_base_pct")
        half = card.get("half") or 1
        if base_pct is not None and half == 1:
            out.append("Typ rozgrywek: %s (w takich meczach gol w 1. połowie padał w ok. %d%% spotkań z zebranej historii)." % (kind_label, int(base_pct)))
        else:
            out.append("Typ rozgrywek: %s." % kind_label)
    out.append("Ostatnie 5 meczów każdej drużyny waży więcej niż starsze.")
    rem = card.get("remaining_minutes")
    tref = card.get("minutes") if card.get("minutes") is not None else card.get("t_ref")
    if rem is not None and card.get("phase") in ("1h", "2h", "ht"):
        out.append("Minuty bez kolejnego gola obniżają szansę — zostało ok. %d min tej połowy." % int(rem))
    tempo = tempo or card.get("tempo") or {}
    if tempo.get("label"):
        extra = (" (%s)" % tempo["note"]) if tempo.get("note") else ""
        out.append("Tempo meczu: %s%s." % (tempo["label"], extra))
    # market from primary / best line
    best = _primary_line(card.get("lines") or []) or card
    if best and best.get("implied_pct") is not None:
        out.append("Kurs bukmachera sugeruje %d%%." % int(best["implied_pct"]))
    elif best and not best.get("odds"):
        out.append("Brak kursu Superbet — nie typuję TAK, szansę z samej historii ograniczam.")
    if best and best.get("chance_hist") is not None and best.get("chance") is not None:
        hp = pct(best["chance_hist"])
        cp = best.get("chance_pct")
        if hp is not None and cp is not None and int(hp) != int(cp):
            out.append("Z samej historii wyszło ok. %d%%, po poprawkach (kurs, tempo, typ rozgrywek) pokazuję %d%%." % (int(hp), int(cp)))
    if best and best.get("chance_capped"):
        out.append("Szansa została ograniczona — bez zgody kursu nie pokazuję tak wysokiej wartości.")
    return out


def finalize_live_chance(card, tempo=None):
    """Kotwica rynkowa (≥50%), tempo live, limit bez kursu i limit późnej minuty na 1 gol."""
    if not card:
        return card
    tempo = tempo or card.get("tempo")
    if tempo:
        card["tempo"] = tempo
    phase = card.get("phase")
    try:
        minute = card.get("minutes")
        minute = int(str(minute).split("+")[0]) if minute is not None and minute != "" else None
    except (TypeError, ValueError):
        minute = None
    if card.get("phase") == "ht":
        t_for_late = 45 if (card.get("half") == 1) else 45
        # 2H at HT is start of half — not late
        late = False if card.get("half") == 2 else True
    else:
        half = card.get("half") or 1
        start = 0 if half == 1 else 45
        late = minute is not None and (minute - start) >= LATE_NEED1_MINUTE
    lines = []
    for ln in (card.get("lines") or []):
        ln = dict(ln)
        p0 = ln.get("chance_hist")
        if p0 is None:
            p0 = ln.get("chance")
            ln["chance_hist"] = p0
        p = p0
        need = ln.get("need") or 1
        lam = ln.get("lam")
        if tempo and tempo.get("mult") and p is not None:
            p = _apply_tempo_to_p(p, need, lam, tempo["mult"])
        implied = ln.get("implied")
        if implied is not None and p is not None:
            w = MARKET_WEIGHT if need == 1 else 0.35
            p = w * float(implied) + (1.0 - w) * float(p)
        capped = False
        missing = not ln.get("odds") or float(ln.get("odds") or 0) <= 1
        if missing and p is not None and p > NO_ODDS_CAP + 1e-12:
            p = NO_ODDS_CAP
            capped = True
        # nie pokazuj >80% na jednego gola późno w połowie, chyba że kurs też jest krótki
        if (need == 1 and late and p is not None and p > LATE_NEED1_CAP + 1e-12):
            agrees = implied is not None and float(implied) >= (LATE_NEED1_CAP - 0.02)
            if not agrees:
                p = LATE_NEED1_CAP
                capped = True
        ln["chance"] = None if p is None else max(0.0, min(0.99, float(p)))
        ln["chance_pct"] = pct(ln["chance"])
        ln["chance_capped"] = capped
        ln["missing_odds"] = bool(missing)
        # przelicz TAK/NIE od nowej szansy (apply_odds zostawia szansę z historii)
        if missing:
            ln["edge"] = ln["edge_pp"] = ln["verdict"] = None
            ln["unreliable"] = False
            ln["implied"] = ln.get("implied")
        elif implied is not None and ln["chance"] is not None:
            edge = ln["chance"] - float(implied)
            ln["edge"] = round(edge, 4)
            ln["edge_pp"] = int(round(100 * edge))
            if edge > SANITY_EDGE + 1e-12:
                ln["verdict"] = "UNRELIABLE"
                ln["unreliable"] = True
            else:
                ln["unreliable"] = False
                ln["verdict"] = "PLUS" if edge >= EDGE_PLUS - 1e-12 else "MINUS"
        fill_offer(ln)
        if missing:
            ln["worth"] = None  # nigdy TAK bez kursu
            if capped:
                wt = ln.get("worth_text") or ""
                if "ograniczam" not in wt:
                    ln["worth_text"] = (wt + " Szansę z samej historii ograniczam — bez kursu nie typuję.").strip()
        lines.append(ln)
    card["lines"] = lines
    factors = factor_lines(card, tempo)
    card["factors"] = factors
    if card.get("detail") is not None:
        det = dict(card["detail"])
        det["factors"] = factors
        card["detail"] = det
    return card


def apply_odds_to_match(card, odds_map=None, tempo=None):
    if not card:
        return card
    out = dict(card)
    out["lines"] = [apply_odds_to_line(ln, odds_map) for ln in (card.get("lines") or [])]
    if out.get("mode") == "live" or out.get("phase") in ("1h", "2h", "ht"):
        finalize_live_chance(out, tempo=tempo)
    out["recommend"] = live_recommend(out)
    best = _best_offer_line(out)
    for k in ("play", "key", "chance", "chance_pct", "frac", "k", "n",
              "odds", "implied", "implied_pct", "edge", "edge_pp", "verdict",
              "chance_label", "worth", "worth_because", "worth_text", "line", "scope", "why",
              "chance_hist", "chance_capped", "missing_odds"):
        if best.get(k) is not None:
            out[k] = best.get(k)
    if out.get("detail") is not None:
        det = dict(out["detail"])
        det["recommend"] = out["recommend"]
        raw = det.get("next_goal") or ""
        if " Kurs Superbet @" in raw:
            raw = raw.split(" Kurs Superbet @", 1)[0].strip()
        det["next_goal"] = raw
        prim = _primary_line(out.get("lines") or []) or best
        why = det.get("similar_why") or (prim or {}).get("why")
        det["offer"] = offer_text(prim, why=why)
        out["detail"] = det
    if out.get("mode") == "live":
        out["summary"] = live_line_summary(out, best)
    else:
        out["summary"] = pre_line_summary(out, best)
    return out


def live_line_summary(card, line):
    line = fill_offer(dict(line or card))
    home, away = _team_str(card, "home"), _team_str(card, "away")
    sc = card.get("score") or "–"
    tl = card.get("time_label") or (("%d'" % card["minutes"]) if card.get("minutes") is not None else "live")
    header = "%s – %s, %s, %s" % (home, away, sc, tl)
    why = (card.get("detail") or {}).get("similar_why") or line.get("why")
    return offer_text(line, why=why, header=header)


def live_summary(card, why=None):
    return live_line_summary(card, card)


def _primary_line(lines):
    for ln in lines or []:
        if ln.get("need") == 1 and ln.get("scope") == "half":
            return ln
    return (lines or [None])[0]


def _sides_clause(ch):
    h = (ch or {}).get("home") or {}
    a = (ch or {}).get("away") or {}
    bits = []
    for lab, side in (("gospodarze", h), ("goście", a)):
        words = frac_words(side.get("k"), side.get("n")) if side.get("n") else None
        if not words and side.get("frac"):
            words = str(side["frac"]).replace("/", " z ")
        if words:
            bits.append("%s w %s" % (lab, words))
    if not bits:
        return ""
    return " (osobno %s)" % ", ".join(bits)


def _half_already_text(half, goals_so_far):
    side = "1. połowie" if half == 1 else "2. połowie"
    if not goals_so_far:
        return "W %s nie padł jeszcze gol." % side
    if goals_so_far == 1:
        return "W %s padł już 1 gol." % side
    return "W %s padły już %d gole." % (side, goals_so_far)


def _total_goals_phrase(total, half):
    if half == 1:
        if total <= 1:
            return "gol do przerwy padł"
        return "%d+ gole do przerwy padły" % total
    if total <= 1:
        return "gol w 2. połowie padł"
    return "%d+ gole w 2. połowie padły" % total


def _team_half_scoring(matches, half, t=None):
    """Gole strzelone przez tę drużynę w danej połowie (z minut, jeśli są)."""
    buckets_keys = ("0-15", "16-30", "31-45+") if half == 1 else ("46-60", "61-75", "76-90+")
    buckets = {b: 0 for b in buckets_keys}
    goals = n = n_scored = rem = 0
    n_tl = 0
    for m in matches or []:
        if not m:
            continue
        if m.get("timeline_ok") and m.get("scored") is not None:
            sc = 0
            n_tl += 1
            for g in m.get("scored") or []:
                if g.get("half") != half:
                    continue
                sc += 1
                b = g.get("bucket")
                if b in buckets:
                    buckets[b] += 1
                if t is not None:
                    try:
                        if int(g.get("minute") or 0) > int(t):
                            rem += 1
                    except (TypeError, ValueError):
                        pass
        else:
            sc = m.get("gf_1h") if half == 1 else m.get("gf_2h")
            if sc is None:
                continue
        n += 1
        goals += int(sc or 0)
        if (sc or 0) >= 1:
            n_scored += 1
    return {
        "goals": goals, "n": n, "k_scored": n_scored, "frac_scored": frac(n_scored, n),
        "buckets": buckets, "rem_goals": rem, "n_tl": n_tl,
    }


def phase_scoring(home, away, half, t=None):
    H = _team_half_scoring(home.get("matches") or [], half, t)
    A = _team_half_scoring(away.get("matches") or [], half, t)
    h_rate = (H["goals"] / H["n"]) if H["n"] else 0
    a_rate = (A["goals"] / A["n"]) if A["n"] else 0
    if H["n"] and A["n"]:
        leader = "home" if h_rate > a_rate else ("away" if a_rate > h_rate else "equal")
    elif H["n"]:
        leader = "home"
    elif A["n"]:
        leader = "away"
    else:
        leader = None
    return {"home": H, "away": A, "leader": leader, "half": half}


def _plus_counts(vals, thresholds=(1, 2, 3, 4)):
    out = []
    for th in thresholds:
        out.append((th, sum(1 for v in vals if v >= th)))
    return out


def _current_fav_side(meta):
    meta = meta or {}
    try:
        o1 = float(meta.get("o1"))
        o2 = float(meta.get("o2"))
    except (TypeError, ValueError):
        return None
    if o1 <= 1 or o2 <= 1:
        return None
    if o1 < o2:
        return "home"
    if o2 < o1:
        return "away"
    return None


def _lead_role(hs, aws, fav):
    if hs is None or aws is None:
        return None
    if hs == aws:
        return "draw"
    leader = "home" if hs > aws else "away"
    if not fav:
        return None
    return "fav_lead" if leader == fav else "dog_lead"


def _pitch_score_at(m, t):
    """Wynik (gosp., gość) o minucie t. None, gdy nie da się tego uczciwie odczytać."""
    try:
        t = int(t)
    except (TypeError, ValueError):
        t = None
    if m.get("timeline_ok") and t is not None:
        hs = aws = 0
        for g in m.get("goals") or []:
            try:
                mn = int(g.get("minute"))
            except (TypeError, ValueError):
                continue
            if mn > t:
                continue
            if g.get("scored") == "home":
                hs += 1
            elif g.get("scored") == "away":
                aws += 1
        return hs, aws
    if t is not None and t <= 45 and m.get("ht_home") is not None and m.get("ht_away") is not None:
        if t < 45 and (m.get("tot_1h") or 0) != 0:
            return None
        return int(m["ht_home"]), int(m["ht_away"])
    return None


def _team_lead_at(m, t):
    sc = _pitch_score_at(m, t)
    if not sc:
        return None
    hs, aws = sc
    gf, ga = (hs, aws) if m.get("is_home") else (aws, hs)
    if gf > ga:
        return "lead"
    if gf < ga:
        return "trail"
    return "draw"


def _wanted_lead(fav, role, team_is_current_home):
    if role == "draw":
        return "draw"
    if role not in ("fav_lead", "dog_lead") or not fav:
        return None
    team_is_fav = (fav == "home") if team_is_current_home else (fav == "away")
    if role == "fav_lead":
        return "lead" if team_is_fav else "trail"
    return "lead" if not team_is_fav else "trail"


def _ht_ok(m, ht_mode, p1, g1):
    if not ht_mode:
        return True
    if ht_mode == "exact" and p1 and p1[0] is not None and p1[1] is not None:
        return m.get("ht_home") == int(p1[0]) and m.get("ht_away") == int(p1[1])
    if ht_mode == "count" and g1 is not None:
        return m.get("tot_1h") == int(g1)
    return True


def _reach_minute(m, half, gsf, use_minute, t):
    if use_minute:
        return t
    if int(gsf or 0) == 0:
        return 45 if half == 2 else 0
    mins = sorted(_half_goal_minutes(m, half))
    if len(mins) < int(gsf):
        return None
    return mins[int(gsf) - 1]


def _rem_after_state(m, half, gsf, use_minute, t):
    gsf = int(gsf or 0)
    if use_minute:
        if m.get("timeline_ok") and t is not None:
            mins = _half_goal_minutes(m, half)
            if sum(1 for x in mins if x <= int(t)) != gsf:
                return None
            return sum(1 for x in mins if x > int(t))
        if gsf != 0:
            return None
        if half == 2 and m.get("tot_2h") is not None and (t is None or int(t) <= 45):
            return int(m["tot_2h"])
        if half == 1 and m.get("tot_1h") is not None and (t is None or int(t) <= 0):
            return int(m["tot_1h"])
        if half == 2 and m.get("first_2h_known") and not m.get("any_2h"):
            return 0
        return None
    tot = m.get("tot_1h") if half == 1 else m.get("tot_2h")
    if tot is None or int(tot) < gsf:
        return None
    return int(tot) - gsf


def similar_situation(home, away, st, t=None, goals_so_far=0, meta=None):
    """Bloku podobnych meczów: wynik 1H, gole w tej połowie, kto prowadzi, minuta.

    Gdy jest mniej niż 5 meczów, po kolei odpuszczamy: faworyta, dokładny wynik 1H
    (zostaje liczba goli), minutę. Liczby tylko z danych.
    """
    st = st or {}
    phase = st.get("phase")
    if phase not in ("1h", "ht", "2h"):
        return None
    half = 1 if phase == "1h" else 2
    gsf = int(goals_so_far or 0)
    p1 = st.get("p1")
    g1 = st.get("goals_1h")
    score = st.get("score")
    hs, aws = score if score and len(score) == 2 else (None, None)
    fav = _current_fav_side(meta if meta is not None else st)
    role = _lead_role(hs, aws, fav)
    skipped = []
    relaxed = []
    use_leader = (role == "draw") or (role in ("fav_lead", "dog_lead") and fav)
    if not use_leader and role in ("fav_lead", "dog_lead"):
        skipped.append("nie sprawdzam, kto jest faworytem, bo nie ma kursu 1 albo 2")
    elif not use_leader and role is None and (hs is None):
        skipped.append("nie wiadomo, kto prowadzi")
    ht_mode = None
    if half == 2:
        if p1 and p1[0] is not None and p1[1] is not None:
            ht_mode = "exact"
        elif g1 is not None:
            ht_mode = "count"
    score_exact = score if (half == 1 and hs is not None and aws is not None) else None
    use_minute = t is not None

    def collect():
        out_h, out_a = [], []
        wanted_h = _wanted_lead(fav, role, True) if use_leader else None
        wanted_a = _wanted_lead(fav, role, False) if use_leader else None
        for side, prof, wanted in (("home", home, wanted_h), ("away", away, wanted_a)):
            bucket = out_h if side == "home" else out_a
            for m in (prof.get("matches") or []) if prof else []:
                if not m:
                    continue
                if half == 2 and ht_mode and not _ht_ok(m, ht_mode, p1, g1):
                    continue
                if half == 1 and score_exact:
                    lt = t if use_minute else _reach_minute(m, 1, gsf, False, t)
                    sc = _pitch_score_at(m, lt) if lt is not None else None
                    if sc != score_exact:
                        continue
                rem = _rem_after_state(m, half, gsf, use_minute, t)
                if rem is None:
                    continue
                if use_leader and wanted:
                    lt = _reach_minute(m, half, gsf, use_minute, t)
                    got = _team_lead_at(m, lt) if lt is not None else None
                    if got != wanted:
                        continue
                bucket.append(rem)
        return out_h, out_a

    h, a = collect()
    n = len(h) + len(a)
    if n < SIMILAR_MIN_N and use_leader:
        use_leader = False
        relaxed.append("kto prowadzi (faworyt albo nie)")
        h, a = collect()
        n = len(h) + len(a)
    if n < SIMILAR_MIN_N and half == 2 and ht_mode == "exact":
        ht_mode = "count"
        relaxed.append("dokładny wynik 1. połowy — zostaje tylko liczba goli w 1. połowie")
        h, a = collect()
        n = len(h) + len(a)
    if n < SIMILAR_MIN_N and half == 1 and score_exact:
        score_exact = None
        relaxed.append("dokładny wynik — zostaje tylko liczba goli w tej połowie")
        h, a = collect()
        n = len(h) + len(a)
    if n < SIMILAR_MIN_N and use_minute:
        use_minute = False
        relaxed.append("dokładnie tę samą minutę")
        h, a = collect()
        n = len(h) + len(a)

    bits = []
    if half == 2:
        if ht_mode == "exact" and p1:
            bits.append("1. połowa skończyła się %d:%d" % (int(p1[0]), int(p1[1])))
        elif ht_mode == "count" and g1 is not None:
            if int(g1) == 0:
                bits.append("w 1. połowie nie padł gol")
            elif int(g1) == 1:
                bits.append("w 1. połowie padł 1 gol")
            else:
                bits.append("w 1. połowie padły %d gole" % int(g1))
        if gsf == 0:
            bits.append("w 2. połowie jeszcze nie było gola")
        elif gsf == 1:
            bits.append("w 2. połowie padł już 1 gol")
        else:
            bits.append("w 2. połowie padły już %d gole" % gsf)
    else:
        if score_exact:
            bits.append("wynik %d:%d" % (int(score_exact[0]), int(score_exact[1])))
        if gsf == 0:
            bits.append("w 1. połowie jeszcze nie było gola")
        elif gsf == 1:
            bits.append("w 1. połowie padł już 1 gol")
        else:
            bits.append("w 1. połowie padły już %d gole" % gsf)
    if use_leader:
        if role == "fav_lead":
            bits.append("prowadzi faworyt (drużyna z niższym kursem)")
        elif role == "dog_lead":
            bits.append("prowadzi drużyna z wyższym kursem (nie faworyt)")
        elif role == "draw":
            bits.append("jest remis")
    if use_minute and t is not None:
        bits.append("stan z przerwy" if phase == "ht" else "stan z tej samej minuty (ok. %d.)" % int(t))
    intro = "Szukam podobnych ostatnich meczów obu drużyn (każda z różnymi rywalami): %s." % ", ".join(bits) if bits else "Szukam podobnych ostatnich meczów obu drużyn (każda z różnymi rywalami)."
    k = sum(1 for rem in (h + a) if rem >= 1)
    kh, nh = sum(1 for rem in h if rem >= 1), len(h)
    ka, na = sum(1 for rem in a if rem >= 1), len(a)
    if n == 0:
        body = "Nie znalazłem podobnego meczu — nie da się uczciwie powiedzieć, jak często pada kolejny gol."
        why = None
    else:
        body = "W %d z %d podobnych ostatnich meczów obu drużyn (każda z różnymi rywalami) padł kolejny gol" % (k, n)
        sides = []
        if nh:
            sides.append("gospodarze w %d z %d" % (kh, nh))
        if na:
            sides.append("goście w %d z %d" % (ka, na))
        if sides:
            body += " (osobno %s)" % ", ".join(sides)
        body += "."
        more = []
        for extra in (2, 3, 4):
            kk = sum(1 for rem in (h + a) if rem >= extra)
            if kk:
                more.append("jeszcze co najmniej %d %s %s" % (
                    extra, "gol" if extra == 1 else "gole", _w_meczach(kk)))
        if more:
            body += " Potem padło %s." % ", ".join(more)
        why = "w %d z %d podobnych ostatnich meczów obu drużyn (każda z różnymi rywalami) padł kolejny gol" % (k, n)
        if n < 8:
            body += " " + _mala_proba(n)
    if skipped:
        body += " " + "; ".join(s[0].upper() + s[1:] for s in skipped) + "."
    if relaxed:
        body += " Żeby zebrać choć 5 podobnych meczów, odpuściłem: %s." % "; ".join(relaxed)
    return {
        "text": intro + " " + body,
        "why": why,
        "n": n, "k": k,
        "relaxed": relaxed, "skipped": skipped,
        "home": {"k": kh, "n": nh}, "away": {"k": ka, "n": na},
    }


def prior_half_context(home, away, st, t=None, goals_so_far=0):
    """Rozkład goli bieżącej połowy pod warunkiem wyniku wcześniejszej (np. 1H 0:0)."""
    if (st or {}).get("phase") not in ("ht", "2h"):
        return None
    matches = [m for m in ((home.get("matches") or []) + (away.get("matches") or []))
               if m and m.get("tot_1h") is not None and m.get("tot_2h") is not None]
    n_all = len(matches)
    if not n_all:
        return None
    p1 = st.get("p1")
    label = None
    sample = []
    if p1 and p1[0] is not None and p1[1] is not None:
        hh, aa = int(p1[0]), int(p1[1])
        sample = [m for m in matches if m.get("ht_home") == hh and m.get("ht_away") == aa]
        label = "1. połowa %d:%d" % (hh, aa)
    elif st.get("goals_1h") is not None:
        g1 = int(st["goals_1h"])
        sample = [m for m in matches if m.get("tot_1h") == g1]
        if g1 == 0:
            label = "1. połowa 0:0"
        elif g1 == 1:
            label = "w 1. połowie padł 1 gol"
        else:
            label = "w 1. połowie padły %d gole" % g1
    if not label:
        return None
    n = len(sample)
    twos = [m.get("tot_2h") or 0 for m in sample]
    parts = ["%d+ goli w 2. połowie w %d" % (th, k) for th, k in _plus_counts(twos)]
    text = "W %s ostatnich meczach obu drużyn (każda z różnymi rywalami) %s." % (frac_words(n, n_all) or "0 z %d" % n_all, label)
    if n:
        text += " Z tych %s: %s." % (_pl_nom_mecze(n), ", ".join(parts))
        if n < 8:
            text += " " + _mala_proba(n)
    else:
        text += " Brak meczów z takim wynikiem 1. połowy — nie da się podać rozkładu 2. połowy."
    given_now = None
    if st.get("phase") == "2h" and t is not None and n:
        rems = []
        for m in sample:
            if not m.get("timeline_ok"):
                continue
            mins = _half_goal_minutes(m, 2)
            by_t = sum(1 for x in mins if x <= int(t))
            if by_t != int(goals_so_far):
                continue
            rems.append(sum(1 for x in mins if x > int(t)))
        if rems:
            n2 = len(rems)
            now_parts = []
            for extra in (1, 2, 3):
                k = sum(1 for rem in rems if rem >= extra)
                total = int(goals_so_far) + extra
                now_parts.append("%d+ gole łącznie w 2. połowie w %s" % (total, frac(k, n2)))
            given_now = (
                "Przy tym samym wyniku 1. połowy i %d golach już w 2. połowie po %d. minucie: %s."
                % (int(goals_so_far), int(t), "; ".join(now_parts))
            )
            if n2 < 8:
                given_now += " " + _mala_proba(n2)
    return {"text": text, "given_now": given_now, "n": n, "n_all": n_all, "label": label}


def priced_next_goal_text(base, card):
    """Zostawia zdanie o kolejnym golu bez żargonu — kurs jest w ofercie."""
    return (base or "").strip()


def build_live_detail(card, dist, home, away, st):
    """Teksty szczegółów: stan, warunek 2+/3+, kolejny gol, kto strzela, przedziały 15-min."""
    half = dist.get("half") or card.get("half") or 1
    gsf = dist.get("goals_so_far")
    if gsf is None:
        gsf = card.get("goals_so_far") or 0
    t = dist.get("t") if dist.get("t") is not None else st.get("t_ref")
    phase = st.get("phase") or card.get("phase")
    score = card.get("score") or st.get("score_s") or "–"
    tl = card.get("time_label") or st.get("time_label") or "live"
    state = "Wynik %s, %s. %s" % (score, tl, _half_already_text(half, gsf))
    by = dist.get("by_need") or {}
    # kolejny gol = 1+ w pozostałym czasie
    nxt = by.get(1) or {}
    if half == 1:
        when = "do końca 1. połowy od %d. minuty" % t if t is not None else "do przerwy"
    elif phase == "ht":
        when = "do końca 2. połowy od przerwy"
    else:
        when = "do końca 2. połowy od %d. minuty" % t if t is not None else "w 2. połowie"
    nxt_frac = nxt.get("frac") or "—"
    nxt_s = "Szansa na kolejnego gola %s: ok. %s%% (padł w %s ostatnich meczach obu drużyn (każda z różnymi rywalami)%s)." % (
        when, nxt["pct"] if nxt.get("pct") is not None else "—", nxt_frac, _sides_clause(nxt))
    cond = []
    for extra in (1, 2, 3):
        total = int(gsf) + extra
        if total < 2 and extra != 1:
            continue
        if extra == 1:
            continue  # 1+ jest w zdaniu o kolejnym golu; 2+ i 3+ (łącznie w połowie) niżej
        ch = by.get(extra) or {}
        if not ch.get("n"):
            continue
        phrase = _total_goals_phrase(total, half)
        cond.append("%s w %s ostatnich meczach obu drużyn (każda z różnymi rywalami)%s." % (
            phrase[0].upper() + phrase[1:], ch.get("frac") or "—", _sides_clause(ch)))
    # jeśli gsf>=1, pierwsze cond to (gsf+1)+ czyli 2+ gdy był 1 gol — user explicitly wants 2+ and 3+
    # extra 1 was skipped; add total=gsf+1 if gsf>=1 (2+ when 1 already)
    if int(gsf) >= 1:
        ch = by.get(1) or {}
        if ch.get("n"):
            phrase = _total_goals_phrase(int(gsf) + 1, half)
            cond.insert(0, "%s w %s ostatnich meczach obu drużyn (każda z różnymi rywalami)%s." % (
                phrase[0].upper() + phrase[1:], ch.get("frac") or "—", _sides_clause(ch)))
    sc = phase_scoring(home, away, half, t)
    H, A = sc["home"], sc["away"]
    fase = "1. połowie" if half == 1 else "2. połowie"
    if sc["leader"] == "home":
        who = "częściej strzelali gospodarze"
    elif sc["leader"] == "away":
        who = "częściej strzelali goście"
    elif sc["leader"] == "equal":
        who = "obie strony strzelały podobnie"
    else:
        who = None
    scoring = None
    if who and (H["n"] or A["n"]):
        scoring = (
            "W %s %s: %d goli gospodarzy w %d meczach, wobec %d goli gości w %d meczach "
            "(gola w tej fazie strzelili w %s i %s meczach)." % (
                fase, who, H["goals"], H["n"], A["goals"], A["n"],
                H.get("frac_scored") or "—", A.get("frac_scored") or "—")
        )
    buckets_txt = None
    if H.get("n_tl") or A.get("n_tl"):
        keys = list(H["buckets"].keys())
        parts = []
        for b in keys:
            parts.append("%s gospod. %d, goście %d" % (b.replace("-", "–"), H["buckets"].get(b, 0), A["buckets"].get(b, 0)))
        buckets_txt = "Przedziały 15-min w %s: %s." % (fase, "; ".join(parts))
    prior = prior_half_context(home, away, st, t, gsf)
    sim = similar_situation(home, away, st, t, gsf, meta=card)
    nxt_s = priced_next_goal_text(nxt_s, card)
    similar_why = (sim or {}).get("why")
    offer = offer_text(_primary_line(card.get("lines") or []) or card, why=similar_why)
    return {
        "state": state,
        "next_goal": nxt_s,
        "cond": cond,
        "given_1h": None if not prior else prior["text"],
        "given_1h_now": None if not prior else prior.get("given_now"),
        "similar": None if not sim else sim["text"],
        "similar_why": similar_why,
        "offer": offer,
        "scoring": scoring,
        "buckets": buckets_txt,
        "recommend": card.get("recommend"),
        "scoring_raw": sc,
        "factors": card.get("factors") or [],
    }


def live_match_card(home, away, meta=None):
    """Mecz live: rozkład 1+/2+/3+ goli w pozostałym czasie bieżącej połowy i linie over."""
    meta = meta or {}
    st = parse_live_state(meta)
    if st.get("phase") in (None, "ft") or st.get("t_ref") is None:
        return None
    if st["phase"] == "1h":
        half = 1
        goals_so_far = st.get("goals_1h")
        if goals_so_far is None:
            goals_so_far = st.get("tot")
    elif st["phase"] in ("ht", "2h"):
        half = 2
        goals_so_far = st.get("goals_2h")
    else:
        return None
    if goals_so_far is None:
        return None
    dist = remaining_goals_dist(home, away, st["t_ref"], half, goals_so_far, meta=meta)
    if not (dist.get("n") or 0):
        return None
    lines = make_live_lines(dist, st)
    if not lines:
        return None
    primary = _primary_line(lines)
    used = (dist.get("by_need") or {}).get(1) or (dist.get("by_need") or {}).get(primary["need"])
    market = {"key": primary["key"], "play": primary["play"], "use_cond": True, "cond": used,
              "enter": "teraz, %s, wynik %s" % (st["time_label"], st.get("score_s") or "–")}
    why = primary.get("why") or ""
    card = _live_card(home, away, meta, market, used, [why], st)
    card["line"] = primary.get("line")
    card["frac_cond"] = primary.get("frac")
    card["lines"] = lines
    card["dist"] = {
        need: {"frac": ch.get("frac"), "k": ch.get("k"), "n": ch.get("n"),
               "chance": ch.get("p"), "chance_pct": ch.get("pct"),
               "p_emp": ch.get("p_emp"), "p_pois": ch.get("p_pois"), "lam": ch.get("lam"),
               "home": ch.get("home"), "away": ch.get("away")}
        for need, ch in (dist.get("by_need") or {}).items()
    }
    card["half"] = half
    card["goals_so_far"] = int(goals_so_far)
    card["tot"] = st.get("tot")
    card["goals_1h"] = st.get("goals_1h")
    card["goals_2h"] = st.get("goals_2h")
    card["comp_kind"] = dist.get("comp_kind")
    card["comp_label"] = dist.get("comp_label")
    card["comp_base_p"] = dist.get("comp_base_p")
    card["comp_base_pct"] = pct(dist.get("comp_base_p"))
    card["remaining_minutes"] = dist.get("remaining_minutes")
    card["chance_hist"] = (used or {}).get("p_hist", (used or {}).get("p"))
    # Informacyjnie: czy 1+ w połowie spełnia dawny próg. Nie ukrywamy meczu.
    card["qualified"] = bool(
        (used.get("n") or 0) >= MIN_N and used.get("p") is not None and used["p"] >= P_MIN
        and (home.get("n") or 0) >= MIN_N and (away.get("n") or 0) >= MIN_N)
    card["summary"] = live_summary(card, why)
    card["detail"] = build_live_detail(card, dist, home, away, st)
    card["recommend"] = live_recommend(card)
    return card


def pick_live_for_match(home, away, meta=None, p_min=P_MIN, min_n=MIN_N):
    """Karta meczu live (wszystkie linie). p_min/min_n zostają dla zgodności, nie ukrywają meczu."""
    return live_match_card(home, away, meta)


def _line_sort_key(ln):
    return (-(ln.get("edge") if ln.get("edge") is not None else -99),
            -ln.get("n", 0), -ln.get("chance", 0), ln.get("ts") or 0)


def _as_name(v, fallback=None):
    """Wyciąga nazwę drużyny, gdy pole jest słownikiem albo puste."""
    if isinstance(v, str) and v.strip() and v.strip() not in ("?", "None"):
        return v.strip()
    if isinstance(v, dict):
        for k in ("name", "home", "away", "label", "team"):
            s = v.get(k)
            if isinstance(s, str) and s.strip():
                return s.strip()
        return fallback
    if v is None or isinstance(v, (list, tuple, dict)):
        return fallback
    s = str(v).strip()
    if not s or s.startswith("{") or s in ("None", "undefined"):
        return fallback
    return s


def sanitize_team_names(row, src=None):
    """home/away zawsze jako zwykły napis — nigdy obiekt z ułamkiem."""
    if not row:
        return row
    src = src or row
    h = _as_name(src.get("home"), _as_name(src.get("home_name")))
    a = _as_name(src.get("away"), _as_name(src.get("away_name")))
    # jeśli src ma złe home, spróbuj jeszcze row (np. karta)
    if h is None:
        h = _as_name(row.get("home"), _as_name(row.get("home_name")))
    if a is None:
        a = _as_name(row.get("away"), _as_name(row.get("away_name")))
    if h:
        row["home"] = h
        row["home_name"] = _as_name(src.get("home_name"), h) or h
    if a:
        row["away"] = a
        row["away_name"] = _as_name(src.get("away_name"), a) or a
    return row


def _is_pre_1h_line(ln):
    """Czy typ należy do zakładki przedmeczowej (tylko 1. połowa)."""
    if not ln:
        return False
    if ln.get("scope") == "1h":
        return True
    key = str(ln.get("key") or "")
    if key in PRE_1H_KEYS or key.endswith("_1h"):
        return True
    play = (ln.get("play") or "").lower()
    return "1. połowie" in play and "over" in play


def _is_match_over05(ln):
    """Krótki over 0.5 w całym meczu — nie typujemy go przed meczem."""
    if not ln:
        return False
    try:
        line = float(ln.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    if (ln.get("scope") == "match" or ln.get("key") == "over05") and line <= 0.5 + 1e-9:
        return True
    play = (ln.get("play") or "").lower()
    return play in ("over 0.5 w meczu", "over 0.5 gola w meczu")


def _pre_group(ln):
    """Kolejność typów przed meczem bez kursu: najpierw over 0.5, potem 1.5, potem 2.5 w 1. połowie."""
    if not _is_pre_1h_line(ln):
        return 9
    try:
        line = float(ln.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    if abs(line - 0.5) < 1e-9:
        return 0
    if abs(line - 1.5) < 1e-9:
        return 1
    if abs(line - 2.5) < 1e-9:
        return 2
    return 9


_MATCH_COPY_KEYS = (
    "eventId", "home", "away", "home_name", "away_name", "score",
    "time_label", "phase", "minutes", "period", "tournament",
    "category", "start_warsaw", "day_warsaw", "ts", "warnings",
    "recommend", "o1", "o2",
    "state_ts", "state_warsaw", "state_age_seconds", "state_label", "pending",
)


def _row_from_match_line(m, ln):
    """Wiersz rankingu: dane linii + nazwy zawsze z karty meczu (nie z ułamka strony)."""
    skip = {"home", "away", "home_name", "away_name", "home_side", "away_side"}
    row = {k: v for k, v in dict(ln or {}).items() if k not in skip}
    for k in _MATCH_COPY_KEYS:
        if m.get(k) is not None:
            row[k] = m[k]
    sanitize_team_names(row, m)
    fill_offer(row)
    return row


def best_rank_line(m, p_min=LIVE_RANK_P_MIN, min_odds=None):
    """Najlepsza linia meczu do rankingu: szansa ≥ p_min i jest kurs (opcjonalnie min. kurs)."""
    cands = []
    for ln in (m.get("lines") or []):
        if ln.get("chance") is None or ln["chance"] < p_min:
            continue
        odds = ln.get("odds")
        if not odds or odds <= 1:
            continue
        if min_odds is not None and odds < min_odds - 1e-12:
            continue
        if ln.get("unreliable") or ln.get("verdict") == "UNRELIABLE":
            continue
        cands.append(ln)
    if not cands:
        return None
    cands.sort(key=_line_sort_key)
    return cands[0]


def best_pre_line_no_odds(m, p_min=LIVE_RANK_P_MIN):
    """Gdy nie ma kursu: tylko linie 1. połowy (0.5, potem 1.5, potem 2.5)."""
    cands = []
    for ln in (m.get("lines") or []):
        if ln.get("chance") is None or ln["chance"] < p_min:
            continue
        if (ln.get("n") or 0) < MIN_N:
            continue
        if _is_match_over05(ln) or _pre_group(ln) >= 9:
            continue
        cands.append(ln)
    if not cands and m.get("chance") is not None and m["chance"] >= p_min and not _is_match_over05(m):
        if _pre_group(m) < 9:
            cands = [m]
    if not cands:
        return None
    best_g = min(_pre_group(ln) for ln in cands)
    pool = [ln for ln in cands if _pre_group(ln) == best_g]
    return max(pool, key=lambda x: (x.get("chance") or 0, x.get("n") or 0))


def rank_live_lines(matches, limit=MAX_PICKS, p_min=LIVE_RANK_P_MIN, min_odds=None):
    """Ranking: każdy mecz tylko raz; pozycję wyznacza jego najlepsza linia."""
    rows = []
    seen = set()
    for m in matches or []:
        if not m:
            continue
        eid = m.get("eventId")
        uniq = eid if eid is not None else id(m)
        if uniq in seen:
            continue
        ln = best_rank_line(m, p_min, min_odds=min_odds)
        if not ln:
            continue
        seen.add(uniq)
        row = _row_from_match_line(m, ln)
        if m.get("mode") == "prematch":
            row["summary"] = pre_line_summary(m, ln)
        else:
            row["summary"] = live_line_summary(m, ln)
        rows.append(row)
    rows.sort(key=_line_sort_key)
    for i, r in enumerate(rows[:limit], 1):
        r["rank"] = i
    return rows[:limit]


def rank_pre_lines(matches, limit=MAX_PICKS, p_min=LIVE_RANK_P_MIN, min_odds=PRE_MIN_ODDS):
    """Przed meczem: tylko over w 1. połowie, z kursem Superbet ≥ ok. 1.30, każdy mecz raz."""
    narrowed = []
    for m in matches or []:
        if not m:
            continue
        mm = dict(m)
        mm["lines"] = [ln for ln in (m.get("lines") or []) if _is_pre_1h_line(ln)]
        if not mm["lines"]:
            continue
        narrowed.append(mm)
    return rank_live_lines(narrowed, limit=limit, p_min=p_min, min_odds=min_odds)


def rank_unique_by_chance(matches, limit=MAX_PICKS, p_min=LIVE_RANK_P_MIN, pre=False):
    """Jeden wiersz na mecz, bez wymogu kursu — zanim dojdą kursy albo gdy żaden nie przechodzi progu."""
    rows = []
    seen = set()
    for m in matches or []:
        if not m:
            continue
        eid = m.get("eventId")
        uniq = eid if eid is not None else id(m)
        if uniq in seen:
            continue
        if pre or m.get("mode") == "prematch":
            ln = best_pre_line_no_odds(m, p_min)
        else:
            cands = [ln for ln in (m.get("lines") or [])
                     if ln.get("chance") is not None and ln["chance"] >= p_min and (ln.get("n") or 0) >= MIN_N]
            if not cands and m.get("chance") is not None and m["chance"] >= p_min:
                cands = [m]
            ln = max(cands, key=lambda x: (x.get("chance") or 0, x.get("n") or 0)) if cands else None
        if not ln:
            continue
        seen.add(uniq)
        row = _row_from_match_line(m, ln)
        if m.get("mode") == "live":
            row["summary"] = live_line_summary(m, ln)
        else:
            row["summary"] = pre_line_summary(m, ln)
        rows.append(row)
    rows.sort(key=lambda r: (-(r.get("chance") or 0), -r.get("n", 0), r.get("ts") or 0))
    for i, r in enumerate(rows[:limit], 1):
        r["rank"] = i
    return rows[:limit]


def rank_picks(cards, limit=MAX_PICKS):
    cards = [c for c in cards if c and c.get("chance") is not None]
    cards.sort(key=lambda c: (-c["chance"], -c.get("n", 0), c.get("ts") or 0))
    for i, c in enumerate(cards[:limit], 1):
        c["rank"] = i
    return cards[:limit]


_PUBLIC_KEYS = ("rank", "eventId", "start_warsaw", "day_warsaw", "ts", "home", "away",
            "home_name", "away_name", "tournament", "category", "mode", "minutes", "score",
            "play", "enter", "why", "summary", "chance", "chance_pct", "frac", "p_emp", "p_pois",
            "p_raw", "lam", "n", "k", "conditional", "warnings", "key", "start_pl",
            "chance_kickoff_pct", "chance_min20_pct", "frac_min20", "n_min20",
            "odds", "time_label", "phase", "period", "frac_base", "frac_cond", "line", "qualified",
            "need", "scope", "implied", "implied_pct", "edge", "edge_pp", "verdict",
            "unreliable", "odds_under", "current",
            "state_ts", "state_warsaw", "state_age_seconds", "state_label", "pending",
            "half", "goals_so_far", "tot", "goals_1h", "goals_2h", "recommend",
            "factors", "tempo", "comp_label", "comp_kind", "comp_base_pct",
            "remaining_minutes", "chance_hist", "chance_capped", "missing_odds")


def _round_pub(out):
    for k in ("p_emp", "p_pois", "p_raw", "chance", "implied", "edge"):
        if out.get(k) is not None:
            try:
                out[k] = round(float(out[k]), 4)
            except (TypeError, ValueError):
                pass
    return out


def public_line_card(c):
    out = {k: c.get(k) for k in _PUBLIC_KEYS}
    sanitize_team_names(out, c)
    for k in ("chance_label", "worth", "worth_because", "worth_text"):
        if c.get(k) is not None:
            out[k] = c.get(k)
    return _round_pub(out)


def public_match_card(c):
    out = {k: c.get(k) for k in _PUBLIC_KEYS}
    sanitize_team_names(out, c)
    out["lines"] = [public_line_card(ln) for ln in (c.get("lines") or [])]
    dist = c.get("dist") or {}
    out["dist"] = {}
    for need, ch in dist.items():
        block = dict(ch)
        for k in ("chance", "p_emp", "p_pois"):
            if block.get(k) is not None:
                try:
                    block[k] = round(float(block[k]), 4)
                except (TypeError, ValueError):
                    pass
        out["dist"][str(need)] = block
    if c.get("detail"):
        out["detail"] = c.get("detail")
    out["recommend"] = c.get("recommend")
    return _round_pub(out)


def public_card(c):
    """Pola na kartę w UI — bez surowych list meczów."""
    if c and c.get("lines"):
        return public_match_card(c)
    return public_line_card(c)


CHANCE_RULE = (
    "Na żywo liczę szansę na gole w pozostałym czasie tej połowy: ile goli zwykle padało na minutę "
    "w ostatnich meczach obu drużyn (ost. 5 waży więcej), a nie samo „gol w 1. połowie w X z N meczów”. "
    "Gdy jest kurs Superbet na tę linię, biorę szansę z kursu (bez marży bukmachera) jako kotwicę — co najmniej połowę wagi — "
    "a historia tylko delikatnie koryguje. "
    "Dokładam typ rozgrywek (np. puchar europejski albo liga), a gdy mecz trwa: tempo z strzałów, celnych, rożnych i groźnych ataków. "
    "Im dłużej nie ma gola, tym szansa spada, bo zostaje mniej minut. "
    "Bez kursu nie typuję TAK i nie pokazuję bardzo wysokiej szansy z samej historii. "
    "Późno w połowie nie pokazuję powyżej 80% na jednego gola, chyba że kurs też tak mówi. "
    "Opłaca się, gdy nasza szansa jest wyraźnie wyższa od kursu (o co najmniej 3 na 100). "
    "Gdy szansa mocno mija się z kursem, oznaczam typ jako niepewne dane. "
    "Przed meczem typuję tylko gole w 1. połowie (over 0.5, 1.5 i 2.5) i tylko przy kursie ok. 1,30 albo wyższym. "
    "To nie jest pewność, że tak będzie, i nic nie jest stawiane."
)

