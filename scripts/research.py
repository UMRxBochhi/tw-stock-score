"""
研究：用 2018~2026 完整資料（含 2018、2022 空頭）重新設計綜合分數與選股規則
- 訓練：2018/07 ~ 2023/06；驗證：2023/06 之後（和 backtest.py 相同切分）
- 比較多種分數組合、選股規則、空頭減碼（0050 在半年線下時降低持股）
由 .github/workflows/research.yml 執行，輸出 data/research.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402
import backtest as b    # noqa: E402

OUT = {}


def comp_scores(snap, W, over_pen=15):
    """W: {特徵: 權重}；特徵前加 - 代表越小越好"""
    rk = {}
    for k in W:
        key = k.lstrip("-")
        sign = -1 if k.startswith("-") else 1
        rk[k] = u.pct_rank({c: (m.get(key) * sign if m.get(key) is not None else None) for c, m in snap.items()})
    out = {}
    for c, m in snap.items():
        parts = [(rk[k][c], w) for k, w in W.items() if c in rk[k]]
        tw = sum(w for _, w in parts)
        if tw < 0.55 * sum(W.values()):
            continue
        s = sum(v * w for v, w in parts) / tw * 100
        out[c] = max(0.0, s - over_pen) if u.is_over(m) else s
    return out


COMPOSITES = {
    "目前（營收40 流動性20 PEG20 外資持股20）": None,
    "A 營收創高50 動能30 連增20": {"rev_hi": .5, "accel": .3, "streak": .2},
    "B 營收創高45 動能30 PEG15 連增10": {"rev_hi": .45, "accel": .3, "peg": .15, "streak": .1},
    "C B＋低波動": {"rev_hi": .4, "accel": .25, "peg": .1, "streak": .1, "lowvol": .15},
    "D B＋流動性": {"rev_hi": .4, "accel": .25, "peg": .1, "streak": .1, "lv": .15},
    "E B＋不追高（季乖離越低越好）": {"rev_hi": .4, "accel": .25, "peg": .1, "streak": .1, "-b60": .15},
    "F 營收創高60 動能40": {"rev_hi": .6, "accel": .4},
}


def nw(xs, lag):
    t = b.nw_t(xs, lag)
    return round(t, 2) if t is not None else None


def main():
    records = b.build_records()
    nt = next(i for i, (t, _) in enumerate(records) if t > "20230615")
    OUT["split"] = [records[0][0], records[nt][0], records[-1][0]]
    # 預先算各組合分數
    for name, W in COMPOSITES.items():
        for t, snap in records:
            sc = ({c: u.strat_score(m["sc"], m, u.SCORE_W) for c, m in snap.items()} if W is None else comp_scores(snap, W))
            for c, v in sc.items():
                if v is not None:
                    snap[c].setdefault("CS", {})[name] = v

    # ---- 1. 分數組合：IC（訓練/驗證、多頭/空頭）與 80 分以上勝率
    res = {}
    for name in COMPOSITES:
        r = {}
        for h, hz in (("short", 20), ("long", 60)):
            def ics(part):
                v = [b.spearman({c: m.get("CS", {}).get(name) for c, m in s.items()}, {c: m.get("ex_" + h) for c, m in s.items()}) for _, s in part]
                return [x for x in v if x is not None]
            tr, te = ics(records[:nt]), ics(records[nt:])
            bull = ics([x for x in records if b.MKT.get(x[0], {}).get("up120") is True])
            bear = ics([x for x in records if b.MKT.get(x[0], {}).get("up120") is False])
            hi = lambda part: [m["ex_" + h] for _, s in part for m in s.values() if m.get("CS", {}).get(name, 0) >= 80 and "ex_" + h in m]
            w80 = lambda xs: round(sum(x > 0 for x in xs) / len(xs) * 100, 1) if xs else None
            r[hz] = {"ic_train": round(sum(tr) / len(tr) * 100, 2), "t_train": nw(tr, hz // 5),
                     "ic_test": round(sum(te) / len(te) * 100, 2), "t_test": nw(te, hz // 5),
                     "t_bull": nw(bull, hz // 5), "t_bear": nw(bear, hz // 5),
                     "win80_train": w80(hi(records[:nt])), "win80_test": w80(hi(records[nt:]))}
        res[name] = r
    OUT["composites"] = res
    best = max((n for n in COMPOSITES if COMPOSITES[n]), key=lambda n: (res[n][60]["t_train"] or 0) + (res[n][20]["t_train"] or 0))
    OUT["best_composite_by_train"] = best
    u.log("最佳組合（只看訓練期）：" + best)

    # ---- 2. 長線選股規則（60 日、5 檔）
    def liq_rank(snap):
        return u.pct_rank({c: m.get("avgval") for c, m in snap.items()})

    def rule_eval(select, hz=60, n=5, part=None):
        h = "long" if hz == 60 else "short"
        rows = []
        for t, snap in part:
            picks = select(t, snap)[:n]
            ms = [snap[c] for c in picks if "fwd_" + h in snap[c]]
            if not ms:
                continue
            mk = b.MKT.get(t, {})
            rows.append((t, sum(m["fwd_" + h] for m in ms) / len(ms), sum(m["ex_" + h] for m in ms) / len(ms), mk.get(f"f{hz}")))
        if not rows:
            return None
        k = len(rows)
        mk = [r for r in rows if r[3] is not None]
        yrs = {}
        for t, r, e, m in mk:
            yrs.setdefault(t[:4], []).append(r - m)
        return {"n": k, "pos": round(sum(r[1] > 0 for r in rows) / k * 100, 1), "beat_med": round(sum(r[2] > 0 for r in rows) / k * 100, 1),
                "beat_0050": round(sum(r[1] > r[3] for r in mk) / len(mk) * 100, 1), "avg": round(sum(r[1] for r in rows) / k * 100, 2),
                "avg_0050": round(sum(r[3] for r in mk) / len(mk) * 100, 2), "worst": round(min(r[1] for r in rows) * 100, 2),
                "years_beat_0050": f"{sum(1 for v in yrs.values() if sum(v) > 0)}/{len(yrs)}"}

    def by(key):
        return lambda m: -(m.get(key) if m.get(key) is not None else -1e9)

    def cs(name):
        return lambda m: -(m.get("CS", {}).get(name, -1))

    def mk_rule(cond, sort, liq_top=None, abs_min=None):
        def sel(t, snap):
            lr = liq_rank(snap) if liq_top else {}
            c = [(code, m) for code, m in snap.items() if not u.is_over(m) and cond(m)
                 and (liq_top is None or lr.get(code, 0) >= 1 - liq_top) and (abs_min is None or (m.get("avgval") or 0) >= abs_min)]
            c.sort(key=lambda x: sort(x[1]))
            return [code for code, _ in c]
        return sel

    streak12 = lambda m: (m.get("streak") or 0) >= 12
    RULES = {
        "目前：連增≥12＋均量≥10億，外資20日排序": mk_rule(streak12, by("f20"), abs_min=1e9),
        "連增≥12＋流動性前30%，外資20日排序": mk_rule(streak12, by("f20"), liq_top=0.3),
        "流動性前30%，最佳分數排序": mk_rule(lambda m: True, cs(best), liq_top=0.3),
        "流動性前30%＋營收創新高，最佳分數排序": mk_rule(lambda m: (m.get("rev_hi") if m.get("rev_hi") is not None else -1) >= 0, cs(best), liq_top=0.3),
        "流動性前30%＋營收創新高＋動能>0，最佳分數排序": mk_rule(lambda m: (m.get("rev_hi") if m.get("rev_hi") is not None else -1) >= 0 and (m.get("accel") or -1) > 0, cs(best), liq_top=0.3),
        "流動性前50%，最佳分數排序": mk_rule(lambda m: True, cs(best), liq_top=0.5),
        "連增≥12＋流動性前30%，最佳分數排序": mk_rule(streak12, cs(best), liq_top=0.3),
    }
    lr = {}
    for name, sel in RULES.items():
        lr[name] = {"train": rule_eval(sel, 60, 5, records[:nt]), "test": rule_eval(sel, 60, 5, records[nt:]),
                    "all": rule_eval(sel, 60, 5, records)}
    OUT["long_rules"] = lr
    # 短線（20 日、3 檔）：回檔條件
    pull = lambda m: m.get("b20") is not None and m["b20"] < 0
    SR = {
        "目前短線：連增≥12＋動能>0＋均量≥10億＋月線下，外資持股排序": mk_rule(lambda m: streak12(m) and (m.get("accel") or -1) > 0 and pull(m), by("qf"), abs_min=1e9),
        "流動性前30%＋月線下，最佳分數排序": mk_rule(pull, cs(best), liq_top=0.3),
        "流動性前30%＋營收創新高＋月線下，最佳分數排序": mk_rule(lambda m: pull(m) and (m.get("rev_hi") if m.get("rev_hi") is not None else -1) >= 0, cs(best), liq_top=0.3),
        "流動性前30%（不限回檔），最佳分數排序": mk_rule(lambda m: True, cs(best), liq_top=0.3),
    }
    OUT["short_rules"] = {name: {"train": rule_eval(sel, 20, 3, records[:nt]), "test": rule_eval(sel, 20, 3, records[nt:]),
                                 "all": rule_eval(sel, 20, 3, records)} for name, sel in SR.items()}

    # ---- 3. 空頭減碼：同一個帳戶，每 60 個交易日換股，0050 在半年線下時只投入 X%（其餘現金）
    best_rule = max(lr, key=lambda n: (lr[n]["train"] or {}).get("beat_0050", 0))
    OUT["best_long_rule_by_train"] = best_rule
    sel = RULES[best_rule]
    sims = {}
    for expo_bear in (1.0, 0.5, 0.0):
        finals, mdds, finals_mkt, mdd_mkt = [], [], [], []
        for off in range(12):
            eq = em = 1.0
            peak = peakm = 1.0
            dd = ddm = 0.0
            for i in range(off, len(records), 12):
                t, snap = records[i]
                mk = b.MKT.get(t, {})
                if mk.get("f60") is None:
                    continue
                picks = [snap[c] for c in sel(t, snap)[:5] if "fwd_long" in snap[c]]
                r = sum(m["fwd_long"] for m in picks) / len(picks) if picks else 0.0
                x = expo_bear if mk.get("up120") is False else 1.0
                eq *= 1 + r * x
                em *= 1 + mk["f60"]
                peak, peakm = max(peak, eq), max(peakm, em)
                dd, ddm = min(dd, eq / peak - 1), min(ddm, em / peakm - 1)
            finals.append(eq)
            mdds.append(dd)
            finals_mkt.append(em)
            mdd_mkt.append(ddm)
        yrs = (len(records) * 5) / 250
        cagr = lambda f: round((f ** (1 / yrs) - 1) * 100, 1)
        sims[f"空頭時投入 {int(expo_bear * 100)}%"] = {
            "年化報酬（12 個起點平均）": cagr(sum(finals) / len(finals)), "最差起點年化": cagr(min(finals)),
            "平均最大回撤": round(sum(mdds) / len(mdds) * 100, 1), "最大回撤（最差）": round(min(mdds) * 100, 1),
            "0050 年化": cagr(sum(finals_mkt) / len(finals_mkt)), "0050 最大回撤": round(min(mdd_mkt) * 100, 1)}
    OUT["bear_exposure"] = sims
    u.save("research.json", OUT)
    u.log(json.dumps(OUT, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
