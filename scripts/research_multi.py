# 多特徵交互作用：大盤狀態 × 個股特徵，窮舉 1~4 個條件的組合，找出單檔 12 個月贏 0050 機率明顯提高、而且前後兩段都成立的組合
"""
- 樣本：每月 11 日後第一個交易日 × 市值 ≥ 100 億的股票；目標＝12 個月後贏 0050（兩種：抱滿 12 個月／賺 20% 就轉 0050）
- 特徵（各切 2~3 段，用固定、好解釋的門檻）：
  大盤：0050 距一年高點、半年線上下、0050 近一年漲幅、近 60 日波動、台積電近一年相對 0050
  個股：外資持股、3 年營收成長、營收連續上修月數、營收創新高、近 3 月營收年增、前瞻／傳統本益比位階、beta、
        近 12 個月相對 0050、距自己一年高點、市值
- 防止「剛好符合過去」：
  前段 A＝2019/12~2022/06、後段 B＝2023/07~2025/11（兩段的 12 個月觀察期不重疊）；在 A 找、到 B 驗，也反過來做
  把勝負在同一個月內隨機打亂後重跑同樣的搜尋，估計純靠運氣會冒出多少「好看」的組合
- 結果裡的勝率是單檔；另外對最穩的組合算「每月從符合的股票挑 3 檔（外資30＋成長30＋beta40）」的組合勝率
輸出 data/research_multi.json
"""
import bisect
import itertools
import json
import math
import os
import pickle
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u                 # noqa: E402
import research_winrate as W       # noqa: E402

MIN_CAP = 1e10
D12 = 250
A_END, B_START = "20220701", "20230701"


def load():
    cache = os.environ.get("WR_CACHE")
    pxc = Path(cache + ".px") if cache else None
    if cache and Path(cache).exists() and pxc.exists():
        dates, G = pickle.loads(pxc.read_bytes())
        snaps = pickle.loads(Path(cache).read_bytes())
    else:
        dates, close, val = W.load_prices()
        G = {c: W.growth(a) for c, a in close.items()}
        snaps = None
    E = G["0050"]
    for i in range(1, len(E)):
        if E[i] is None:
            E[i] = E[i - 1]
    months = [i for i, t in enumerate(dates)
              if int(t[6:]) >= 11 and (i == 0 or dates[i - 1][:6] != t[:6] or int(dates[i - 1][6:]) < 11)]
    if snaps is None:
        snaps = W.build_snaps(dates, close, val, G, E, months)
    return dates, G, E, months, snaps


def main():
    dates, G, E, months, snaps = load()
    N = len(dates)
    u.log(f"交易日 {N}，每月快照 {len(snaps)}")

    def rel(c, i0, i1):
        return (G[c][i1] / G[c][i0]) / (E[i1] / E[i0])

    def tp20(c, i0):
        g0 = G[c][i0]
        for k in range(1, D12 + 1):
            if G[c][i0 + k] / g0 >= 1.20:
                return rel(c, i0, i0 + k) * (1 - 0.003)
        return rel(c, i0, i0 + D12)

    def pct_rank(d):
        xs = sorted(v for v in d.values() if v is not None)
        n = len(xs)
        return {c: (bisect.bisect_left(xs, v) + bisect.bisect_right(xs, v) - 1) / 2 / max(1, n - 1)
                for c, v in d.items() if v is not None}

    # ---- 樣本與特徵
    S = []
    for i in months:
        if i < 250 or i + D12 >= N:
            continue
        t = dates[i]
        if not (t < A_END or t >= B_START):
            per = "gap"
        else:
            per = "A" if t < A_END else "B"
        w = E[max(0, i - 249):i + 1]
        mk = {"dd50": E[i] / max(w) - 1, "below": E[i] < sum(E[i - 119:i + 1]) / 120, "r50": E[i] / E[i - 250] - 1,
              "vol": (lambda rs: (sum((x - sum(rs) / len(rs)) ** 2 for x in rs) / len(rs)) ** 0.5 * math.sqrt(250))(
                  [E[k] / E[k - 1] - 1 for k in range(i - 59, i + 1)]),
              "tsmc": (G["2330"][i] / G["2330"][i - 250]) / (E[i] / E[i - 250]) - 1}
        for c, m in (snaps.get(i) or {}).items():
            if m.get("cap", 0) < MIN_CAP or not G[c][i] or not G[c][i - 250]:
                continue
            gw = [x for x in G[c][i - 249:i + 1] if x]
            f = dict(mk)
            f.update({k: m.get(k) for k in ("qf", "cagr3", "rev_up_n", "rev_hi", "rev3", "pe_zf", "pe_zt", "beta", "cap")})
            f["mom"] = rel(c, i - 250, i) - 1
            f["ddown"] = G[c][i] / max(gw) - 1
            r_hold, r_tp = rel(c, i, i + D12), tp20(c, i)
            S.append({"i": i, "c": c, "per": per, "f": f, "hold": r_hold, "tp": r_tp})
    u.log(f"樣本 {len(S)}（A {sum(s['per'] == 'A' for s in S)}、B {sum(s['per'] == 'B' for s in S)}）")

    def num(x, lo=None, hi=None):
        return x is not None and (lo is None or x >= lo) and (hi is None or x < hi)

    # （群組, 條件名稱, 判斷）
    CONDS = [
        ("0050距高點", "0050 接近高點（> −5%）", lambda f: f["dd50"] > -0.05),
        ("0050距高點", "0050 回檔 −5~−15%", lambda f: -0.15 < f["dd50"] <= -0.05),
        ("0050距高點", "0050 大跌（≤ −15%）", lambda f: f["dd50"] <= -0.15),
        ("半年線", "0050 半年線下", lambda f: f["below"]),
        ("半年線", "0050 半年線上", lambda f: not f["below"]),
        ("0050一年", "0050 近一年下跌", lambda f: f["r50"] < 0),
        ("0050一年", "0050 近一年漲 0~30%", lambda f: 0 <= f["r50"] < 0.3),
        ("0050一年", "0050 近一年漲 > 30%", lambda f: f["r50"] >= 0.3),
        ("波動", "大盤波動低（< 15%）", lambda f: f["vol"] < 0.15),
        ("波動", "大盤波動中", lambda f: 0.15 <= f["vol"] < 0.25),
        ("波動", "大盤波動高（≥ 25%）", lambda f: f["vol"] >= 0.25),
        ("台積電", "台積電領漲（> +10%）", lambda f: f["tsmc"] > 0.10),
        ("台積電", "台積電沒有領漲（≤ +10%）", lambda f: f["tsmc"] <= 0.10),
        ("外資", "外資持股 < 20%", lambda f: num(f["qf"], hi=20)),
        ("外資", "外資持股 20~40%", lambda f: num(f["qf"], 20, 40)),
        ("外資", "外資持股 ≥ 40%", lambda f: num(f["qf"], 40)),
        ("3年成長", "3 年成長 < 0", lambda f: num(f["cagr3"], hi=0)),
        ("3年成長", "3 年成長 0~15%", lambda f: num(f["cagr3"], 0, 0.15)),
        ("3年成長", "3 年成長 ≥ 15%", lambda f: num(f["cagr3"], 0.15)),
        ("上修", "營收預估下修中（0）", lambda f: f["rev_up_n"] == 0),
        ("上修", "連續上修 1~2 個月", lambda f: num(f["rev_up_n"], 1, 3)),
        ("上修", "連續上修 ≥ 3 個月", lambda f: num(f["rev_up_n"], 3)),
        ("創新高", "營收創 12 月新高", lambda f: num(f["rev_hi"], 0)),
        ("創新高", "營收沒創新高", lambda f: num(f["rev_hi"], hi=0)),
        ("近3月年增", "近 3 月營收年增 < 0", lambda f: num(f["rev3"], hi=0)),
        ("近3月年增", "近 3 月營收年增 0~20%", lambda f: num(f["rev3"], 0, 0.2)),
        ("近3月年增", "近 3 月營收年增 ≥ 20%", lambda f: num(f["rev3"], 0.2)),
        ("前瞻位階", "前瞻本益比 < −0.5σ", lambda f: num(f["pe_zf"], hi=-0.5)),
        ("前瞻位階", "前瞻本益比 −0.5~+0.5σ", lambda f: num(f["pe_zf"], -0.5, 0.5)),
        ("前瞻位階", "前瞻本益比 > +0.5σ", lambda f: num(f["pe_zf"], 0.5)),
        ("傳統位階", "傳統本益比 < −0.5σ", lambda f: num(f["pe_zt"], hi=-0.5)),
        ("傳統位階", "傳統本益比 −0.5~+0.5σ", lambda f: num(f["pe_zt"], -0.5, 0.5)),
        ("傳統位階", "傳統本益比 > +0.5σ", lambda f: num(f["pe_zt"], 0.5)),
        ("beta", "beta < 0.8", lambda f: num(f["beta"], hi=0.8)),
        ("beta", "beta 0.8~1.2", lambda f: num(f["beta"], 0.8, 1.2)),
        ("beta", "beta ≥ 1.2", lambda f: num(f["beta"], 1.2)),
        ("動能", "近一年落後 0050 > 20%", lambda f: f["mom"] < -0.2),
        ("動能", "近一年和 0050 差不多（±20%）", lambda f: -0.2 <= f["mom"] < 0.2),
        ("動能", "近一年領先 0050 > 20%", lambda f: f["mom"] >= 0.2),
        ("距高點", "距自己高點 < 10%", lambda f: f["ddown"] > -0.10),
        ("距高點", "距自己高點 10~30%", lambda f: -0.30 < f["ddown"] <= -0.10),
        ("距高點", "距自己高點 > 30%", lambda f: f["ddown"] <= -0.30),
        ("市值", "市值 < 300 億", lambda f: num(f["cap"], hi=3e10)),
        ("市值", "市值 300~1000 億", lambda f: num(f["cap"], 3e10, 1e11)),
        ("市值", "市值 ≥ 1000 億", lambda f: num(f["cap"], 1e11)),
    ]

    def mask(pred):
        return int("".join("1" if pred(s) else "0" for s in reversed(S)) or "0", 2)

    CM = [mask(lambda s, p=p: p(s["f"])) for _, _, p in CONDS]
    PA, PB = mask(lambda s: s["per"] == "A"), mask(lambda s: s["per"] == "B")
    month_of = [s["i"] for s in S]
    idx_by_month = {}
    for k, s in enumerate(S):
        idx_by_month.setdefault(s["i"], []).append(k)

    def bits(m):
        return [k for k, ch in enumerate(bin(m)[2:][::-1]) if ch == "1"]

    def search(WIN, max_k=4, minA=80, minB=60):
        bA, bB = (PA & WIN).bit_count() / PA.bit_count(), (PB & WIN).bit_count() / PB.bit_count()
        res = []
        groups = sorted({g for g, _, _ in CONDS})
        by_g = {g: [j for j, (gg, _, _) in enumerate(CONDS) if gg == g] for g in groups}
        for k in range(1, max_k + 1):
            for gs in itertools.combinations(groups, k):
                for js in itertools.product(*[by_g[g] for g in gs]):
                    m = CM[js[0]]
                    for j in js[1:]:
                        m &= CM[j]
                    ma, mb = m & PA, m & PB
                    na, nb = ma.bit_count(), mb.bit_count()
                    if na < minA or nb < minB:
                        continue
                    wa, wb = (ma & WIN).bit_count() / na, (mb & WIN).bit_count() / nb
                    res.append((js, na, nb, wa, wb))
        return res, bA, bB

    def spearman(xs, ys):
        def rk(v):
            o = sorted(range(len(v)), key=lambda k: v[k])
            r = [0] * len(v)
            for p, k in enumerate(o):
                r[k] = p
            return r
        rx, ry = rk(xs), rk(ys)
        n = len(xs)
        mx, my = sum(rx) / n, sum(ry) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        return cov / math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))

    def trio_stats(m, tgt):
        """每月從符合的股票，依外資30＋成長30＋beta40 挑 3 檔"""
        ks = set(bits(m))
        rs = []
        for i, idxs in idx_by_month.items():
            mem = [k for k in idxs if k in ks]
            if len(mem) < 3:
                continue
            W_ = {"qf": .3, "cagr3": .3, "beta": .4}
            rk = {w: pct_rank({k: S[k]["f"].get(w) for k in mem}) for w in W_}
            sc = {}
            for k in mem:
                parts = [(rk[w][k], wt) for w, wt in W_.items() if k in rk[w]]
                tw = sum(wt for _, wt in parts)
                if tw >= 0.5:
                    sc[k] = sum(v * wt for v, wt in parts) / tw
            pick = sorted(sc, key=lambda k: -sc[k])[:3]
            if len(pick) == 3:
                rs.append((i, sum(S[k][tgt] for k in pick) / 3))
        if not rs:
            return None
        win = [r > 1 for _, r in rs]
        return {"月數": len(rs), "年份數": len({dates[i][:4] for i, _ in rs}), "勝率": round(sum(win) / len(win) * 100, 1),
                "平均超額": round((sum(r for _, r in rs) / len(rs) - 1) * 100, 1), "最差": round((min(r for _, r in rs) - 1) * 100, 1)}

    def describe(js):
        return "＋".join(CONDS[j][1] for j in js)

    def months_of(m):
        return len({month_of[k] for k in bits(m)})

    out = {"說明": __doc__.strip(), "條件數": len(CONDS), "樣本數": len(S)}
    rng = random.Random(5)
    for tgt, lab in (("tp", "賺 20% 就轉 0050"), ("hold", "抱滿 12 個月")):
        WIN = mask(lambda s: s[tgt] > 1)
        res, bA, bB = search(WIN)
        u.log(f"[{lab}] 組合數 {len(res)}，基準勝率 A {bA:.3f} B {bB:.3f}")
        sec = {"基準單檔勝率": {"前段 A（2019/12~2022/06）": round(bA * 100, 1), "後段 B（2023/07~2025/11）": round(bB * 100, 1)},
               "符合樣本數門檻的組合數": len(res)}
        la = [wa - bA for _, _, _, wa, _ in res]
        lb = [wb - bB for _, _, _, _, wb in res]
        sec["前段勝率提升 vs 後段勝率提升 的等級相關"] = round(spearman(la, lb), 3)

        def row(r):
            js, na, nb, wa, wb = r
            m = CM[js[0]]
            for j in js[1:]:
                m &= CM[j]
            return {"條件": describe(js), "前段勝率": round(wa * 100, 1), "前段樣本": na, "前段月數": months_of(m & PA),
                    "後段勝率": round(wb * 100, 1), "後段樣本": nb, "後段月數": months_of(m & PB),
                    "前段提升": round((wa - bA) * 100, 1), "後段提升": round((wb - bB) * 100, 1)}

        topA = sorted(res, key=lambda r: -r[3])[:20]
        sec["用前段挑的前 20 名 → 後段表現"] = [row(r) for r in topA]
        sec["前段前 20 名在後段的平均提升"] = round(sum(r[4] - bB for r in topA) / len(topA) * 100, 1)
        topB = sorted(res, key=lambda r: -r[4])[:20]
        sec["用後段挑的前 20 名 → 前段表現"] = [row(r) for r in topB]
        sec["後段前 20 名在前段的平均提升"] = round(sum(r[3] - bA for r in topB) / len(topB) * 100, 1)
        # 兩段都至少提升 15 個百分點（依較差那段排序）
        rob = sorted([r for r in res if r[3] - bA >= 0.15 and r[4] - bB >= 0.15], key=lambda r: -min(r[3] - bA, r[4] - bB))
        sec["兩段都提升 ≥ 15 個百分點的組合數"] = len(rob)
        PG = mask(lambda s: s["per"] == "gap")
        bG = (PG & WIN).bit_count() / PG.bit_count()
        sec["基準單檔勝率"]["中間段 G（2022/07~2023/06，沒參與挑選）"] = round(bG * 100, 1)
        rows = []
        seen = set()
        theme = {}
        n_ok = 0
        for r in rob:
            m = CM[r[0][0]]
            for j in r[0][1:]:
                m &= CM[j]
            ma_, mb_ = months_of(m & PA), months_of(m & PB)
            if ma_ < 6 or mb_ < 6:                           # 每段至少 6 個月份
                continue
            ks = bits(m)
            stocks = {S[k]["c"] for k in ks}
            if len(stocks) < 15:                             # 不能只靠少數幾檔
                continue
            n_ok += 1
            for j in r[0]:
                theme[CONDS[j][1]] = theme.get(CONDS[j][1], 0) + 1
            key = frozenset(r[0])
            if any(frozenset(x) <= key for x in seen):       # 已經有更簡單的組合涵蓋，就跳過
                continue
            seen.add(key)
            rr = row(r)
            mg = m & PG
            ng = mg.bit_count()
            rr["中間段勝率"] = round((mg & WIN).bit_count() / ng * 100, 1) if ng >= 20 else None
            rr["中間段樣本"] = ng
            rr["中間段提升"] = round(((mg & WIN).bit_count() / ng - bG) * 100, 1) if ng >= 20 else None
            rr["不同股票數"] = len(stocks)
            rr["三檔組合（全期）"] = trio_stats(m, tgt)
            rows.append(rr)
            if len(rows) >= 30:
                break
        sec["兩段都提升 ≥ 15、每段 ≥ 6 個月、≥ 15 檔股票的組合數"] = n_ok
        sec["兩段都有效的組合（最精簡的前 30 個）"] = rows
        sec["這些組合裡最常出現的條件"] = dict(sorted(theme.items(), key=lambda x: -x[1])[:20])
        g_ok = [x for x in rows if x["中間段提升"] is not None]
        sec["在中間段也提升的比例"] = f"{sum(x['中間段提升'] > 0 for x in g_ok)}/{len(g_ok)}"
        # 打亂勝負（同一個月內）再搜尋：純運氣會冒出幾個「兩段都提升 ≥ 15」的組合
        fake = []
        for sd in range(5):
            lab_ = [S[k][tgt] > 1 for k in range(len(S))]
            for i, idxs in idx_by_month.items():
                v = [lab_[k] for k in idxs]
                rng.shuffle(v)
                for k, x in zip(idxs, v):
                    lab_[k] = x
            Wf = int("".join("1" if x else "0" for x in reversed(lab_)), 2)
            rf, fa, fb = search(Wf)
            fake.append(sum(1 for r in rf if r[3] - fa >= 0.15 and r[4] - fb >= 0.15))
            u.log(f"  打亂 {sd}: {fake[-1]}")
        sec["打亂勝負後，兩段都提升 ≥ 15 的組合數（5 次）"] = fake
        out[lab] = sec
        u.log(f"[{lab}] 相關 {sec['前段勝率提升 vs 後段勝率提升 的等級相關']}，穩健 {len(rob)}，打亂 {fake}")
        for x in rows[:12]:
            u.log("   " + json.dumps(x, ensure_ascii=False))
    (u.DATA / "research_multi.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
