"""
研究 2：以 0050 為基礎的策略能不能穩定贏 0050（含交易成本）
- 市值 = 發行股數（外資持股資料 MI_QFIIS / 櫃買 qfii，籌碼實驗寫入 data/hist/qf_*）× 股價
- 每 60 個交易日換股一次，12 個不同起點各模擬一個帳戶
- 交易成本：單邊換手 × 0.5%（手續費約 0.1% ×2 + 證交稅 0.3%）
輸出 data/research2.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402
import backtest as b    # noqa: E402
import research as R    # noqa: E402

COST = 0.005
W_BEST = R.COMPOSITES["C B＋低波動"]
SPLIT = "20230615"


def multibagger(usable):
    """翻倍股分析：一年後（250 個交易日）漲一倍的股票，事前長什麼樣子？高分股翻倍的機率有多高？"""
    px = b.load_hist("px")
    dates = sorted(px)
    di = {d: i for i, d in enumerate(dates)}
    rows = []
    for t, snap in usable:
        i = di.get(t)
        if i is None or i + 250 >= len(dates):
            continue
        for c, m in snap.items():
            path = [px[d].get(c) for d in dates[i:i + 251]]
            path = [x for x in path if x]
            if len(path) < 200 or len(u.after_split(path)) != len(path):
                continue
            f = path[-1][0] / m["p"] - 1
            rows.append((t, m.get("S"), f, m.get("rev_hi"), m.get("accel"), m.get("streak"), m.get("cap"), m.get("b60")))
    if not rows:
        return None
    n = len(rows)
    pct = lambda xs: round(sum(xs) / len(xs) * 100, 1) if xs else None
    out = {"樣本（股票×時間點）": n, "期間": [rows[0][0], rows[-1][0]],
           "一年翻倍機率（全部）": pct([r[2] >= 1 for r in rows]),
           "一年漲 50% 以上機率（全部）": pct([r[2] >= 0.5 for r in rows]),
           "一年跌 30% 以上機率（全部）": pct([r[2] <= -0.3 for r in rows])}
    # 依分數分 5 組
    sc = sorted([r for r in rows if r[1] is not None], key=lambda r: r[1])
    q = len(sc) // 5
    groups = {}
    for k in range(5):
        g = sc[k * q:(k + 1) * q] if k < 4 else sc[4 * q:]
        fs = sorted(r[2] for r in g)
        groups[f"分數第 {k + 1} 組（{'最低' if k == 0 else '最高' if k == 4 else ''}）"] = {
            "翻倍機率": pct([x >= 1 for x in fs]), "漲50%+": pct([x >= 0.5 for x in fs]), "跌30%+": pct([x <= -0.3 for x in fs]),
            "中位數報酬": round(fs[len(fs) // 2] * 100, 1), "平均報酬": round(sum(fs) / len(fs) * 100, 1)}
    out["依分數分組"] = groups
    # 翻倍股事前特徵 vs 全部
    dbl = [r for r in rows if r[2] >= 1]
    feat = lambda rs, f: pct([f(r) for r in rs])
    chars = {"營收創 12 月新高": lambda r: r[3] is not None and r[3] >= 0, "營收動能 > 0": lambda r: r[4] is not None and r[4] > 0,
             "營收連續年增 ≥ 6 個月": lambda r: (r[5] or 0) >= 6, "分數 ≥ 70": lambda r: (r[1] or 0) >= 70,
             "非市值前 100 大（中小型）": lambda r: r[6] is None or True, "股價在季線上": lambda r: (r[7] or 0) > 0}
    caps = {}
    for t, *_ in rows:
        pass
    out["翻倍股事前特徵（翻倍股 vs 全部）"] = {k: [feat(dbl, f), feat(rows, f)] for k, f in chars.items() if not k.startswith("非市值")}
    out["翻倍股樣本數"] = len(dbl)
    # 每期分數前 20 名裡，至少有一檔一年後翻倍的比例；以及平均有幾檔
    per = {}
    for r in rows:
        per.setdefault(r[0], []).append(r)
    hits, cnts, rets = [], [], []
    for t, rs in per.items():
        top = sorted([r for r in rs if r[1] is not None], key=lambda r: -r[1])[:20]
        k = sum(1 for r in top if r[2] >= 1)
        hits.append(k > 0)
        cnts.append(k)
        rets.append(sum(r[2] for r in top) / len(top))
    out["分數前 20 名（等權）"] = {"至少一檔翻倍的期數比例": pct(hits), "平均每期有幾檔翻倍": round(sum(cnts) / len(cnts), 2),
                             "一年後組合報酬中位數": round(sorted(rets)[len(rets) // 2] * 100, 1)}
    return out


def main():
    records = b.build_records()
    qfh = b.load_hist("qf")
    last_sh = {}
    usable = []
    for t, snap in records:
        for c, v in (qfh.get(t) or {}).items():
            if v and len(v) > 1 and v[1]:
                last_sh[c] = v[1]
        cap = {c: last_sh[c] * m["p"] for c, m in snap.items() if c in last_sh and m.get("p")}
        if len(cap) < 80:
            continue
        sc = R.comp_scores(snap, W_BEST)
        for c, m in snap.items():
            m["cap"] = cap.get(c)
            m["S"] = sc.get(c)
        usable.append((t, snap))
    u.log(f"可用時間點 {len(usable)}（{usable[0][0]}~{usable[-1][0]}）")

    def top_cap(snap, n):
        return sorted([c for c, m in snap.items() if m.get("cap")], key=lambda c: -snap[c]["cap"])[:n]

    def norm(w):
        s = sum(w.values())
        return {c: v / s for c, v in w.items() if v > 0} if s > 0 else {}

    S = lambda m: m["S"] if m.get("S") is not None else 50.0

    def cap50(snap, prev):
        return norm({c: snap[c]["cap"] for c in top_cap(snap, 50)})

    def drop20(snap, prev):
        cs = top_cap(snap, 50)
        cut = sorted(cs, key=lambda c: S(snap[c]))[:10]
        return norm({c: snap[c]["cap"] for c in cs if c not in cut})

    def tilt(snap, prev):
        return norm({c: snap[c]["cap"] * math.exp((S(snap[c]) - 50) / 50) for c in top_cap(snap, 50)})

    def tsmc40(snap, prev):
        others = [c for c in top_cap(snap, 100) if c != "2330"]
        pick = sorted(others, key=lambda c: -S(snap[c]))[:10]
        w = {c: 0.6 / len(pick) for c in pick}
        if "2330" in snap:
            w["2330"] = 0.4
        return norm(w)

    def top10(snap, prev):
        pick = sorted(top_cap(snap, 100), key=lambda c: -S(snap[c]))[:10]
        return norm({c: 1 for c in pick})

    def top10_buffer(snap, prev):
        uni = top_cap(snap, 100)
        rank = sorted(uni, key=lambda c: -S(snap[c]))
        keep = [c for c in (prev or {}) if c in rank[:20]]
        pick = keep + [c for c in rank if c not in keep][:10 - len(keep)]
        return norm({c: 1 for c in pick[:10]})

    def best5(snap, prev):
        lr = u.pct_rank({c: m.get("avgval") for c, m in snap.items()})
        c = sorted([c for c, m in snap.items() if lr.get(c, 0) >= 0.5 and not u.is_over(m) and m.get("S") is not None],
                   key=lambda c: -snap[c]["S"])[:5]
        return norm({x: 1 for x in c})

    STRATS = {"市值前 50 大（0050 的近似，用來檢查模擬是否準確）": cap50,
              "權值股剔除分數最差 20%": drop20, "權值股依分數加減碼": tilt,
              "台積電 40% ＋ 前 100 大裡分數前 10 名": tsmc40, "前 100 大裡分數前 10 名（等權）": top10,
              "前 100 大分數前 10 名（跌出前 20 才換，降低換手）": top10_buffer,
              "上一輪最佳：流動性前 50% 分數前 5 名": best5}

    out = {"period": [usable[0][0], usable[-1][0]], "cost_one_way": COST, "strategies": {}}
    yrs_total = len(usable) * 5 / 250
    for name, fn in STRATS.items():
        runs = []
        for off in range(12):
            eq = em = 1.0
            peak = 1.0
            dd = 0.0
            prev = None
            seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
            beats = []
            years = {}
            turn = []
            for i in range(off, len(usable), 12):
                t, snap = usable[i]
                mk = b.MKT.get(t, {})
                if mk.get("f60") is None:
                    continue
                w = fn(snap, prev)
                if not w:
                    continue
                # 換手：上一期持股漂移後的權重 vs 新權重
                if prev:
                    to = sum(abs(w.get(c, 0) - prev.get(c, 0)) for c in set(w) | set(prev)) / 2
                else:
                    to = 1.0
                turn.append(to)
                r = sum(wt * (snap[c].get("fwd_long") or 0) for c, wt in w.items()) - to * COST
                eq *= 1 + r
                em *= 1 + mk["f60"]
                peak = max(peak, eq)
                dd = min(dd, eq / peak - 1)
                k = "train" if t < SPLIT else "test"
                seg[k][0] *= 1 + r
                seg[k][1] *= 1 + mk["f60"]
                seg[k][2] += 1
                beats.append(r > mk["f60"])
                years.setdefault(t[:4], [1.0, 1.0])
                years[t[:4]][0] *= 1 + r
                years[t[:4]][1] *= 1 + mk["f60"]
                # 期末漂移後權重
                tot = sum(wt * (1 + (snap[c].get("fwd_long") or 0)) for c, wt in w.items())
                prev = {c: wt * (1 + (snap[c].get("fwd_long") or 0)) / tot for c, wt in w.items()}
            runs.append({"eq": eq, "em": em, "dd": dd, "seg": seg, "beats": beats, "years": years, "turn": turn})
        cagr = lambda f, y: (f ** (1 / y) - 1) * 100 if y > 0 else None
        yrs_seg = lambda k: sum(r["seg"][k][2] for r in runs) / len(runs) * 60 / 250
        allyears = sorted({y for r in runs for y in r["years"]})
        ybeat = {y: round(sum(1 for r in runs if y in r["years"] and r["years"][y][0] > r["years"][y][1]) /
                          max(1, sum(1 for r in runs if y in r["years"])) * 100) for y in allyears}
        out["strategies"][name] = {
            "年化（12 起點平均）": round(sum(cagr(r["eq"], yrs_total) for r in runs) / len(runs), 1),
            "0050 年化": round(sum(cagr(r["em"], yrs_total) for r in runs) / len(runs), 1),
            "最差起點年化": round(min(cagr(r["eq"], yrs_total) for r in runs), 1),
            "2018-2023 年化": round(sum(cagr(r["seg"]["train"][0], yrs_seg("train")) for r in runs) / len(runs), 1),
            "2018-2023 0050": round(sum(cagr(r["seg"]["train"][1], yrs_seg("train")) for r in runs) / len(runs), 1),
            "2023-2026 年化": round(sum(cagr(r["seg"]["test"][0], yrs_seg("test")) for r in runs) / len(runs), 1),
            "2023-2026 0050": round(sum(cagr(r["seg"]["test"][1], yrs_seg("test")) for r in runs) / len(runs), 1),
            "每季贏 0050 比例": round(sum(sum(r["beats"]) for r in runs) / sum(len(r["beats"]) for r in runs) * 100, 1),
            "各年贏 0050 的起點比例%": ybeat,
            "最大回撤（平均/最差）": [round(sum(r["dd"] for r in runs) / len(runs) * 100, 1), round(min(r["dd"] for r in runs) * 100, 1)],
            "平均每季換手": round(sum(sum(r["turn"][1:]) for r in runs) / max(1, sum(len(r["turn"]) - 1 for r in runs)) * 100, 1)}
        u.log(name + " " + json.dumps(out["strategies"][name], ensure_ascii=False))
    out["multibagger"] = multibagger(usable)
    u.save("research2.json", out)


if __name__ == "__main__":
    main()
