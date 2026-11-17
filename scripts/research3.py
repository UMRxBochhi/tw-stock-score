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
    # ---- 賣出／停利規則：持有到規則觸發才賣，空出的位置補當月基本面最高分、還沒持有的股票
    def exit_test(N, rule):
        slots = []          # 每檔：{"c", "v" 市值占比, "g" 進場後累積報酬倍數, "pk" 最高倍數, "mo" 持有月數}
        eq = em = pk_eq = 1.0
        dd = 0.0
        seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
        n_m = n_sell = 0
        hold_mo = []
        wins = 0
        for idx in reb:
            t, snap = records[idx]
            mk = snap.get("__mkt")
            if not mk:
                continue
            sc = user_score(snap, FUND, None, None)
            ranked = [c for c in sorted(sc, key=lambda c: -sc[c]) if snap[c].get("hold") is not None]
            rpos = {c: i for i, c in enumerate(ranked)}
            keep, cash = [], 0.0 if slots else 1.0
            for sl in slots:
                m = snap.get(sl["c"])
                if not m or m.get("hold") is None or rule(sl, m, rpos.get(sl["c"], 9999)):
                    cash += sl["v"]
                    n_sell += 1
                    hold_mo.append(sl["mo"])
                    continue
                keep.append(sl)
            held = {x["c"] for x in keep}
            new = [c for c in ranked if c not in held][:N - len(keep)]
            for c in new:
                keep.append({"c": c, "v": cash / len(new), "g": 1.0, "pk": 1.0, "mo": 0})
            if not new and cash and keep:          # 沒有可補的就平均加回現有持股
                for x in keep:
                    x["v"] += cash / len(keep)
            tot = sum(x["v"] for x in keep) or 1.0
            buy = sum(x["v"] for x in keep if x["mo"] == 0) / tot if slots else 0.0
            r = sum(x["v"] * snap[x["c"]]["hold"] for x in keep) / tot - buy * COST
            for x in keep:
                h = snap[x["c"]]["hold"]
                x["v"] = x["v"] / tot * (1 + h)
                x["g"] *= 1 + h
                x["pk"] = max(x["pk"], x["g"])
                x["mo"] += 1
            slots = keep
            eq *= 1 + r
            em *= 1 + mk["hold"]
            wins += r > mk["hold"]
            pk_eq = max(pk_eq, eq)
            dd = min(dd, eq / pk_eq - 1)
            k = "train" if t < SPLIT else "test"
            seg[k][0] *= 1 + r
            seg[k][1] *= 1 + mk["hold"]
            seg[k][2] += 1
            n_m += 1
        cg = lambda f, y: round((f ** (1 / y) - 1) * 100, 1) if y > 0 else None
        return {"年化": cg(eq, n_m / 12), "0050 年化": cg(em, n_m / 12),
                "2018-2023 年化": cg(seg["train"][0], seg["train"][2] / 12), "2018-2023 0050": cg(seg["train"][1], seg["train"][2] / 12),
                "2023-2026 年化": cg(seg["test"][0], seg["test"][2] / 12), "2023-2026 0050": cg(seg["test"][1], seg["test"][2] / 12),
                "每月贏 0050 比例": round(wins / max(1, n_m) * 100, 1), "最大回撤": round(dd * 100, 1),
                "平均持有月數": round(sum(hold_mo) / len(hold_mo), 1) if hold_mo else None,
                "每月賣出檔數": round(n_sell / max(1, n_m), 2)}

    down = lambda m: m.get("rev_up_n") == 0
    pe_hi = lambda m: m.get("val_cheap") is not None and -m["val_cheap"] > 93     # 本益比位階 > +1.5σ（約 93 百分位）
    EXITS = {
        "每月全換（掉出前 N 名就賣，基準）": lambda sl, m, rk: rk >= N_,
        "掉出前 30 名才賣": lambda sl, m, rk: rk >= 30,
        "營收預估下修才賣": lambda sl, m, rk: down(m),
        "下修才賣，或過熱停利": lambda sl, m, rk: down(m) or u.is_over(m),
        "下修才賣，或本益比 > +1.5σ 停利": lambda sl, m, rk: down(m) or pe_hi(m),
        "下修才賣，或賺 30% 停利": lambda sl, m, rk: down(m) or sl["g"] >= 1.3,
        "下修才賣，或賺 50% 停利": lambda sl, m, rk: down(m) or sl["g"] >= 1.5,
        "下修才賣，或從高點回落 20% 停損": lambda sl, m, rk: down(m) or sl["g"] / sl["pk"] <= 0.8,
        "掉出前 30 名，或過熱停利": lambda sl, m, rk: rk >= 30 or u.is_over(m),
        "掉出前 30 名，或賺 50% 停利": lambda sl, m, rk: rk >= 30 or sl["g"] >= 1.5,
        "只在過熱時停利（其餘不賣）": lambda sl, m, rk: u.is_over(m),
        "只在本益比 > +1.5σ 時停利": lambda sl, m, rk: pe_hi(m),
        "固定持有 12 個月": lambda sl, m, rk: sl["mo"] >= 12,
        "固定持有 12 個月，或過熱停利": lambda sl, m, rk: sl["mo"] >= 12 or u.is_over(m),
    }
    out["exit_rules"] = {}
    for N_ in (5, 10):
        for name, rule in EXITS.items():
            out["exit_rules"][f"前 {N_} 名｜{name}"] = res = exit_test(N_, rule)
            u.log(f"賣出規則 前{N_} {name}: " + json.dumps(res, ensure_ascii=False))
    # ---- 分散版：基本面分數前 N 檔、每個產業最多 K 檔，每 E 個月整組換；可選台積電核心
    def div_test(N, K, E, core=0.0):
        w = {}
        eq = em = pk = 1.0
        dd = 0.0
        seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
        n_m = wins = 0
        tos = []
        n_ind = []
        for n, idx in enumerate(reb):
            t, snap = records[idx]
            mk = snap.get("__mkt")
            if not mk:
                continue
            w = {c: v for c, v in w.items() if snap.get(c, {}).get("hold") is not None}
            tw = sum(w.values())
            w = {c: v / tw for c, v in w.items()} if tw else {}
            to = 0.0
            if n % E == 0 or not w:
                sc = user_score(snap, FUND, None, None)
                pick, cnt = [], {}
                for c in sorted(sc, key=lambda c: -sc[c]):
                    if snap[c].get("hold") is None or c == "2330":
                        continue
                    g = ind_of(c) or "?"
                    if K and cnt.get(g, 0) >= K:
                        continue
                    cnt[g] = cnt.get(g, 0) + 1
                    pick.append(c)
                    if len(pick) >= N:
                        break
                if not pick:
                    continue
                n_ind.append(len(cnt))
                nw = {c: (1 - core) / len(pick) for c in pick}
                if core and snap.get("2330", {}).get("hold") is not None:
                    nw["2330"] = nw.get("2330", 0) + core
                else:
                    nw = {c: 1 / len(pick) for c in pick}
                to = sum(abs(nw.get(c, 0) - w.get(c, 0)) for c in set(nw) | set(w)) / 2 if w else 1.0
                w = nw
                tos.append(to)
            r = sum(v * snap[c]["hold"] for c, v in w.items()) - to * COST
            tot = sum(v * (1 + snap[c]["hold"]) for c, v in w.items())
            w = {c: v * (1 + snap[c]["hold"]) / tot for c, v in w.items()}
            eq *= 1 + r
            em *= 1 + mk["hold"]
            wins += r > mk["hold"]
            pk = max(pk, eq)
            dd = min(dd, eq / pk - 1)
            k = "train" if t < SPLIT else "test"
            seg[k][0] *= 1 + r
            seg[k][1] *= 1 + mk["hold"]
            seg[k][2] += 1
            n_m += 1
        cg = lambda f, y: round((f ** (1 / y) - 1) * 100, 1) if y > 0 else None
        return {"年化": cg(eq, n_m / 12), "0050 年化": cg(em, n_m / 12),
                "2018-2023 年化": cg(seg["train"][0], seg["train"][2] / 12), "2018-2023 0050": cg(seg["train"][1], seg["train"][2] / 12),
                "2023-2026 年化": cg(seg["test"][0], seg["test"][2] / 12), "2023-2026 0050": cg(seg["test"][1], seg["test"][2] / 12),
                "每月贏 0050 比例": round(wins / max(1, n_m) * 100, 1), "最大回撤": round(dd * 100, 1),
                "平均涵蓋產業數": round(sum(n_ind) / len(n_ind), 1) if n_ind else None,
                "平均每次換手": round(sum(tos[1:]) / max(1, len(tos) - 1) * 100, 1)}

    out["diversified"] = {}
    for core in (0.0, 0.4):
        for E in (1, 3):
            for N, K in ((10, None), (10, 2), (20, None), (20, 2), (20, 3), (30, 2), (30, 3)):
                name = f"{'台積電 40%＋' if core else ''}前 {N} 檔｜每產業{'最多 ' + str(K) + ' 檔' if K else '不限'}｜每 {E} 個月換"
                out["diversified"][name] = res = div_test(N, K, E, core)
                u.log(f"分散 {name}: " + json.dumps(res, ensure_ascii=False))
    # ---- 門檻版：符合條件的股票每檔 per_w，其餘（或沒有符合時全部）放 0050；每 E 個月檢查一次
    def thr_test(cond, per_w, E, K=None):
        w = {}
        eq = em = pk = 1.0
        dd = 0.0
        seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
        n_m = wins = zero = 0
        n_q, sat = [], []
        for n, idx in enumerate(reb):
            t, snap = records[idx]
            mk = snap.get("__mkt")
            if not mk:
                continue
            H = lambda c: mk["hold"] if c == "__0050" else snap.get(c, {}).get("hold")
            gone = sum(v for c, v in w.items() if H(c) is None)
            w = {c: v for c, v in w.items() if H(c) is not None}
            if gone:
                w["__0050"] = w.get("__0050", 0) + gone
            to = 0.0
            if n % E == 0 or not w:
                sc = user_score(snap, FUND, None, None)
                q, cnt = [], {}
                for c in sorted(sc, key=lambda c: -sc[c]):
                    m = snap[c]
                    if m.get("hold") is None or not cond(sc[c], m):
                        continue
                    g = ind_of(c) or "?"
                    if K and cnt.get(g, 0) >= K:
                        continue
                    cnt[g] = cnt.get(g, 0) + 1
                    q.append(c)
                n_q.append(len(q))
                zero += not q
                pw = min(per_w, 1 / len(q)) if q else 0
                nw = {c: pw for c in q}
                nw["__0050"] = 1 - pw * len(q)
                to = sum(abs(nw.get(c, 0) - w.get(c, 0)) for c in set(nw) | set(w)) / 2 if w else 1.0
                w = nw
            sat.append(1 - w.get("__0050", 0))
            r = sum(v * H(c) for c, v in w.items()) - to * COST
            tot = sum(v * (1 + H(c)) for c, v in w.items())
            w = {c: v * (1 + H(c)) / tot for c, v in w.items()}
            eq *= 1 + r
            em *= 1 + mk["hold"]
            wins += r > mk["hold"]
            pk = max(pk, eq)
            dd = min(dd, eq / pk - 1)
            k = "train" if t < SPLIT else "test"
            seg[k][0] *= 1 + r
            seg[k][1] *= 1 + mk["hold"]
            seg[k][2] += 1
            n_m += 1
        cg = lambda f, y: round((f ** (1 / y) - 1) * 100, 1) if y > 0 else None
        return {"年化": cg(eq, n_m / 12), "0050 年化": cg(em, n_m / 12),
                "2018-2023 年化": cg(seg["train"][0], seg["train"][2] / 12), "2018-2023 0050": cg(seg["train"][1], seg["train"][2] / 12),
                "2023-2026 年化": cg(seg["test"][0], seg["test"][2] / 12), "2023-2026 0050": cg(seg["test"][1], seg["test"][2] / 12),
                "每月贏 0050 比例": round(wins / max(1, n_m) * 100, 1), "最大回撤": round(dd * 100, 1),
                "平均符合檔數": round(sum(n_q) / len(n_q), 1) if n_q else None,
                "沒有符合的次數比例": round(zero / max(1, len(n_q)) * 100, 1),
                "平均個股占比": round(sum(sat) / len(sat) * 100, 1) if sat else None}

    up = lambda m, k: (m.get("rev_up_n") or 0) >= k
    hi = lambda m: m.get("rev_hi") is not None and m["rev_hi"] >= 0
    CONDS = {
        "分數 ≥ 80": lambda v, m: v >= 80,
        "分數 ≥ 85": lambda v, m: v >= 85,
        "分數 ≥ 90": lambda v, m: v >= 90,
        "分數 ≥ 95": lambda v, m: v >= 95,
        "連續上修 ≥ 3 個月": lambda v, m: up(m, 3),
        "連續上修 ≥ 3 個月＋營收創新高": lambda v, m: up(m, 3) and hi(m),
        "連續上修 ≥ 6 個月＋營收創新高": lambda v, m: up(m, 6) and hi(m),
        "分數 ≥ 85＋連續上修 ≥ 3 個月": lambda v, m: v >= 85 and up(m, 3),
        "分數 ≥ 90＋連續上修 ≥ 3 個月＋創新高": lambda v, m: v >= 90 and up(m, 3) and hi(m),
    }
    out["threshold"] = {}
    for E in (1, 3):
        for name, cond in CONDS.items():
            for per_w, lab in ((0.05, "每檔 5%，其餘 0050"), (1.0, "全部平均買，沒有才買 0050")):
                key = f"{name}｜{lab}｜每 {E} 個月"
                out["threshold"][key] = res = thr_test(cond, per_w, E, K=2)
                u.log(f"門檻 {key}: " + json.dumps(res, ensure_ascii=False))
    # ---- 什麼情況下組合比較容易贏 0050：每個月都當起點，持有 3 個月，依進場當下的狀態分組
    def cond_test(cond, H=3, K=2, MAXN=20):
        obs = []
        for n, idx in enumerate(reb[:-H]):
            t, snap = records[idx]
            fut = [records[reb[n + j]][1] for j in range(H)]
            if any(not f.get("__mkt") for f in fut):
                continue
            sc = user_score(snap, FUND, None, None)
            q, cnt, nq = [], {}, 0
            for c in sorted(sc, key=lambda c: -sc[c]):
                m = snap[c]
                if not cond(sc[c], m):
                    continue
                nq += 1
                g = ind_of(c) or "?"
                if cnt.get(g, 0) >= K or len(q) >= MAXN:
                    continue
                cnt[g] = cnt.get(g, 0) + 1
                q.append(c)
            rets = []
            for c in q:
                g = 1.0
                for f in fut:
                    h = (f.get(c) or {}).get("hold")
                    if h is None:
                        g = None
                        break
                    g *= 1 + h
                if g is not None:
                    rets.append(g - 1)
            if len(rets) < 3:
                continue
            mk = 1.0
            for f in fut:
                mk *= 1 + f["__mkt"]["hold"]
            i = di[t]
            m60 = e[i] / e[i - 60] - 1 if i >= 60 and e[i] and e[i - 60] else None
            m250 = e[i] / e[i - 250] - 1 if i >= 250 and e[i] and e[i - 250] else None
            ts = snap.get("2330") or {}
            avg_sc = sum(sc[c] for c in q) / len(q)
            ex = sum(rets) / len(rets) - (mk - 1)
            # 個股層級：組合內有幾成個股贏 0050
            hit = sum(r > mk - 1 for r in rets) / len(rets)
            obs.append({"t": t, "ex": ex, "hit": hit, "state": {
                "大盤多空（0050 在半年線上下）": "多頭" if b.MKT.get(t, {}).get("up120") else "空頭",
                "0050 近 3 個月漲跌": None if m60 is None else ("跌" if m60 < 0 else "漲 0~10%" if m60 < 0.10 else "漲 > 10%"),
                "0050 近 1 年漲跌": None if m250 is None else ("跌" if m250 < 0 else "漲 0~25%" if m250 < 0.25 else "漲 > 25%"),
                "台積電強弱（距季線）": None if ts.get("b60") is None else ("弱（季線下）" if ts["b60"] < 0 else "普通（0~10%）" if ts["b60"] < 10 else "強（> 10%）"),
                "符合條件的檔數（市場廣度）": "< 15 檔" if nq < 15 else "15~40 檔" if nq <= 40 else "> 40 檔",
                "入選股平均分數": "< 80" if avg_sc < 80 else "80~88" if avg_sc < 88 else "≥ 88",
                "進場月份": f"{int(t[4:6])} 月",
            }})
        res = {"全部": None}
        def summ(xs, lab):
            if not xs:
                return None
            exs = [o["ex"] for o in xs]
            tt = b.nw_t(exs, H - 1) if len(exs) >= 10 else None
            return {"次數": len(xs), "組合贏 0050 機率": round(sum(x > 0 for x in exs) / len(exs) * 100, 1),
                    "平均超額（3 個月）": round(sum(exs) / len(exs) * 100, 2), "t": tt and round(tt, 2),
                    "組合內個股贏 0050 比例": round(sum(o["hit"] for o in xs) / len(xs) * 100, 1),
                    "2018-2023 次數/勝率": (lambda a: f"{len(a)} / {round(sum(o['ex'] > 0 for o in a) / len(a) * 100) if a else '—'}%")([o for o in xs if o["t"] < SPLIT]),
                    "2023-2026 次數/勝率": (lambda a: f"{len(a)} / {round(sum(o['ex'] > 0 for o in a) / len(a) * 100) if a else '—'}%")([o for o in xs if o["t"] >= SPLIT])}
        res["全部"] = summ(obs, "全部")
        for dim in (obs[0]["state"] if obs else {}):
            res[dim] = {}
            for v in sorted({o["state"][dim] for o in obs if o["state"][dim] is not None}):
                res[dim][v] = summ([o for o in obs if o["state"][dim] == v], v)
        return res

    out["conditional"] = {
        "連續上修 ≥ 3 個月＋營收創新高": cond_test(lambda v, m: up(m, 3) and hi(m)),
        "分數 ≥ 80": cond_test(lambda v, m: v >= 80),
    }
    u.log("條件分組: " + json.dumps(out["conditional"], ensure_ascii=False)[:3000])
    # ---- 特徵組合的個股勝率：每月起點、市值 ≥ 100 億，符合條件的個股持有 H 個月 vs 0050
    def combo_test():
        pos = lambda m: -m["val_cheap"] if m.get("val_cheap") is not None else None
        cheap = lambda m: pos(m) is not None and pos(m) < 31          # 約 −0.5σ 以下
        fair = lambda m: pos(m) is not None and pos(m) <= 69          # 約 +0.5σ 以下
        rich = lambda m: pos(m) is not None and pos(m) > 69
        CS = {
            "全部（市值 ≥ 100 億）": lambda m: True,
            "本益比便宜（< −0.5σ）": cheap,
            "營收連續上修 ≥ 2 個月": lambda m: up(m, 2),
            "營收連續上修 ≥ 3 個月": lambda m: up(m, 3),
            "上修 ≥ 2＋本益比便宜": lambda m: up(m, 2) and cheap(m),
            "上修 ≥ 3＋本益比便宜": lambda m: up(m, 3) and cheap(m),
            "上修 ≥ 3＋本益比不貴（≤ +0.5σ）": lambda m: up(m, 3) and fair(m),
            "上修 ≥ 3＋本益比偏貴（> +0.5σ）": lambda m: up(m, 3) and rich(m),
            "上修 ≥ 3＋創新高": lambda m: up(m, 3) and hi(m),
            "上修 ≥ 3＋創新高＋本益比便宜": lambda m: up(m, 3) and hi(m) and cheap(m),
            "上修 ≥ 3＋創新高＋本益比不貴": lambda m: up(m, 3) and hi(m) and fair(m),
            "上修 ≥ 6＋創新高＋本益比不貴": lambda m: up(m, 6) and hi(m) and fair(m),
        }
        res = {}
        for H in (3, 6, 12):
            ev = {k: [] for k in CS}
            bk = {k: [] for k in CS}
            for n, idx in enumerate(reb[:-H]):
                t, snap = records[idx]
                fut = [records[reb[n + j]][1] for j in range(H)]
                if any(not f.get("__mkt") for f in fut):
                    continue
                mk = 1.0
                for f in fut:
                    mk *= 1 + f["__mkt"]["hold"]
                rets = {}
                for c, m in snap.items():
                    if c.startswith("__") or (m.get("cap") or 0) < MIN_CAP:
                        continue
                    g = 1.0
                    for f in fut:
                        h = (f.get(c) or {}).get("hold")
                        if h is None:
                            g = None
                            break
                        g *= 1 + h
                    if g is not None:
                        rets[c] = g - mk
                for k, fn in CS.items():
                    xs = [rets[c] for c in rets if fn(snap[c])]
                    ev[k] += [(t, x) for x in xs]
                    if len(xs) >= 3:
                        bk[k].append((t, sum(xs) / len(xs)))
            out_h = {}
            for k in CS:
                e_, b_ = ev[k], bk[k]
                if not e_:
                    continue
                xs = sorted(x for _, x in e_)
                wr = lambda a: round(sum(x > 0 for _, x in a) / len(a) * 100, 1) if a else None
                out_h[k] = {"事件數": len(e_), "個股贏 0050 機率": wr(e_),
                            "個股平均超額": round(sum(xs) / len(xs) * 100, 1), "個股超額中位數": round(xs[len(xs) // 2] * 100, 1),
                            "組合贏 0050 機率": wr(b_), "組合平均超額": round(sum(x for _, x in b_) / len(b_) * 100, 1) if b_ else None,
                            "組合 t": (lambda tt: tt and round(tt, 2))(b.nw_t([x for _, x in b_], H - 1)) if len(b_) >= 10 else None,
                            "前段/後段 組合勝率": f"{wr([x for x in b_ if x[0] < SPLIT])} / {wr([x for x in b_ if x[0] >= SPLIT])}",
                            "前段/後段 個股勝率": f"{wr([x for x in e_ if x[0] < SPLIT])} / {wr([x for x in e_ if x[0] >= SPLIT])}",
                            "平均每次檔數": round(len(e_) / max(1, len(b_)), 1)}
            res[f"持有 {H} 個月"] = out_h
        return res

    out["combo"] = combo_test()
    u.log("特徵組合: " + json.dumps(out["combo"], ensure_ascii=False)[:3000])
    out["persistence"] = persistence(records, reb, ind_of, e, di)
    u.save("research3.json", out)


if __name__ == "__main__":
    main()
