# 研究 3：使用者架構與修正版技術面／籌碼面
"""
研究 3：使用者的選股架構（A 階段：用現有資料）
- 基本面 65%：營收預估修正（未來 12 個月營收推估的上修）、成長性（營收創新高、動能、連續年增）
  ※ 獲利能力、EPS 預估修正要等季報歷史補齊（B 階段）
- 技術面 20%：趨勢（站上季線但沒追高）、接近支撐
- 籌碼面 15%：外資持股比例 4 週變化、負乖離（不追高）
  ※ 集保大戶持股沒有歷史，暫不納入
- 每月營收公布後（11 日後第一個評估日）換股：買前 N 名、等金額，持有到下次換股
- 交易成本：單邊換手 × 0.5%；比較同期 0050
輸出 data/research3.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402
import backtest as b    # noqa: E402
import research as R    # noqa: E402

COST = 0.005
SPLIT = "20230615"
MIN_CAP = 1e10     # 市值至少 100 億（使用者條件）；沒有市值資料的時間點不套用


def shift(k, n):
    y, m = int(k[:-2]), int(k[-2:]) - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


def est12(h, k):
    """月份 k 時推估的未來 12 個月營收 = 近 12 個月營收 ×（1 + 近 3 個月年增率）"""
    cur = [h.get(shift(k, i), [None])[0] for i in range(12)]
    r3 = [h.get(shift(k, i), [None])[0] for i in range(3)]
    p3 = [h.get(shift(k, i + 12), [None])[0] for i in range(3)]
    if None in cur or None in r3 or None in p3 or not sum(p3):
        return None
    g = max(-0.5, min(1.5, sum(r3) / sum(p3) - 1))
    return sum(cur) * (1 + g)


def persistence(records, reb, ind_of, e, di):
    """上修持續性：連續上修 N 個月後，下個月繼續上修的機率、之後的報酬（相對 0050）；
    並區分「整個族群一起上修」vs「只有自己」、「低基期造成的假成長」"""
    nxt_up, ret = {}, {}
    seq = [records[i] for i in reb]
    for n in range(len(seq) - 1):
        t, snap = seq[n]
        t2, snap2 = seq[n + 1]
        mk = snap.get("__mkt")
        # 族群上修比例
        g_up = {}
        for c, m in snap.items():
            if c.startswith("__") or m.get("rev_rev1") is None:
                continue
            g = ind_of(c)
            if g:
                g_up.setdefault(g, []).append(m["rev_rev1"] > 0)
        g_ratio = {g: sum(v) / len(v) for g, v in g_up.items() if len(v) >= 3}
        for c, m in snap.items():
            if c.startswith("__") or m.get("rev_up_n") is None:
                continue
            k = m["rev_up_n"]
            bucket = "0（本月下修）" if k == 0 else "1 個月" if k == 1 else "2~3 個月" if k <= 3 else "4~6 個月" if k <= 6 else "7~11 個月" if k <= 11 else "12 個月以上"
            m2 = snap2.get(c)
            if m2 and m2.get("rev_rev1") is not None:
                nxt_up.setdefault(bucket, []).append(m2["rev_rev1"] > 0)
            if m.get("hold") is not None and mk:
                wide = g_ratio.get(ind_of(c), 0) >= 0.6
                ret.setdefault(bucket, []).append(m["hold"] - mk["hold"])
                if k >= 2:
                    ret.setdefault("連續上修≥2：族群多數也上修", []).append(m["hold"] - mk["hold"]) if wide else                         ret.setdefault("連續上修≥2：只有自己上修", []).append(m["hold"] - mk["hold"])
                    big1 = (m.get("rev_rev1") or 0) > 0.15 and (m.get("rev_rev3") or 0) < (m.get("rev_rev1") or 0) * 1.2
                    (ret.setdefault("單月暴衝型（一次性嫌疑）", []) if big1 else ret.setdefault("穩定累積型", [])).append(m["hold"] - mk["hold"])
    pct = lambda xs: round(sum(xs) / len(xs) * 100, 1) if xs else None
    order = ["0（本月下修）", "1 個月", "2~3 個月", "4~6 個月", "7~11 個月", "12 個月以上"]
    res = {"下個月繼續上修的機率": {k: {"機率": pct(nxt_up[k]), "樣本": len(nxt_up[k])} for k in order if k in nxt_up},
           "下個月相對 0050 的超額報酬": {k: {"平均": round(sum(v) / len(v) * 100, 2), "贏 0050 機率": pct([x > 0 for x in v]), "樣本": len(v)}
                                   for k, v in ret.items()}}
    # 上修一段會持續多久：連續上修的長度分布（每段從開始到中斷）
    runs = []
    cur = {}
    for t, snap in seq:
        for c, m in snap.items():
            if c.startswith("__") or m.get("rev_rev1") is None:
                continue
            if m["rev_rev1"] > 0:
                cur[c] = cur.get(c, 0) + 1
            elif cur.get(c):
                runs.append(cur.pop(c))
    if runs:
        runs.sort()
        res["一段上修持續多久（月）"] = {"中位數": runs[len(runs) // 2], "平均": round(sum(runs) / len(runs), 1),
                                 "只有 1 個月的比例": pct([r == 1 for r in runs]), "3 個月以上": pct([r >= 3 for r in runs]),
                                 "6 個月以上": pct([r >= 6 for r in runs]), "12 個月以上": pct([r >= 12 for r in runs]), "段數": len(runs)}
    return res


def main():
    records = b.build_records()
    revh = json.loads((b.HIST / "rev.json").read_text(encoding="utf-8")) if (b.HIST / "rev.json").exists() else {}
    for code, h in u.load("fundamentals.json", {}).get("rev_hist", {}).items():
        revh.setdefault(code, {}).update(h)
    px = b.load_hist("px")
    dates = sorted(px)
    di = {d: i for i, d in enumerate(dates)}
    e = []
    last = None
    for d in dates:
        v = (px[d].get("0050") or [None])[0]
        last = v if v else last
        e.append(last)
    for i in range(1, len(e)):
        if e[i] and e[i - 1] and e[i] / e[i - 1] < 0.5:
            k = e[i - 1] / e[i]
            for j in range(i):
                e[j] = e[j] / k if e[j] else e[j]

    # 每月換股日：11 日之後第一個評估日
    reb, seen = [], set()
    for idx, (t, snap) in enumerate(records):
        if int(t[6:]) >= 11 and t[:6] not in seen:
            seen.add(t[:6])
            reb.append(idx)
    u.log(f"換股次數 {len(reb)}（{records[reb[0]][0]}~{records[reb[-1]][0]}）")

    # 市值 = 發行股數（外資持股資料）× 股價；使用者條件：市值 ≥ 100 億
    qfh = b.load_hist("qf")
    mgh = b.load_hist("mg")
    mg_dates = sorted(mgh)
    last_sh = {}
    cap_ok_dates = set()
    for t, snap in records:
        for c, v in (qfh.get(t) or {}).items():
            if v and len(v) > 1 and v[1]:
                last_sh[c] = v[1]
        n_cap = 0
        for c, m in snap.items():
            if c in last_sh and m.get("p"):
                m["cap"] = last_sh[c] * m["p"]
                n_cap += 1
        if n_cap >= 200:
            cap_ok_dates.add(t)
    u.log(f"有市值資料的時間點 {len(cap_ok_dates)} / {len(records)}")

    # 特徵
    for n, idx in enumerate(reb):
        t, snap = records[idx]
        rk = b.avail_rev_key(t)
        prev_qf = records[idx - 4][1] if idx >= 4 else {}
        md = [d for d in mg_dates if d <= t]
        mg = mgh[md[-1]] if md and (int(t) - int(md[-1])) < 100 else {}
        bull = b.MKT.get(t, {}).get("up120")
        for c, m in snap.items():
            h = revh.get(c, {})
            ests = [est12(h, shift(rk, j)) for j in range(13)]
            if ests[0] and ests[1]:
                m["rev_rev1"] = ests[0] / ests[1] - 1
            if ests[0] and ests[3]:
                m["rev_rev3"] = ests[0] / ests[3] - 1
            up = 0
            for j in range(12):
                if ests[j] and ests[j + 1] and ests[j] > ests[j + 1]:
                    up += 1
                else:
                    break
            m["rev_up_n"] = up if ests[0] else None
            # 技術面：站上季線但不追高（0~15% 最好），接近支撐
            b6 = m.get("b60")
            if b6 is not None:
                m["trend_ok"] = 100 if 0 <= b6 <= 15 else 60 if (-10 <= b6 < 0 or 15 < b6 <= 30) else 20
            # 籌碼面：外資持股 4 週變化、負乖離
            # 修正版技術面／籌碼面：多頭才看支撐、融資使用率
            if bull is True and m.get("sup_score") is not None:
                m["sup_bull"] = m["sup_score"]
            x = mg.get(c)
            if x and x[0] is not None and x[1]:
                m["fin_use"] = x[0] / x[1]
            q0 = (prev_qf.get(c) or {}).get("qf")
            if m.get("qf") is not None and q0 is not None:
                m["qd4"] = m["qf"] - q0
        # 持有報酬：到下一次換股
        nxt = reb[n + 1] if n + 1 < len(reb) else None
        i0 = di[t]
        i1 = di[records[nxt][0]] if nxt is not None else None
        for c, m in snap.items():
            if i1 is None:
                continue
            path = [px[d].get(c) for d in dates[i0:i1 + 1]]
            path = [x for x in path if x]
            if len(path) >= (i1 - i0) * 0.8 and len(u.after_split(path)) == len(path) and px[dates[i1]].get(c):
                m["hold"] = px[dates[i1]][c][0] / m["p"] - 1
        snap["__mkt"] = {"hold": e[i1] / e[i0] - 1} if i1 is not None and e[i0] and e[i1] else None

    def user_score(snap, W_f, W_t, W_c, liq_top=None):
        items = {c: m for c, m in snap.items() if not c.startswith("__")}
        if MIN_CAP and any(m.get("cap") for m in items.values()):
            items = {c: m for c, m in items.items() if (m.get("cap") or 0) >= MIN_CAP}
        if liq_top:
            lr = u.pct_rank({c: m.get("avgval") for c, m in items.items()})
            items = {c: m for c, m in items.items() if lr.get(c, 0) >= 1 - liq_top}
        def sub(W):
            rk = {}
            for k in W:
                key, sign = k.lstrip("-"), (-1 if k.startswith("-") else 1)
                if key in ("trend_ok", "sup_score", "sup_bull"):
                    rk[k] = {c: (m[key] / 100 if m.get(key) is not None else None) for c, m in items.items()}
                else:
                    rk[k] = u.pct_rank({c: (m.get(key) * sign if m.get(key) is not None else None) for c, m in items.items()})
            out = {}
            for c in items:
                parts = [(rk[k].get(c), w) for k, w in W.items() if rk[k].get(c) is not None]
                tw = sum(w for _, w in parts)
                if tw >= 0.5 * sum(W.values()):
                    out[c] = sum(v * w for v, w in parts) / tw * 100
            return out
        F, T, C = sub(W_f), sub(W_t) if W_t else {}, sub(W_c) if W_c else {}
        wf, wt, wc = 0.65, (0.20 if W_t else 0), (0.15 if W_c else 0)
        out = {}
        for c in F:
            parts = [(F[c], wf)] + ([(T[c], wt)] if c in T else []) + ([(C[c], wc)] if c in C else [])
            tw = sum(w for _, w in parts)
            s = sum(v * w for v, w in parts) / tw
            out[c] = max(0.0, s - 15) if u.is_over(items[c]) else s
        return out

    FUND = {"rev_up_n": .30, "rev_rev3": .20, "rev_hi": .20, "accel": .15, "streak": .15}
    rev_now = u.load("fundamentals.json", {}).get("revenue", {})
    IND = {}

    def ind_of(c):
        if c not in IND:
            IND[c] = u.industry(c, rev_now)
        return IND[c]

    def funnel(snap, top_ind=3, need_up=True, max_pos=69):
        """族群漏斗：族群分數前 N 名 → 族群內依個股分數排序 → 營收預估有上修 → 本益比位階不貴（≤ +0.5σ ≈ 69 百分位）"""
        items = {c: m for c, m in snap.items() if not c.startswith("__")}
        F = user_score(snap, FUND, None, None)
        groups = {}
        for c, v in F.items():
            g = ind_of(c)
            if g:
                groups.setdefault(g, []).append(v)
        gs = {g: sorted(v)[len(v) // 2] for g, v in groups.items() if len(v) >= 3}
        top = set(sorted(gs, key=lambda g: -gs[g])[:top_ind])
        full = user_score(snap, FUND, TECH, CHIP)
        out = {}
        for c, v in full.items():
            m = items[c]
            if ind_of(c) not in top:
                continue
            if need_up and not (m.get("rev_up_n") or 0) >= 1:
                continue
            pos = -m["val_cheap"] if m.get("val_cheap") is not None else None
            if max_pos is not None and pos is not None and pos > max_pos:
                continue
            out[c] = v
        return out
    TECH = {"trend_ok": .5, "sup_score": .5}
    CHIP = {"qd4": .5, "-b20": .5}
    TECH2 = {"-b20": .5, "sup_bull": .5}
    TECH3 = {"-b20": .4, "-vr": .2, "sup_bull": .4}
    CHIP2 = {"qf": .5, "-fin_use": .5}
    VARIANTS = {
        "修正版（技術＝負乖離＋多頭支撐，籌碼＝外資持股高＋融資使用率低）": lambda s: user_score(s, FUND, TECH2, CHIP2),
        "修正版＋避開爆量": lambda s: user_score(s, FUND, TECH3, CHIP2),
        "修正版，只加技術面": lambda s: user_score(s, FUND, TECH2, None),
        "修正版，只加籌碼面": lambda s: user_score(s, FUND, None, CHIP2),
        "使用者架構（基本面65 技術20 籌碼15）": lambda s: user_score(s, FUND, TECH, CHIP),
        "使用者架構，限流動性前 50%": lambda s: user_score(s, FUND, TECH, CHIP, 0.5),
        "只看基本面（營收修正＋成長）": lambda s: user_score(s, FUND, None, None),
        "族群漏斗：前 3 族群→領先股→營收上修→本益比不貴": lambda s: funnel(s),
        "族群漏斗：前 5 族群": lambda s: funnel(s, top_ind=5),
        "族群漏斗：前 3 族群，不看本益比": lambda s: funnel(s, max_pos=None),
                "資料導向分數 C（研究 1 最佳）": lambda s: R.comp_scores({c: m for c, m in s.items() if not c.startswith("__") and (not m.get("cap") or m["cap"] >= MIN_CAP)}, R.COMPOSITES["C B＋低波動"]),
    }

    # 先看各新指標的預測力（到下次換股的報酬）
    ic = {}
    for k in ("rev_up_n", "rev_rev1", "rev_rev3", "trend_ok", "qd4", "b20", "vr", "sup_score", "sup_bull", "qf", "fin_use"):
        xs = []
        for idx in reb:
            t, snap = records[idx]
            items = {c: m for c, m in snap.items() if not c.startswith("__")}
            v = b.spearman({c: m.get(k) for c, m in items.items()}, {c: m.get("hold") for c, m in items.items()})
            if v is not None:
                xs.append(v)
        if xs:
            tt = b.nw_t(xs, 1)
            ic[k] = {"ic": round(sum(xs) / len(xs) * 100, 2), "t": tt and round(tt, 2), "n": len(xs)}
    out = {"ic_monthly": ic, "portfolios": {}}

    for vname, fn in VARIANTS.items():
        for N in (3, 5, 10):
            eq = em = 1.0
            peak = 1.0
            dd = 0.0
            prev = {}
            rows = []
            seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
            years = {}
            for idx in reb:
                t, snap = records[idx]
                mk = snap.get("__mkt")
                if not mk:
                    continue
                sc = fn(snap)
                pick = [c for c in sorted(sc, key=lambda c: -sc[c]) if snap[c].get("hold") is not None][:N]
                if not pick:
                    continue
                w = {c: 1 / len(pick) for c in pick}
                to = sum(abs(w.get(c, 0) - prev.get(c, 0)) for c in set(w) | set(prev)) / 2 if prev else 1.0
                r = sum(w[c] * snap[c]["hold"] for c in pick) - to * COST
                eq *= 1 + r
                em *= 1 + mk["hold"]
                peak = max(peak, eq)
                dd = min(dd, eq / peak - 1)
                rows.append((t, r, mk["hold"], to))
                k = "train" if t < SPLIT else "test"
                seg[k][0] *= 1 + r
                seg[k][1] *= 1 + mk["hold"]
                seg[k][2] += 1
                years.setdefault(t[:4], [1.0, 1.0])
                years[t[:4]][0] *= 1 + r
                years[t[:4]][1] *= 1 + mk["hold"]
                tot = sum(w[c] * (1 + snap[c]["hold"]) for c in pick)
                prev = {c: w[c] * (1 + snap[c]["hold"]) / tot for c in pick}
            if not rows:
                continue
            yrs = len(rows) / 12
            cg = lambda f, y: round((f ** (1 / y) - 1) * 100, 1) if y > 0 else None
            out["portfolios"][f"{vname}｜前 {N} 名"] = {
                "年化": cg(eq, yrs), "0050 年化": cg(em, yrs),
                "2018-2023 年化": cg(seg["train"][0], seg["train"][2] / 12), "2018-2023 0050": cg(seg["train"][1], seg["train"][2] / 12),
                "2023-2026 年化": cg(seg["test"][0], seg["test"][2] / 12), "2023-2026 0050": cg(seg["test"][1], seg["test"][2] / 12),
                "每月贏 0050 比例": round(sum(r > m for _, r, m, _ in rows) / len(rows) * 100, 1),
                "贏 0050 的年數": f"{sum(1 for v in years.values() if v[0] > v[1])}/{len(years)}",
                "各年（策略/0050）": {y: f"{(v[0] - 1) * 100:+.0f}% / {(v[1] - 1) * 100:+.0f}%" for y, v in years.items()},
                "最大回撤": round(dd * 100, 1), "平均每月換手": round(sum(x[3] for x in rows[1:]) / max(1, len(rows) - 1) * 100, 1)}
            u.log(f"{vname} 前{N}: " + json.dumps(out["portfolios"][f"{vname}｜前 {N} 名"], ensure_ascii=False)[:400])
    out["persistence"] = persistence(records, reb, ind_of, e, di)
    u.save("research3.json", out)


if __name__ == "__main__":
    main()
