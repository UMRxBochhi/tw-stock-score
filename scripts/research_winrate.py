# 勝率專題：「智邦型」（營收上修、創新高、前瞻本益比低、外資持股高、長期成長）單檔贏 0050 的機率，以及上車／下車方式
"""
- 每月 11 日後第一個交易日檢查；選股範圍：市值 ≥ 100 億、近 20 日平均成交值 ≥ 3000 萬
- 勝率 = 單檔在觀察期（訊號日起 6／12／24 個月）報酬 > 同期 0050；含股利報酬（0050 同樣含；PRICE_ONLY=1 改回只算股價）
- 上車方式：等待期間錢放 0050；觀察期固定從訊號日起算 12 個月，所以不同上車方式可以直接比較
- 下車方式：賣掉的錢轉回 0050（之後超額報酬就固定），每次換到 0050 扣 0.3%
- 「新訊號」＝這個月符合、上個月不符合（避免同一段上漲被重複計算）
輸出 data/research_winrate.json
"""
import bisect
import json
import math
import os
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402

HIST = u.DATA / "hist"
MIN_CAP = 1e10
D12 = 250


def shift(k, n):
    y, m = int(k[:-2]), int(k[-2:]) - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


def avail_rev_key(t):
    y, m, d = int(t[:4]), int(t[4:6]), int(t[6:])
    m -= 1 if d >= 11 else 2
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y - 1911}{m:02d}"


def est12(h, k):
    cur = [h.get(shift(k, i), [None])[0] for i in range(12)]
    p3 = [h.get(shift(k, i + 12), [None])[0] for i in range(3)]
    if None in cur or None in p3 or not sum(p3):
        return None
    g = max(-0.5, min(1.5, sum(cur[:3]) / sum(p3) - 1))
    return sum(cur) * (1 + g)


def load_prices():
    dates, rows = [], []
    for ym in u.hist_months("px"):
        d = u.hist_read("px", ym)
        for t in sorted(d):
            dates.append(t)
            rows.append({c: (v[0], v[2] if len(v) > 2 else None) for c, v in d[t].items()
                         if v and v[0] and (c == "0050" or u.is_stock(c))})
    N = len(dates)
    codes = sorted({c for r in rows for c in r})
    close = {c: [None] * N for c in codes}
    val = {c: [None] * N for c in codes}
    for i, r in enumerate(rows):
        for c, (p, v) in r.items():
            close[c][i] = p
            val[c][i] = v
    return dates, close, val


DIVS = {} if os.environ.get("PRICE_ONLY") else u.load_divs()     # PRICE_ONLY=1：只算股價（舊版，比較用）


def growth(arr, dates=None, c=None):
    """累積報酬指數（含股利）：除權息／減資當天乘上還原係數；還原後單日仍 ±15% 以上視為 0；停止交易就維持最後值"""
    return u.tr_growth(arr, dates or [None] * len(arr), DIVS.get(c) if dates else None)


def main():
    cache = os.environ.get("WR_CACHE")
    pxc = Path(cache + ".px") if cache else None
    if pxc and pxc.exists() and Path(cache).exists():
        dates, G = pickle.loads(pxc.read_bytes())
        close = val = None
    else:
        dates, close, val = load_prices()
        G = {c: growth(a, dates, c) for c, a in close.items()}
        if pxc:
            pxc.write_bytes(pickle.dumps((dates, G)))
    N = len(dates)
    u.log(f"交易日 {N}（{dates[0]}~{dates[-1]}），股票 {len(G)}")
    E = G["0050"]
    for i in range(1, N):
        if E[i] is None:
            E[i] = E[i - 1]

    months = []
    for i, t in enumerate(dates):
        if int(t[6:]) >= 11 and (i == 0 or dates[i - 1][:6] != t[:6] or int(dates[i - 1][6:]) < 11):
            months.append(i)
    mset = set(months)

    if cache and Path(cache).exists():
        snaps = pickle.loads(Path(cache).read_bytes())
    else:
        snaps = build_snaps(dates, close, val, G, E, months)
        if cache:
            Path(cache).write_bytes(pickle.dumps(snaps))
    u.log(f"每月快照 {len(snaps)} 個")

    up = lambda m, n=1: (m.get("rev_up_n") or 0) >= n
    hi = lambda m: m.get("rev_hi") is not None and m["rev_hi"] >= 0
    zf = lambda m, v: m.get("pe_zf") is not None and m["pe_zf"] < v
    qf = lambda m, v: (m.get("qf") or 0) >= v
    cg = lambda m, v: m.get("cagr3") is not None and m["cagr3"] >= v
    bt = lambda m, v: m.get("beta") is not None and m["beta"] >= v
    site = lambda m: up(m) and qf(m, 30) and cg(m, 0.10)
    zhi = lambda m: up(m, 3) and hi(m) and zf(m, -1.0)
    PROF = {
        "全部大型股（基準）": lambda m: True,
        "前瞻本益比 < −1σ（只看便宜）": lambda m: zf(m, -1.0),
        "營收上修 ≥ 3＋創新高": lambda m: up(m, 3) and hi(m),
        "智邦型（上修≥3＋創新高＋前瞻 < −1σ）": zhi,
        "智邦型＋外資≥30%＋3年成長≥15%": lambda m: zhi(m) and qf(m, 30) and cg(m, 0.15),
        "網站選股名單條件（上修＋外資≥30%＋3年成長≥10%）": site,
        "網站條件＋前瞻 < −1σ": lambda m: site(m) and zf(m, -1.0),
        "網站條件＋前瞻 < −0.5σ": lambda m: site(m) and zf(m, -0.5),
        "網站條件＋前瞻 > +0.5σ（偏貴）": lambda m: site(m) and m.get("pe_zf") is not None and m["pe_zf"] > 0.5,
        "網站條件＋創新高": lambda m: site(m) and hi(m),
        "網站條件＋beta ≥ 1.2": lambda m: site(m) and bt(m, 1.2),
        "網站條件＋上修 ≥ 6": lambda m: site(m) and up(m, 6),
        "智邦型放寬（上修≥3＋創新高＋前瞻 < −0.5σ）＋外資≥30%＋3年成長≥15%": lambda m: up(m, 3) and hi(m) and zf(m, -0.5) and qf(m, 30) and cg(m, 0.15),
        "網站條件＋創新高＋前瞻 < −0.5σ": lambda m: site(m) and hi(m) and zf(m, -0.5),
    }
    FOCUS = ("智邦型（上修≥3＋創新高＋前瞻 < −1σ）", "智邦型＋外資≥30%＋3年成長≥15%",
             "智邦型放寬（上修≥3＋創新高＋前瞻 < −0.5σ）＋外資≥30%＋3年成長≥15%",
             "網站選股名單條件（上修＋外資≥30%＋3年成長≥10%）", "網站條件＋前瞻 < −1σ", "網站條件＋創新高",
             "網站條件＋創新高＋前瞻 < −0.5σ")

    def rel(c, i0, i1):
        g0, g1 = G[c][i0], G[c][i1]
        if not g0 or g1 is None:
            return None
        return (g1 / g0) / (E[i1] / E[i0])

    out = {"說明": __doc__.strip(), "期間": f"{dates[0]}~{dates[-1]}"}
    sig_all, sig_new = {}, {}
    prev_ok = {}
    for k in PROF:
        sig_all[k], sig_new[k] = [], []
        prev_ok[k] = set()
    for i in months:
        s = snaps.get(i)
        if not s:
            continue
        for k, f in PROF.items():
            ok = {c for c, m in s.items() if m.get("cap", 0) >= MIN_CAP and f(m)}
            for c in ok:
                sig_all[k].append((i, c))
                if c not in prev_ok[k]:
                    sig_new[k].append((i, c))
            prev_ok[k] = ok

    # ---- 1. 勝率：單檔、組合、不同持有期間
    def pct(xs):
        return round(sum(xs) / len(xs) * 100, 1) if xs else None

    def q(xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(len(xs) * p))] * 100, 1) if xs else None

    split = "20230615"
    win = {}
    for k in PROF:
        row = {}
        for lab, H in (("6 個月", 125), ("12 個月", D12), ("24 個月", 500)):
            xs = [(i, rel(c, i, i + H)) for i, c in sig_all[k] if i + H < N]
            xs = [(i, r) for i, r in xs if r is not None]
            ns = [(i, rel(c, i, i + H)) for i, c in sig_new[k] if i + H < N]
            ns = [(i, r) for i, r in ns if r is not None]
            if len(xs) < 20:
                continue
            # 組合：每個月買全部符合的股票（等權），看組合贏 0050 的比例
            by_m = {}
            for i, r in xs:
                by_m.setdefault(i, []).append(r)
            port = [sum(v) / len(v) for v in by_m.values() if len(v) >= 3]
            row[lab] = {"樣本（股票×月）": len(xs), "單檔勝率": pct([r > 1 for _, r in xs]),
                        "單檔超額中位數": q([r - 1 for _, r in xs], 0.5),
                        "最差 10%": q([r - 1 for _, r in xs], 0.1),
                        "新訊號數": len(ns), "新訊號勝率": pct([r > 1 for _, r in ns]),
                        "前段/後段單檔勝率": f"{pct([r > 1 for i, r in xs if dates[i] < split])} / {pct([r > 1 for i, r in xs if dates[i] >= split])}",
                        "組合勝率（每月買全部符合的）": pct([r > 1 for r in port]), "組合月數": len(port),
                        "平均每月檔數": round(len(xs) / max(1, len(by_m)), 1)}
        win[k] = row
        u.log(f"{k}: " + json.dumps(row.get("12 個月"), ensure_ascii=False))
    out["勝率"] = win

    # ---- 2. 中途波動：最後贏 0050 的股票，中途曾經跌多深（停損設太緊會被洗掉）
    def path_stats(c, i0, H=D12):
        g0 = G[c][i0]
        mn_abs, mn_rel, pk, dd_pk = 1.0, 1.0, 1.0, 0.0
        for k in range(1, H + 1):
            g = G[c][i0 + k] / g0
            r = g / (E[i0 + k] / E[i0])
            mn_abs, mn_rel = min(mn_abs, g), min(mn_rel, r)
            pk = max(pk, g)
            dd_pk = min(dd_pk, g / pk - 1)
        return mn_abs - 1, mn_rel - 1, dd_pk

    mae = {}
    for k in ("全部大型股（基準）", "智邦型（上修≥3＋創新高＋前瞻 < −1σ）", "智邦型＋外資≥30%＋3年成長≥15%",
              "網站選股名單條件（上修＋外資≥30%＋3年成長≥10%）", "網站條件＋前瞻 < −1σ"):
        W, L = [], []
        for i, c in sig_new[k]:
            if i + D12 >= N or not G[c][i]:
                continue
            r = rel(c, i, i + D12)
            if r is None:
                continue
            (W if r > 1 else L).append(path_stats(c, i))
        if not W or not L:
            continue
        tb = {}
        for x in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
            tb[f"從買進價跌 {int(x * 100)}%"] = {"贏家中途碰到的比例": pct([a <= -x for a, _, _ in W]),
                                            "輸家中途碰到的比例": pct([a <= -x for a, _, _ in L])}
        for x in (0.15, 0.20, 0.30):
            tb[f"從最高點回落 {int(x * 100)}%"] = {"贏家中途碰到的比例": pct([d <= -x for _, _, d in W]),
                                             "輸家中途碰到的比例": pct([d <= -x for _, _, d in L])}
        for x in (0.10, 0.20):
            tb[f"落後 0050 {int(x * 100)}%"] = {"贏家中途碰到的比例": pct([b <= -x for _, b, _ in W]),
                                           "輸家中途碰到的比例": pct([b <= -x for _, b, _ in L])}
        mae[k] = {"新訊號：贏家/輸家": f"{len(W)}/{len(L)}",
                  "贏家中途最深跌幅（中位數）": q([a for a, _, _ in W], 0.5),
                  "贏家從高點最大回落（中位數）": q([d for _, _, d in W], 0.5),
                  "輸家中途最深跌幅（中位數）": q([a for a, _, _ in L], 0.5), "各門檻": tb}
        u.log(f"中途波動 {k}: " + json.dumps(mae[k], ensure_ascii=False)[:800])
    out["中途波動"] = mae

    # ---- 3. 上車方式：觀察期固定 12 個月，等待期間放 0050
    def entry_ratio(c, i0, how):
        end = i0 + D12
        if how == "立刻買":
            return rel(c, i0, end)
        if how == "分 3 個月買（每月 1/3）":
            rs = [rel(c, i0 + j * 21, end) for j in range(3)]
            return sum(rs) / 3 if None not in rs else None
        if how == "分 6 個月買":
            rs = [rel(c, i0 + j * 21, end) for j in range(6)]
            return sum(rs) / 6 if None not in rs else None
        g0 = G[c][i0]
        for k in range(1, 61):
            g, m = G[c][i0 + k] / g0, E[i0 + k] / E[i0]
            x = float(re.search(r"(\d+)%", how).group(1)) / 100
            hit = ((how.startswith("等股價回檔") and g <= 1 - x) or
                   (how.startswith("等落後 0050") and g / m <= 1 - x))
            if hit:
                return rel(c, i0 + k, end)
        if how.endswith("沒等到就不買"):
            return 1.0
        return rel(c, i0 + 60, end)

    ENTRY = ["立刻買", "分 3 個月買（每月 1/3）", "分 6 個月買",
             "等股價回檔 5% 再買（3 個月內沒等到就買）", "等股價回檔 10% 再買（3 個月內沒等到就買）",
             "等股價回檔 10% 再買（沒等到就不買）",
             "等落後 0050 5% 再買（3 個月內沒等到就買）", "等落後 0050 10% 再買（3 個月內沒等到就買）"]
    ent = {}
    for k in FOCUS:
        ent[k] = {}
        sig = [(i, c) for i, c in sig_new[k] if i + D12 < N and G[c][i]]
        for how in ENTRY:
            rs = [entry_ratio(c, i, how) for i, c in sig]
            rs = [r for r in rs if r is not None]
            if rs:
                ent[k][how] = {"樣本": len(rs), "勝率": pct([r > 1 for r in rs]), "超額中位數": q([r - 1 for r in rs], 0.5),
                               "超額平均": round((sum(rs) / len(rs) - 1) * 100, 1), "最差 10%": q([r - 1 for r in rs], 0.1)}
        u.log(f"上車 {k}: " + json.dumps(ent[k], ensure_ascii=False)[:900])
    out["上車方式"] = ent

    # ---- 4. 下車方式：賣掉轉回 0050；觀察期 12 個月
    def exit_ratio(c, i0, rule):
        g0 = G[c][i0]
        pk, rpk = 1.0, 1.0
        for k in range(1, D12 + 1):
            i = i0 + k
            g = G[c][i] / g0
            m = E[i] / E[i0]
            r = g / m
            pk, rpk = max(pk, g), max(rpk, r)
            st = {"g": g, "pk": pk, "r": r, "rpk": rpk, "k": k}
            snap_m = (snaps.get(i) or {}).get(c) if i in mset else None
            if rule(st, snap_m):
                return r * (1 - 0.003), k
        return rel(c, i0, i0 + D12), None

    zf_hi = lambda v: (lambda st, sm: sm is not None and sm.get("pe_zf") is not None and sm["pe_zf"] > v)
    dn = lambda st, sm: sm is not None and sm.get("rev_up_n") == 0
    r3n = lambda st, sm: sm is not None and sm.get("rev3") is not None and sm["rev3"] < 0
    EXIT = {
        "抱滿 12 個月": lambda st, sm: False,
        "從買進價跌 10% 停損": lambda st, sm: st["g"] <= 0.90,
        "從買進價跌 20% 停損": lambda st, sm: st["g"] <= 0.80,
        "從最高點回落 15%": lambda st, sm: st["g"] / st["pk"] <= 0.85,
        "從最高點回落 20%": lambda st, sm: st["g"] / st["pk"] <= 0.80,
        "從最高點回落 30%": lambda st, sm: st["g"] / st["pk"] <= 0.70,
        "落後 0050 達 10%": lambda st, sm: st["r"] <= 0.90,
        "落後 0050 達 20%": lambda st, sm: st["r"] <= 0.80,
        "賺 20% 就走": lambda st, sm: st["g"] >= 1.20,
        "賺 30% 就走": lambda st, sm: st["g"] >= 1.30,
        "前瞻本益比 > 0σ 就賣（回到平均）": zf_hi(0.0),
        "前瞻本益比 > +0.5σ 就賣": zf_hi(0.5),
        "前瞻本益比 > +1σ 就賣": zf_hi(1.0),
        "營收預估轉下修就賣": dn,
        "近 3 月營收年增轉負就賣": r3n,
        "下修就賣，或從最高點回落 30%": lambda st, sm: dn(st, sm) or st["g"] / st["pk"] <= 0.70,
        "前瞻 > +1σ 或下修就賣": lambda st, sm: zf_hi(1.0)(st, sm) or dn(st, sm),
        "賺 10% 就走": lambda st, sm: st["g"] >= 1.10,
        "賺 15% 就走": lambda st, sm: st["g"] >= 1.15,
        "賺 20% 就走，或從買進價跌 30%": lambda st, sm: st["g"] >= 1.20 or st["g"] <= 0.70,
        "前瞻 > 0σ 就賣，或從買進價跌 30%": lambda st, sm: zf_hi(0.0)(st, sm) or st["g"] <= 0.70,
        "賺 20% 或前瞻 > 0σ 就賣": lambda st, sm: st["g"] >= 1.20 or zf_hi(0.0)(st, sm),
        "前瞻 > 0σ 或落後 0050 達 20%": lambda st, sm: zf_hi(0.0)(st, sm) or st["r"] <= 0.80,
        "前瞻 > 0σ 或下修就賣": lambda st, sm: zf_hi(0.0)(st, sm) or dn(st, sm),
    }
    ex = {}
    for k in FOCUS:
        ex[k] = {}
        sig = [(i, c) for i, c in sig_new[k] if i + D12 < N and G[c][i]]
        hold = {(i, c): rel(c, i, i + D12) for i, c in sig}
        for name, rule in EXIT.items():
            rs, early, shaken, saved = [], 0, 0, 0
            for i, c in sig:
                h = hold[(i, c)]
                if h is None:
                    continue
                r, kk = exit_ratio(c, i, rule)
                rs.append(r)
                if kk is not None:
                    early += 1
                    shaken += h > 1 and r <= 1
                    saved += h <= 1 and r > h
            if rs:
                ex[k][name] = {"勝率": pct([r > 1 for r in rs]), "超額中位數": q([r - 1 for r in rs], 0.5),
                               "超額平均": round((sum(rs) / len(rs) - 1) * 100, 1), "最差 10%": q([r - 1 for r in rs], 0.1),
                               "提前下車比例": pct([True] * early + [False] * (len(rs) - early)),
                               "被洗掉（抱著會贏、賣了變輸）": pct([True] * shaken + [False] * (len(rs) - shaken)),
                               "賣得好（抱著會更差）": pct([True] * saved + [False] * (len(rs) - saved))}
        u.log(f"下車 {k}: " + json.dumps(ex[k], ensure_ascii=False)[:1500])
    out["下車方式"] = ex

    # ---- 6. 三檔組合：在什麼市場狀態、候選條件、挑法、下車方式下，3 檔等權組合贏 0050 的機率最高
    #       每月一組，觀察期 12 個月；個股下車後轉 0050；前段 2019~2022、後段 2023~ 分開看
    import random
    rng = random.Random(11)

    def mstate(i):
        w = [x for x in E[max(0, i - 249):i + 1] if x]
        dd_ = E[i] / max(w) - 1
        ma = sum(E[i - 119:i + 1]) / 120 if i >= 119 else None
        r12 = E[i] / E[i - 250] - 1 if i >= 250 else None
        rs = [E[k] / E[k - 1] - 1 for k in range(max(1, i - 59), i + 1)]
        mu = sum(rs) / len(rs)
        vol = (sum((x - mu) ** 2 for x in rs) / len(rs)) ** 0.5 * math.sqrt(250)
        t12 = (G["2330"][i] / G["2330"][i - 250]) / (E[i] / E[i - 250]) - 1 if i >= 250 and G["2330"][i - 250] else None
        return {"0050 距一年高點": "接近高點（> −5%）" if dd_ > -0.05 else "回檔（−5~−15%）" if dd_ > -0.15 else "大跌（≤ −15%）",
                "0050 半年線": None if ma is None else ("半年線上" if E[i] > ma else "半年線下"),
                "0050 近一年漲幅": None if r12 is None else ("< 0" if r12 < 0 else "0~30%" if r12 < 0.30 else "> 30%"),
                "0050 近 60 日波動": "低（< 15%）" if vol < 0.15 else "中（15~25%）" if vol < 0.25 else "高（> 25%）",
                "台積電近一年相對 0050": None if t12 is None else ("台積電領漲（> +10%）" if t12 > 0.10 else "台積電落後（< −5%）" if t12 < -0.05 else "差不多"),
                "全部": "全部"}

    def pct_rank(d):
        xs = sorted(v for v in d.values() if v is not None)
        n = len(xs)
        return {c: (bisect.bisect_left(xs, v) + bisect.bisect_right(xs, v) - 1) / 2 / max(1, n - 1)
                for c, v in d.items() if v is not None}

    def crash_sc(cands, s):
        W = {"qf": .3, "cagr3": .3, "beta": .4}
        rk = {k: pct_rank({c: s[c].get(k) for c in cands}) for k in W}
        res = {}
        for c in cands:
            parts = [(rk[k][c], w) for k, w in W.items() if c in rk[k]]
            tw = sum(w for _, w in parts)
            if tw >= 0.5:
                res[c] = sum(v * w for v, w in parts) / tw
        return res

    POOLS = {
        "網站條件（上修＋外資≥30%＋3年成長≥10%）": site,
        "網站條件＋創新高": lambda m: site(m) and hi(m),
        "高勝率型（上修≥3＋創新高＋外資≥30%＋3年成長≥15%）": lambda m: up(m, 3) and hi(m) and qf(m, 30) and cg(m, 0.15),
        "高勝率型＋前瞻 < −0.5σ": lambda m: up(m, 3) and hi(m) and qf(m, 30) and cg(m, 0.15) and zf(m, -0.5),
        "營收上修中（大跌名單候選）": lambda m: up(m),
    }
    bpre = {}

    def beta_pre(c, i):
        """網站版 beta：0050 近一年高點之前 250 個交易日"""
        pk = max(range(max(0, i - 249), i + 1), key=lambda k: E[k])
        if (c, pk) not in bpre:
            gs, ra, rm = G[c], [], []
            for k in range(max(1, pk - 249), pk + 1):
                if gs[k] and gs[k - 1] and gs[k] != gs[k - 1]:
                    ra.append(gs[k] / gs[k - 1] - 1)
                    rm.append(E[k] / E[k - 1] - 1)
            b = None
            if len(ra) >= 120:
                ma, mm = sum(ra) / len(ra), sum(rm) / len(rm)
                vm = sum((y - mm) ** 2 for y in rm)
                b = sum((x - ma) * (y - mm) for x, y in zip(ra, rm)) / vm if vm else None
            bpre[(c, pk)] = b
        return bpre[(c, pk)]

    cur_i = [None]

    def crash_sc_site(cands, s):
        s2 = {c: {**s[c], "beta": beta_pre(c, cur_i[0])} for c in cands}
        return crash_sc(cands, s2)

    RANKS = {
        "外資持股最高": lambda cands, s: {c: s[c].get("qf") or 0 for c in cands},
        "外資30＋成長30＋beta40（大跌名單排序）": crash_sc,
        "外資30＋成長30＋beta40（網站版：高點前 beta）": crash_sc_site,
        "3 年成長最高": lambda cands, s: {c: s[c].get("cagr3") or -9 for c in cands},
        "前瞻本益比最低": lambda cands, s: {c: -s[c]["pe_zf"] for c in cands if s[c].get("pe_zf") is not None},
        "連續上修最久": lambda cands, s: {c: (s[c].get("rev_up_n") or 0) + (s[c].get("qf") or 0) / 1000 for c in cands},
    }
    EX3 = {"抱滿 12 個月": EXIT["抱滿 12 個月"], "賺 20% 或前瞻回到 0σ 就轉 0050": EXIT["賺 20% 或前瞻 > 0σ 就賣"],
           "賺 20% 就轉 0050": EXIT["賺 20% 就走"]}
    rcache = {}

    def sret(c, i, en):
        if (c, i, en) not in rcache:
            rcache[(c, i, en)] = exit_ratio(c, i, EX3[en])[0]
        return rcache[(c, i, en)]

    obs = []
    for i in months:
        if i + D12 >= N or i < 250:
            continue
        s = snaps.get(i) or {}
        st = mstate(i)
        cur_i[0] = i
        for pn, pf in POOLS.items():
            cands = [c for c, m in s.items() if m.get("cap", 0) >= MIN_CAP and pf(m) and G[c][i]]
            if len(cands) < 3:
                continue
            for en in EX3:
                rr = {c: sret(c, i, en) for c in cands}
                rr = {c: r for c, r in rr.items() if r is not None}
                if len(rr) < 3:
                    continue
                for rn, rf in RANKS.items():
                    sc = rf(list(rr), s)
                    pick = sorted(sc, key=lambda c: -sc[c])[:3]
                    if len(pick) < 3:
                        continue
                    obs.append({"i": i, "pool": pn, "rank": rn, "exit": en, "st": st,
                                "r": sum(rr[c] for c in pick) / 3, "pick": pick})
                ks = list(rr)
                wins = sum(sum(rr[c] for c in rng.sample(ks, 3)) / 3 > 1 for _ in range(300))
                obs.append({"i": i, "pool": pn, "rank": "隨機 3 檔", "exit": en, "st": st, "r": None, "pwin": wins / 300})
    u.log(f"三檔組合觀察數 {len(obs)}")
    front_end = "20230101"

    def summ(xs):
        if xs[0]["r"] is None:
            return {"月數": len(xs), "勝率": round(sum(o["pwin"] for o in xs) / len(xs) * 100, 1)}
        rs = [o["r"] for o in xs]
        fr = [o["r"] for o in xs if dates[o["i"]] < front_end]
        bk = [o["r"] for o in xs if dates[o["i"]] >= front_end]
        return {"月數": len(rs), "年份數": len({dates[o["i"]][:4] for o in xs}),
                "勝率": pct([r > 1 for r in rs]), "超額平均": round((sum(rs) / len(rs) - 1) * 100, 1),
                "超額中位數": q([r - 1 for r in rs], 0.5), "最差": round((min(rs) - 1) * 100, 1),
                "前段勝率（2019~22）": pct([r > 1 for r in fr]), "前段月數": len(fr),
                "後段勝率（2023~）": pct([r > 1 for r in bk]), "後段月數": len(bk)}

    groups = {}
    for o in obs:
        for dim, v in o["st"].items():
            if v is not None:
                groups.setdefault((o["pool"], o["rank"], o["exit"], dim, v), []).append(o)
    table = []
    for (pn, rn, en, dim, v), xs in groups.items():
        table.append({"候選": pn, "挑法": rn, "下車": en, "市場狀態": f"{dim}：{v}" if dim != "全部" else "全部", **summ(xs)})
    three = {"說明": "每月挑 3 檔等權，持有 12 個月（下車後轉 0050），組合報酬 > 0050 算贏；相鄰月份的觀察期互相重疊，月數不等於獨立樣本"}
    three["全部月份（不分市場狀態）"] = sorted([x for x in table if x["市場狀態"] == "全部"], key=lambda x: -(x["勝率"] or 0))
    cand_ = [x for x in table if x["市場狀態"] != "全部" and x.get("年份數", 0) >= 3 and x["月數"] >= 12 and x["挑法"] != "隨機 3 檔"]
    three["分市場狀態（月數 ≥ 12、跨 ≥ 3 個年份），依勝率排序前 40"] = sorted(cand_, key=lambda x: -(x["勝率"] or 0))[:40]
    pool_ = [x for x in table if x["挑法"] != "隨機 3 檔" and (x.get("前段月數") or 0) >= 8 and (x.get("後段月數") or 0) >= 6]
    three["只用前段挑出的前 15 名，在後段的表現"] = sorted(pool_, key=lambda x: (-(x["前段勝率（2019~22）"] or 0), -x["前段月數"]))[:15]
    allb = sorted(x["後段勝率（2023~）"] or 0 for x in pool_)
    three["後段勝率的中位數（所有組合）"] = allb[len(allb) // 2] if allb else None
    eff, rnd = {}, {}
    for x in table:
        (rnd if x["挑法"] == "隨機 3 檔" else eff).setdefault(x["市場狀態"], []).append(x)
    three["各市場狀態的平均勝率（所有候選×挑法×下車平均）"] = {
        k: {"組合數": len(v), "平均勝率": round(sum(y["勝率"] for y in v) / len(v), 1), "最多月數": max(y["月數"] for y in v)}
        for k, v in sorted(eff.items())}
    three["隨機 3 檔的平均勝率（各市場狀態）"] = {k: round(sum(y["勝率"] for y in v) / len(v), 1) for k, v in sorted(rnd.items())}
    three["完整表（大跌名單排序／隨機）"] = sorted(
        [x for x in table if x["挑法"] in ("外資30＋成長30＋beta40（大跌名單排序）", "外資30＋成長30＋beta40（網站版：高點前 beta）", "隨機 3 檔")],
        key=lambda x: (x["候選"], x["下車"], x["市場狀態"], x["挑法"]))
    out["三檔組合"] = three

    # ---- 7. 組合條件 × 本益比位階：大盤狀態 × 候選池 × 前瞻本益比分組，看單檔與三檔組合勝率
    ZB = {"不分位階": lambda m: True,
          "便宜（< −1σ）": lambda m: zf(m, -1.0),
          "偏便宜（< −0.5σ）": lambda m: zf(m, -0.5),
          "合理（−0.5~+0.5σ）": lambda m: m.get("pe_zf") is not None and -0.5 <= m["pe_zf"] <= 0.5,
          "偏貴（> +0.5σ）": lambda m: m.get("pe_zf") is not None and m["pe_zf"] > 0.5,
          "沒有位階資料": lambda m: m.get("pe_zf") is None}
    P7 = {k: POOLS[k] for k in ("網站條件（上修＋外資≥30%＋3年成長≥10%）", "網站條件＋創新高",
                                 "高勝率型（上修≥3＋創新高＋外資≥30%＋3年成長≥15%）")}
    combo7 = []
    for pn, pf in P7.items():
        for zn, zfn in ZB.items():
            for stn in ("半年線下", "半年線上", "全部"):
                single = {"抱滿 12 個月": [], "賺 20% 就轉 0050": []}
                trio = {"抱滿 12 個月": [], "賺 20% 就轉 0050": []}
                for i in months:
                    if i + D12 >= N or i < 250:
                        continue
                    st = mstate(i)["0050 半年線"]
                    if stn != "全部" and st != stn:
                        continue
                    s = snaps.get(i) or {}
                    cands = [c for c, m in s.items() if m.get("cap", 0) >= MIN_CAP and pf(m) and zfn(m) and G[c][i]]
                    cur_i[0] = i
                    for en in single:
                        rr = {c: sret(c, i, en) for c in cands}
                        rr = {c: r for c, r in rr.items() if r is not None}
                        single[en] += [(i, r) for r in rr.values()]
                        if len(rr) >= 3:
                            sc = crash_sc_site(list(rr), s)
                            pick = sorted(sc, key=lambda c: -sc[c])[:3]
                            if len(pick) == 3:
                                trio[en].append((i, sum(rr[c] for c in pick) / 3))
                row = {"候選": pn, "位階": zn, "0050": stn}
                for en in single:
                    xs, ts = single[en], trio[en]
                    row[f"單檔｜{en}"] = {"樣本": len(xs), "月數": len({i for i, _ in xs}), "勝率": pct([r > 1 for _, r in xs]),
                                         "超額中位數": q([r - 1 for _, r in xs], 0.5)} if xs else None
                    row[f"三檔｜{en}"] = {"月數": len(ts), "年份數": len({dates[i][:4] for i, _ in ts}),
                                         "勝率": pct([r > 1 for _, r in ts]), "超額平均": round((sum(r for _, r in ts) / len(ts) - 1) * 100, 1),
                                         "最差": round((min(r for _, r in ts) - 1) * 100, 1),
                                         "前/後段勝率": f"{pct([r > 1 for i, r in ts if dates[i] < front_end])} / {pct([r > 1 for i, r in ts if dates[i] >= front_end])}"} if ts else None
                combo7.append(row)
    out["組合條件×本益比位階"] = combo7
    u.log("三檔組合 全部: " + json.dumps(three["全部月份（不分市場狀態）"][:8], ensure_ascii=False)[:2500])

    # ---- 5. 智邦自己的歷史
    zb = []
    for i in months:
        m = (snaps.get(i) or {}).get("2345")
        if not m:
            continue
        r12 = rel("2345", i, i + D12) if i + D12 < N else None
        zb.append({"日期": dates[i], "前瞻σ": None if m.get("pe_zf") is None else round(m["pe_zf"], 2),
                   "連續上修": m.get("rev_up_n"), "創新高": hi(m), "外資%": m.get("qf"),
                   "之後 12 個月相對 0050": None if r12 is None else round((r12 - 1) * 100, 1)})
    out["智邦歷史"] = zb
    (u.DATA / "research_winrate.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def build_snaps(dates, close, val, G, E, months):
    qf = {}
    for ym in u.hist_months("qf"):
        qf.update(u.hist_read("qf", ym))
    qf_dates = sorted(qf)
    revh = json.loads((HIST / "rev.json").read_text(encoding="utf-8"))
    for code, h in u.load("fundamentals.json", {}).get("rev_hist", {}).items():
        revh.setdefault(code, {}).update(h)
    pe_m = u.load("pe_monthly.json", {})
    pe_keys = sorted(pe_m)
    rev_now = u.load("fundamentals.json", {}).get("revenue", {})
    IND = {}
    fc = {}

    def factor(c, h, rk):
        if (c, rk) not in fc:
            cur = [h.get(shift(rk, i), [None])[0] for i in range(12)]
            x = est12(h, rk)
            fc[(c, rk)] = x / sum(cur) if x and None not in cur and sum(cur) > 0 else None
        return fc[(c, rk)]

    def zlog(v, xs):
        lx = [math.log(x) for x in xs]
        mu = sum(lx) / len(lx)
        sd = (sum((a - mu) ** 2 for a in lx) / len(lx)) ** 0.5
        return (math.log(v) - mu) / sd if sd > 0 else None

    snaps = {}
    for i in months:
        t = dates[i]
        j = bisect.bisect_right(qf_dates, t) - 1
        qd = qf[qf_dates[j]] if j >= 0 else {}
        rk = avail_rev_key(t)
        pk = [k for k in pe_keys if k < t[:6]][-60:]
        snap = {}
        for c, arr in close.items():
            p = arr[i]
            if c == "0050" or not p:
                continue
            vs = [x for x in val[c][max(0, i - 19):i + 1] if x]
            if len(vs) < 15 or sum(vs) / len(vs) < u.MIN_AVG_VALUE:
                continue
            qq = qd.get(c)
            if not qq or len(qq) < 2 or not qq[1]:
                continue
            m = {"cap": qq[1] * p, "qf": qq[0]}
            # beta：近 250 個交易日對 0050
            gs = G[c]
            ra, rm = [], []
            for k in range(max(1, i - 249), i + 1):
                if gs[k] and gs[k - 1] and close[c][k] and close[c][k - 1]:
                    ra.append(gs[k] / gs[k - 1] - 1)
                    rm.append(E[k] / E[k - 1] - 1)
            if len(ra) >= 120:
                ma, mm = sum(ra) / len(ra), sum(rm) / len(rm)
                vm = sum((y - mm) ** 2 for y in rm)
                m["beta"] = sum((x - ma) * (y - mm) for x, y in zip(ra, rm)) / vm if vm else None
            h = revh.get(c, {})
            ests = [est12(h, shift(rk, k)) for k in range(13)]
            if ests[0]:
                n = 0
                for k in range(12):
                    if ests[k] and ests[k + 1] and ests[k] > ests[k + 1]:
                        n += 1
                    else:
                        break
                m["rev_up_n"] = n
            cur12 = [h.get(shift(rk, k), [None])[0] for k in range(12)]
            old = [h.get(shift(rk, k + 36), [None])[0] for k in range(12)]
            if None not in cur12 + old and sum(old) > 0 and sum(cur12) > 0:
                m["cagr3"] = (sum(cur12) / sum(old)) ** (1 / 3) - 1
            r3 = [h.get(shift(rk, k), [None])[0] for k in range(3)]
            p3 = [h.get(shift(rk, k + 12), [None])[0] for k in range(3)]
            if None not in r3 + p3 and sum(p3) > 0:
                m["rev3"] = sum(r3) / sum(p3) - 1
            prev = [x for x in (h.get(shift(rk, k), [None])[0] for k in range(1, 13)) if x]
            if h.get(rk) and h[rk][0] and len(prev) >= 10:
                m["rev_hi"] = h[rk][0] / max(prev) - 1
            s0 = pe_m[pk[-1]].get(c) if pk else None
            if s0 and s0[0] and s0[1] and s0[0] > 0:
                pe_now = s0[0] * p / s0[1]
                hist = [(k, (pe_m[k].get(c) or [None])[0]) for k in pk]
                hist = [(k, v) for k, v in hist if v and v > 0]
                if len(hist) >= 24:
                    m["pe_zt"] = zlog(pe_now, [v for _, v in hist])
                    if c not in IND:
                        IND[c] = u.industry(c, rev_now) or "?"
                    fn = factor(c, h, rk)
                    if fn and fn > 0 and IND[c] not in u.FWD_SKIP_IND:
                        fh = []
                        for k, v in hist:
                            f = factor(c, h, avail_rev_key(k + "28"))
                            if f and f > 0:
                                fh.append(v / f)
                        if len(fh) >= 24:
                            m["pe_zf"] = zlog(pe_now / fn, fh)
            snap[c] = m
        snaps[i] = snap
        u.log(f"  {t}：{len(snap)} 檔")
    return snaps


if __name__ == "__main__":
    main()
