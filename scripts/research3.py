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
import os
import math
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


DIVS = {} if os.environ.get("PRICE_ONLY") else u.load_divs()     # PRICE_ONLY=1：只算股價（舊版，比較用）


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

    pe_m = u.load("pe_monthly.json", {})
    pe_keys = sorted(pe_m)
    _rev0 = u.load("fundamentals.json", {}).get("revenue", {})
    _ind0, _fc = {}, {}

    def fwd_factor(code, rk):
        """未來 12 個月推估營收 / 近 12 個月營收 = 1 + 預估成長"""
        key = (code, rk)
        if key not in _fc:
            h = revh.get(code, {})
            cur = [h.get(shift(rk, i), [None])[0] for i in range(12)]
            e12 = est12(h, rk)
            _fc[key] = e12 / sum(cur) if e12 and None not in cur and sum(cur) > 0 else None
        return _fc[key]

    # 每季損益（觀測站累計數 → 單季）：毛利率、營益率
    import gzip
    _incp = b.HIST / "inc.json.gz"
    incdb = json.loads(gzip.decompress(_incp.read_bytes())) if _incp.exists() else {}
    u.log(f"季損益公司數 {len(incdb)}")

    def qkey_prev(k):
        y, q = int(k[:-2]), int(k[-1])
        return f"{y - 1}Q4" if q == 1 else f"{y}Q{q - 1}"

    _qm = {}

    def q_margin(c, k):
        """單季（毛利率, 營益率）%"""
        if (c, k) not in _qm:
            h = incdb.get(c, {})
            v = h.get(k)
            res = None
            if v and v[0]:
                if k.endswith("Q1"):
                    q = v
                else:
                    pv = h.get(qkey_prev(k))
                    q = [a - b_ if a is not None and b_ is not None else None for a, b_ in zip(v, pv)] if pv else None
                if q and q[0] and q[0] > 0:
                    res = (q[1] / q[0] * 100 if q[1] is not None else None, q[2] / q[0] * 100 if q[2] is not None else None)
            _qm[(c, k)] = res
        return _qm[(c, k)]

    def avail_q(t):
        """t 這天一定已公布的最新一季（保守：公布期限後的下個月）"""
        y, mo = int(t[:4]) - 1911, int(t[4:6])
        return f"{y}Q3" if mo >= 12 else f"{y}Q2" if mo >= 9 else f"{y}Q1" if mo >= 6 else f"{y - 1}Q4" if mo >= 4 else f"{y - 1}Q3"

    def zlog(v, xs):
        lx = [math.log(x) for x in xs]
        mu = sum(lx) / len(lx)
        sd = (sum((a - mu) ** 2 for a in lx) / len(lx)) ** 0.5
        return (math.log(v) - mu) / sd if sd > 0 else None

    # 特徵
    for n, idx in enumerate(reb):
        t, snap = records[idx]
        rk = b.avail_rev_key(t)
        prev_qf = records[idx - 4][1] if idx >= 4 else {}
        prev_q = {w: (records[idx - w][1] if idx >= w else {}) for w in (13, 26, 52)}   # 約 3、6、12 個月前（每週一筆）
        pk = [k for k in pe_keys if k < t[:6]][-60:]
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
            cur12 = [h.get(shift(rk, i), [None])[0] for i in range(12)]
            old12 = [h.get(shift(rk, i + 36), [None])[0] for i in range(12)]
            if None not in cur12 + old12 and sum(old12) > 0:
                m["cagr3"] = (sum(cur12) / sum(old12)) ** (1 / 3) - 1
            # 本益比位階（σ）：傳統＝近四季 EPS；前瞻＝近四季 EPS ×（1 + 營收預估成長）
            s0 = pe_m[pk[-1]].get(c) if pk else None
            if s0 and s0[0] and s0[1] and m.get("p"):
                pe_now = s0[0] * m["p"] / s0[1]
                hist = [(k, (pe_m[k].get(c) or [None])[0]) for k in pk]
                hist = [(k, x) for k, x in hist if x and x > 0]
                if pe_now > 0 and len(hist) >= 24:
                    m["pe_zt"] = zlog(pe_now, [x for _, x in hist])
                    if c not in _ind0:
                        _ind0[c] = u.industry(c, _rev0)
                    fn = fwd_factor(c, rk)
                    if _ind0[c] not in u.FWD_SKIP_IND and fn and fn > 0:
                        fh = []
                        for k, x in hist:
                            f = fwd_factor(c, b.avail_rev_key(k + "28"))
                            if f and f > 0:
                                fh.append(x / f)
                        if len(fh) >= 24:
                            m["pe_zf"] = zlog(pe_now / fn, fh)
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
            for w, snap_w in prev_q.items():
                qw = (snap_w.get(c) or {}).get("qf")
                if m.get("qf") is not None and qw is not None:
                    m[f"qd{w}"] = m["qf"] - qw
            q0 = (prev_qf.get(c) or {}).get("qf")
            if m.get("qf") is not None and q0 is not None:
                m["qd4"] = m["qf"] - q0
        if incdb:
            k0 = avail_q(t)
            k1 = qkey_prev(k0)
            k4 = f"{int(k0[:-2]) - 1}{k0[-2:]}"
            for c, m in snap.items():
                if c.startswith("__"):
                    continue
                a, p1, p4 = q_margin(c, k0), q_margin(c, k1), q_margin(c, k4)
                if a and a[0] is not None:
                    m["gm_q"] = a[0]
                    if p4 and p4[0] is not None:
                        m["gm_yoy"] = a[0] - p4[0]
                    if p1 and p1[0] is not None:
                        m["gm_qoq"] = a[0] - p1[0]
                if a and a[1] is not None and p4 and p4[1] is not None:
                    m["om_yoy"] = a[1] - p4[1]
        # 持有報酬：到下一次換股
        nxt = reb[n + 1] if n + 1 < len(reb) else None
        i0 = di[t]
        i1 = di[records[nxt][0]] if nxt is not None else None
        # 不能用「持有期間有沒有分割、減資、停止交易」來篩選（那是事後才知道的，舊版這樣做會偷看未來）：
        # 一律用含股利的累積報酬（除權息／減資乘上還原係數，還原後單日仍 ±15% 視為 0），停止交易就用最後價格
        def tr_hold(c, p0):
            fac = DIVS.get(c) or {}
            g, last, pend = 1.0, p0, 1.0
            for d in dates[i0 + 1:i1 + 1]:
                pend *= fac.get(d, 1.0)
                v = (px[d].get(c) or [None])[0]
                if not v:
                    continue
                r = v / last * pend
                if 0.85 <= r <= 1.15:
                    g *= r
                last, pend = v, 1.0
            return g - 1

        for c, m in snap.items():
            if i1 is None or c.startswith("__"):
                continue
            p0 = (px[dates[i0]].get(c) or [None])[0] or m.get("p")
            if p0:
                m["hold"] = tr_hold(c, p0)
        snap["__mkt"] = {"hold": tr_hold("0050", (px[dates[i0]].get("0050") or [None])[0] or e[i0])} if i1 is not None and e[i0] and e[i1] else None

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
    for k in ("rev_up_n", "rev_rev1", "rev_rev3", "trend_ok", "qd4", "b20", "vr", "sup_score", "sup_bull", "qf", "fin_use", "pe_zt", "pe_zf"):
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
    _sc_cache = {}

    def exit_test(N, rule, entry=None):
        slots = []          # 每檔：{"c", "v" 市值占比, "g" 進場後累積報酬倍數, "pk" 最高倍數, "mo" 持有月數}
        eq = em = pk_eq = 1.0
        dd = 0.0
        seg = {"train": [1.0, 1.0, 0], "test": [1.0, 1.0, 0]}
        n_m = n_sell = 0
        hold_mo = []
        exit_g = []
        wins = 0
        for idx in reb:
            t, snap = records[idx]
            mk = snap.get("__mkt")
            if not mk:
                continue
            if idx not in _sc_cache:
                _sc_cache[idx] = user_score(snap, FUND, None, None)
            sc = _sc_cache[idx]
            ranked = [c for c in sorted(sc, key=lambda c: -sc[c]) if snap[c].get("hold") is not None
                      and (entry is None or entry(snap[c]))]
            rpos = {c: i for i, c in enumerate(ranked)}
            keep, cash = [], 0.0 if slots else 1.0
            for sl in slots:
                m = snap.get(sl["c"])
                if not m or m.get("hold") is None or rule(sl, m, rpos.get(sl["c"], 9999)):
                    cash += sl["v"]
                    n_sell += 1
                    hold_mo.append(sl["mo"])
                    exit_g.append(sl["g"] - 1)
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
                "每月賣出檔數": round(n_sell / max(1, n_m), 2),
                "下車時平均報酬": round(sum(exit_g) / len(exit_g) * 100, 1) if exit_g else None,
                "下車時虧損 > 30% 的比例": round(sum(g < -0.3 for g in exit_g) / len(exit_g) * 100, 1) if exit_g else None,
                "最差一筆": round(min(exit_g) * 100, 1) if exit_g else None,
                "仍持有中的最差": round(min(x["g"] - 1 for x in slots) * 100, 1) if slots else None}

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
    def thr_test(cond, per_w, E, K=None, off=0, cost=COST, rnd=None, maxn=None):
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
            if n % E == off or not w:
                sc = user_score(snap, FUND, None, None)
                q, cnt = [], {}
                order = sorted(sc, key=lambda c: -sc[c])
                if rnd is not None:
                    rnd.shuffle(order)
                for c in order:
                    m = snap[c]
                    if m.get("hold") is None or not cond(sc[c], m):
                        continue
                    g = ind_of(c) or "?"
                    if K and cnt.get(g, 0) >= K:
                        continue
                    cnt[g] = cnt.get(g, 0) + 1
                    q.append(c)
                    if maxn and len(q) >= maxn:
                        break
                n_q.append(len(q))
                zero += not q
                pw = min(per_w, 1 / len(q)) if q else 0
                nw = {c: pw for c in q}
                nw["__0050"] = 1 - pw * len(q)
                to = sum(abs(nw.get(c, 0) - w.get(c, 0)) for c in set(nw) | set(w)) / 2 if w else 1.0
                w = nw
            sat.append(1 - w.get("__0050", 0))
            r = sum(v * H(c) for c, v in w.items()) - to * cost
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
    def combo_test(CS_in=None):
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
        if CS_in:
            CS = CS_in
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
    zt = lambda m: m.get("pe_zt")
    zf = lambda m: m.get("pe_zf")
    lt = lambda f, v: (lambda m: f(m) is not None and f(m) < v)
    gt = lambda f, v: (lambda m: f(m) is not None and f(m) > v)
    mid = lambda f: (lambda m: f(m) is not None and -0.5 <= f(m) <= 0.5)
    uh = lambda m: up(m, 3) and hi(m)
    out["combo_pe"] = combo_test({
        "全部（市值 ≥ 100 億）": lambda m: True,
        "傳統本益比 < −0.5σ": lt(zt, -0.5),
        "前瞻本益比 < −0.5σ": lt(zf, -0.5),
        "前瞻本益比 > +0.5σ": gt(zf, 0.5),
        "上修 ≥ 3＋創新高（不看位階）": uh,
        "上修 ≥ 3＋創新高＋前瞻 < −0.5σ": lambda m: uh(m) and lt(zf, -0.5)(m),
        "上修 ≥ 3＋創新高＋前瞻 −0.5~+0.5σ": lambda m: uh(m) and mid(zf)(m),
        "上修 ≥ 3＋創新高＋前瞻 > +0.5σ": lambda m: uh(m) and gt(zf, 0.5)(m),
        "上修 ≥ 3＋創新高＋前瞻 ≤ +0.5σ": lambda m: uh(m) and zf(m) is not None and zf(m) <= 0.5,
        "上修 ≥ 3＋創新高＋傳統 < −0.5σ": lambda m: uh(m) and lt(zt, -0.5)(m),
        "上修 ≥ 3＋前瞻 < −0.5σ": lambda m: up(m, 3) and lt(zf, -0.5)(m),
        "上修 ≥ 2＋前瞻 < −0.5σ": lambda m: up(m, 2) and lt(zf, -0.5)(m),
        "上修 ≥ 3＋創新高＋前瞻 < −1σ": lambda m: uh(m) and lt(zf, -1.0)(m),
    })
    u.log("前瞻本益比組合: " + json.dumps(out["combo_pe"], ensure_ascii=False)[:3000])
    u.log("特徵組合: " + json.dumps(out["combo"], ensure_ascii=False)[:3000])
    # ---- 穩健性：本季名單策略換不同起始月、加倍交易成本、和隨機挑股比較
    import random
    uh_ = lambda v, m: up(m, 3) and hi(m)
    rob = {}
    for off in (0, 1, 2):
        rob[f"起始月份錯開 {off} 個月"] = thr_test(uh_, 0.05, 3, K=2, off=off, maxn=20)
    rob["交易成本加倍（1%）"] = thr_test(uh_, 0.05, 3, K=2, cost=0.01, maxn=20)
    rr = [thr_test(lambda v, m: True, 0.05, 3, K=2, rnd=random.Random(sd), maxn=20) for sd in range(20)]
    rr = [x for x in rr if x.get("年化") is not None]
    ys = sorted(x["年化"] for x in rr)
    rob["隨機挑 20 檔大型股（同樣每檔 5%、每產業 2 檔、其餘 0050），20 次"] = {
        "年化中位數": ys[len(ys) // 2], "年化最低/最高": f"{ys[0]} / {ys[-1]}",
        "2018-2023 中位數": sorted(x["2018-2023 年化"] for x in rr)[len(rr) // 2],
        "2023-2026 中位數": sorted(x["2023-2026 年化"] for x in rr)[len(rr) // 2],
        "0050 年化": rr[0]["0050 年化"]}
    out["robust"] = rob

    # ---- 平常時期：本季名單加上「外資持股高」「近 3 年長期成長」會不會更好（三種起始月平均，避免挑到幸運月份）
    qf_ = lambda m, v: (m.get("qf") or 0) >= v
    cg_ = lambda m, v: m.get("cagr3") is not None and m["cagr3"] >= v
    Q2 = {
        "上修 ≥ 3＋創新高（目前網站）": lambda v, m: up(m, 3) and hi(m),
        "＋外資持股 ≥ 20%": lambda v, m: up(m, 3) and hi(m) and qf_(m, 20),
        "＋外資持股 ≥ 30%": lambda v, m: up(m, 3) and hi(m) and qf_(m, 30),
        "＋近 3 年營收年成長 ≥ 10%": lambda v, m: up(m, 3) and hi(m) and cg_(m, 0.10),
        "＋外資 ≥ 20%＋3 年成長 ≥ 10%": lambda v, m: up(m, 3) and hi(m) and qf_(m, 20) and cg_(m, 0.10),
        "＋外資 ≥ 30%＋3 年成長 ≥ 10%": lambda v, m: up(m, 3) and hi(m) and qf_(m, 30) and cg_(m, 0.10),
        "上修 ≥ 1＋外資 ≥ 20%＋3 年成長 ≥ 10%（不要求創新高）": lambda v, m: up(m, 1) and qf_(m, 20) and cg_(m, 0.10),
    }
    out["quarter2"] = {}
    for name, cond in Q2.items():
        rs = [thr_test(cond, 0.05, 3, K=2, off=o, maxn=20) for o in (0, 1, 2)]
        avg = lambda k: round(sum(r[k] for r in rs if r.get(k) is not None) / len(rs), 1)
        out["quarter2"][name] = {"年化（三種起始月平均）": avg("年化"), "三種起始月": [r["年化"] for r in rs],
                                 "0050 年化": rs[0]["0050 年化"], "2018-2023": avg("2018-2023 年化"), "2018-2023 0050": rs[0]["2018-2023 0050"],
                                 "2023-2026": avg("2023-2026 年化"), "2023-2026 0050": rs[0]["2023-2026 0050"],
                                 "每月贏 0050": avg("每月贏 0050 比例"), "最大回撤": avg("最大回撤"), "平均檔數": avg("平均符合檔數"),
                                 "個股占比": avg("平均個股占比")}
        u.log(f"本季名單變化 {name}: " + json.dumps(out["quarter2"][name], ensure_ascii=False))
    u.log("穩健性: " + json.dumps(rob, ensure_ascii=False)[:2000])
    # ---- 下車訊號：進場用網站本季名單條件，比較各種出場方式（含避開大虧的能力）
    ent = lambda m: (m.get("rev_up_n") or 0) >= 1 and (m.get("qf") or 0) >= 20 and m.get("cagr3") is not None and m["cagr3"] >= 0.10
    dd_ = lambda sl, k: sl["g"] / sl["pk"] <= 1 - k
    EX2 = {
        "每季重挑前 10（網站目前做法：不在前 10 就換）": lambda sl, m, rk: sl["mo"] % 3 == 0 and rk >= 10,
        "每季重挑前 10，或從高點回落 30%": lambda sl, m, rk: (sl["mo"] % 3 == 0 and rk >= 10) or dd_(sl, 0.30),
        "每季檢查，不符合條件才下車": lambda sl, m, rk: sl["mo"] % 3 == 0 and not ent(m),
        "每月檢查，不符合條件就下車": lambda sl, m, rk: not ent(m),
        "從高點回落 20% 就下車": lambda sl, m, rk: dd_(sl, 0.20),
        "從高點回落 30% 就下車": lambda sl, m, rk: dd_(sl, 0.30),
        "賺 50% 就走": lambda sl, m, rk: sl["g"] >= 1.5,
        "賺 50% 就走，或從高點回落 20%": lambda sl, m, rk: sl["g"] >= 1.5 or dd_(sl, 0.20),
        "營收近 3 月年增轉負才下車": lambda sl, m, rk: m.get("rev3") is not None and m["rev3"] < 0,
        "每季不符合條件，或從高點回落 20%": lambda sl, m, rk: (sl["mo"] % 3 == 0 and not ent(m)) or dd_(sl, 0.20),
        "每季不符合條件，或從高點回落 30%": lambda sl, m, rk: (sl["mo"] % 3 == 0 and not ent(m)) or dd_(sl, 0.30),
        "營收年增轉負，或從高點回落 30%": lambda sl, m, rk: (m.get("rev3") is not None and m["rev3"] < 0) or dd_(sl, 0.30),
        "永遠不賣（看最壞情況）": lambda sl, m, rk: False,
    }
    out["exit2"] = {}
    for name, rule in EX2.items():
        out["exit2"][name] = res = exit_test(10, rule, entry=ent)
        u.log(f"下車訊號 {name}: " + json.dumps(res, ensure_ascii=False))
    # ---- 外資持股：當下比例 vs 不同期間的變化，對之後 1、3、12 個月報酬的預測力（市值 ≥ 100 億）
    qlong = {}
    for k, lab in (("qf", "當下持股比例"), ("qd4", "4 週變化"), ("qd13", "3 個月變化"), ("qd26", "6 個月變化"), ("qd52", "12 個月變化")):
        qlong[lab] = {}
        for H in (1, 3, 12):
            ics, spread = [], []
            for n, idx in enumerate(reb[:-H]):
                t, snap = records[idx]
                fut = [records[reb[n + j]][1] for j in range(H)]
                f_, r_ = {}, {}
                for c, m in snap.items():
                    if c.startswith("__") or (m.get("cap") or 0) < MIN_CAP or m.get(k) is None:
                        continue
                    g = 1.0
                    for f in fut:
                        h = (f.get(c) or {}).get("hold")
                        if h is None:
                            g = None
                            break
                        g *= 1 + h
                    if g is not None:
                        f_[c], r_[c] = m[k], g - 1
                if len(f_) < 30:
                    continue
                v = b.spearman(f_, r_)
                if v is not None:
                    ics.append(v)
                cs = sorted(f_, key=lambda c: f_[c])
                n3 = len(cs) // 3
                spread.append(sum(r_[c] for c in cs[-n3:]) / n3 - sum(r_[c] for c in cs[:n3]) / n3)
            if len(ics) >= 10:
                tt = b.nw_t(ics, max(1, H - 1))
                qlong[lab][f"之後 {H} 個月"] = {"IC": round(sum(ics) / len(ics) * 100, 2), "t": tt and round(tt, 2), "次數": len(ics),
                                             "最高 1/3 減最低 1/3 報酬": round(sum(spread) / len(spread) * 100, 1),
                                             "高組勝出比例": round(sum(x > 0 for x in spread) / len(spread) * 100)}
    out["qf_long"] = qlong
    u.log("外資持股長期變化: " + json.dumps(qlong, ensure_ascii=False))
    # ---- 網格：外資門檻 × 基本面檢查頻率 × 高點回落停損；用 2018~2023 挑最好的，看 2023~2026 是否仍有效
    grid = []
    for qv in (20, 30, 40):
        en = (lambda q: (lambda m: (m.get("rev_up_n") or 0) >= 1 and (m.get("qf") or 0) >= q
                         and m.get("cagr3") is not None and m["cagr3"] >= 0.10))(qv)
        for chk in ("每季", "每月"):
            for stop in (None, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
                def rule(sl, m, rk, en=en, chk=chk, stop=stop):
                    bad = (sl["mo"] % 3 == 0 if chk == "每季" else True) and not en(m)
                    return bad or (stop is not None and sl["g"] / sl["pk"] <= 1 - stop)
                r = exit_test(10, rule, entry=en)
                grid.append({"外資門檻": qv, "基本面檢查": chk, "高點回落停損": f"{int(stop * 100)}%" if stop else "不設",
                             "年化": r["年化"], "2018-2023": r["2018-2023 年化"], "2023-2026": r["2023-2026 年化"],
                             "最大回撤": r["最大回撤"], "平均持有月數": r["平均持有月數"], "下車虧 > 30% 比例": r["下車時虧損 > 30% 的比例"],
                             "最差一筆": r["最差一筆"], "每月贏 0050": r["每月贏 0050 比例"]})
    best_train = max(grid, key=lambda x: x["2018-2023"] or -99)
    rank_test = sorted(grid, key=lambda x: -(x["2023-2026"] or -99))
    out["exit_grid"] = {"0050": {"年化": 25.9, "2018-2023": 13.7, "2023-2026": 46.4}, "全部": grid,
                        "用 2018-2023 挑的最佳": best_train,
                        "它在 2023-2026 的名次": f"{rank_test.index(best_train) + 1} / {len(grid)}"}
    u.log("下車網格: " + json.dumps(out["exit_grid"], ensure_ascii=False)[:3000])
    # ---- 毛利率：營收上修但毛利率大降（像致茂 2026Q3）是不是比較差
    if incdb:
        gm = {"說明": "毛利率變化用最新已公布一季（公布期限後下個月才使用），單位百分點；營益率同。網站規則＝市值≥100億＋上修≥1＋外資≥30%＋3年成長≥10%，前10、每月不符合就換、高點回落30%下車"}
        cov = [sum(1 for c, m in records[i][1].items() if not c.startswith("__") and m.get("gm_yoy") is not None) for i in reb]
        gm["每次換股有毛利率年變化的公司數（最少/中位）"] = f"{min(cov)} / {sorted(cov)[len(cov) // 2]}"
        ic = {}
        for k, lab in (("gm_yoy", "毛利率年變化"), ("gm_qoq", "毛利率季變化"), ("om_yoy", "營益率年變化"), ("gm_q", "毛利率水準")):
            for only_up in (False, True):
                for H in (1, 3, 12):
                    ics, spread = [], []
                    for n, idx in enumerate(reb[:-H]):
                        t, snap = records[idx]
                        fut = [records[reb[n + j]][1] for j in range(H)]
                        f_, r_ = {}, {}
                        for c, m in snap.items():
                            if c.startswith("__") or (m.get("cap") or 0) < MIN_CAP or m.get(k) is None:
                                continue
                            if only_up and (m.get("rev_up_n") or 0) < 1:
                                continue
                            g = 1.0
                            for f in fut:
                                h = (f.get(c) or {}).get("hold")
                                if h is None:
                                    g = None
                                    break
                                g *= 1 + h
                            if g is not None:
                                f_[c], r_[c] = m[k], g - 1
                        if len(f_) < 30:
                            continue
                        v = b.spearman(f_, r_)
                        if v is not None:
                            ics.append(v)
                        cs = sorted(f_, key=lambda c: f_[c])
                        n3 = len(cs) // 3
                        spread.append(sum(r_[c] for c in cs[-n3:]) / n3 - sum(r_[c] for c in cs[:n3]) / n3)
                    if len(ics) >= 10:
                        tt = b.nw_t(ics, max(1, H - 1))
                        ic[f"{lab}｜{'營收上修中' if only_up else '市值≥100億全部'}｜之後 {H} 個月"] = {
                            "IC": round(sum(ics) / len(ics) * 100, 2), "t": tt and round(tt, 2), "次數": len(ics),
                            "最高 1/3 減最低 1/3 報酬": round(sum(spread) / len(spread) * 100, 1),
                            "高組勝出比例": round(sum(x > 0 for x in spread) / len(spread) * 100)}
        gm["預測力"] = ic
        u.log("毛利率預測力: " + json.dumps(ic, ensure_ascii=False)[:3000])
        base = lambda m: (m.get("rev_up_n") or 0) >= 1 and (m.get("qf") or 0) >= 30 and m.get("cagr3") is not None and m["cagr3"] >= 0.10
        nd = lambda m, k, v: m.get(k) is None or m[k] >= v      # 沒有資料（金融業等）不排除
        GM = {
            "網站規則（基準）": base,
            "＋排除毛利率年減 > 3 個百分點": lambda m: base(m) and nd(m, "gm_yoy", -3),
            "＋排除毛利率年減 > 5 個百分點": lambda m: base(m) and nd(m, "gm_yoy", -5),
            "＋排除毛利率年減 > 10 個百分點": lambda m: base(m) and nd(m, "gm_yoy", -10),
            "＋排除毛利率季減 > 3 個百分點": lambda m: base(m) and nd(m, "gm_qoq", -3),
            "＋排除毛利率季減 > 5 個百分點": lambda m: base(m) and nd(m, "gm_qoq", -5),
            "＋排除營益率年減 > 3 個百分點": lambda m: base(m) and nd(m, "om_yoy", -3),
            "＋只要毛利率年增（≥ 0）": lambda m: base(m) and nd(m, "gm_yoy", 0),
            "＋只要營益率年增（≥ 0）": lambda m: base(m) and nd(m, "om_yoy", 0),
        }
        gm["網站規則加毛利率條件"] = {}
        for name, en in GM.items():
            rule = (lambda en: (lambda sl, m, rk: (not en(m)) or sl["g"] / sl["pk"] <= 0.7))(en)
            r = exit_test(10, rule, entry=en)
            gm["網站規則加毛利率條件"][name] = {k: r[k] for k in ("年化", "0050 年化", "2018-2023 年化", "2018-2023 0050", "2023-2026 年化", "2023-2026 0050",
                                                       "每月贏 0050 比例", "最大回撤", "平均持有月數", "下車時虧損 > 30% 的比例", "最差一筆")}
            u.log(f"毛利率條件 {name}: " + json.dumps(gm["網站規則加毛利率條件"][name], ensure_ascii=False))
        ex, ok = [], []
        for n, idx in enumerate(reb[:-3]):
            t, snap = records[idx]
            fut = [records[reb[n + j]][1] for j in range(3)]
            mk3 = 1.0
            for f in fut:
                mk3 *= 1 + ((f.get("__mkt") or {}).get("hold") or 0)
            for c, m in snap.items():
                if c.startswith("__") or (m.get("cap") or 0) < MIN_CAP or not base(m) or m.get("gm_yoy") is None:
                    continue
                g = 1.0
                for f in fut:
                    h = (f.get(c) or {}).get("hold")
                    if h is None:
                        g = None
                        break
                    g *= 1 + h
                if g is None:
                    continue
                (ex if m["gm_yoy"] < -5 else ok).append(g - mk3)
        st = lambda xs: {"筆數": len(xs), "平均超額": round(sum(xs) / len(xs) * 100, 1), "贏 0050 比例": round(sum(x > 0 for x in xs) / len(xs) * 100)} if xs else None
        gm["符合網站規則的股票，之後 3 個月相對 0050"] = {"毛利率年減 > 5 個百分點": st(ex), "其他": st(ok)}
        u.log("毛利率分組: " + json.dumps(gm["符合網站規則的股票，之後 3 個月相對 0050"], ensure_ascii=False))
        out["gross_margin"] = gm
    out["persistence"] = persistence(records, reb, ind_of, e, di)
    u.save("research3.json", out)


if __name__ == "__main__":
    main()
