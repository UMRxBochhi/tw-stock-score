# 大跌專題：0050 距一年高點跌超過 15% 時，用什麼條件挑股，之後 12 個月最容易贏 0050
"""
- 大跌事件：0050 收盤距近 250 日高點 ≤ −15% 開始；回到距高點 −5% 以內才算結束（避免同一次大跌重複計算）
- 進場點：每次事件「第一次跌破 −15% 那天」，以及事件期間每月 11 日後第一個交易日（且仍在 −15% 以下）
- 選股範圍：市值 ≥ 100 億（發行股數 × 股價）、近 20 日平均成交值 ≥ 3000 萬
- 報酬：價格報酬（不含股利，0050 同樣不含），持有 250 個交易日；單日漲跌超過 ±15%（分割/減資）當天視為 0；
  中途下市或停止交易就以最後價格計（不事先排除，避免偷看未來）
- 評估單位是「事件」：同一次大跌裡的多個進場點先平均，再看幾次事件裡有效
輸出 data/research_crash.json
"""
import gzip
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402

HIST = u.DATA / "hist"
H = 250
DD_ON, DD_OFF = -0.15, -0.05
MIN_CAP = 1e10


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


def pct_rank(d):
    xs = {c: v for c, v in d.items() if v is not None}
    s = sorted(xs.values())
    n = len(s)
    if n < 2:
        return {c: 0.5 for c in xs}
    import bisect
    return {c: (bisect.bisect_left(s, v) + bisect.bisect_right(s, v) - 1) / 2 / (n - 1) for c, v in xs.items()}


def spearman(a, b):
    cs = [c for c in a if a[c] is not None and b.get(c) is not None]
    if len(cs) < 20:
        return None
    ra, rb = pct_rank({c: a[c] for c in cs}), pct_rank({c: b[c] for c in cs})
    ma, mb = sum(ra.values()) / len(cs), sum(rb.values()) / len(cs)
    cov = sum((ra[c] - ma) * (rb[c] - mb) for c in cs)
    va = math.sqrt(sum((ra[c] - ma) ** 2 for c in cs))
    vb = math.sqrt(sum((rb[c] - mb) ** 2 for c in cs))
    return cov / (va * vb) if va and vb else None


def main():
    # ---- 股價：每檔一條序列（收盤、成交值）
    months = u.hist_months("px")
    dates, close, val = [], {}, {}
    for ym in months:
        d = u.hist_read("px", ym)
        for t in sorted(d):
            i = len(dates)
            dates.append(t)
            for c, v in d[t].items():
                if not (u.is_stock(c) or c == "0050") or not v or not v[0]:
                    continue
                if c not in close:
                    close[c] = {}
                    val[c] = {}
                close[c][i] = v[0]
                val[c][i] = v[2] if len(v) > 2 else None
    N = len(dates)
    di = {t: i for i, t in enumerate(dates)}
    u.log(f"交易日 {N}（{dates[0]}~{dates[-1]}），股票 {len(close)}")

    def series(c, i0, i1):
        """i0..i1 的收盤（缺值往前補），回傳 list"""
        s, last = [], None
        cc = close.get(c, {})
        for i in range(i0, i1 + 1):
            last = cc.get(i, last)
            s.append(last)
        return s

    def chain_ret(c, i0, i1):
        """i0→i1 報酬：單日 ±15% 以上視為公司行為（當天報酬 0）；停止交易就用最後價格"""
        cc = close.get(c, {})
        if i0 not in cc:
            return None
        g, last = 1.0, cc[i0]
        for i in range(i0 + 1, i1 + 1):
            v = cc.get(i)
            if not v:
                continue
            r = v / last
            if 0.85 <= r <= 1.15:
                g *= r
            last = v
        return g - 1

    e = series("0050", 0, N - 1)
    for i in range(1, N):            # 0050 分割還原
        if e[i] and e[i - 1] and e[i] / e[i - 1] < 0.5:
            k = e[i - 1] / e[i]
            for j in range(i):
                e[j] = e[j] / k if e[j] else e[j]
    ret50 = lambda i0, i1: e[i1] / e[i0] - 1

    # ---- 大跌事件
    dd = []
    for i in range(N):
        w = [x for x in e[max(0, i - 249):i + 1] if x]
        dd.append(e[i] / max(w) - 1)
    episodes, cur = [], None
    for i in range(N):
        if cur is None and dd[i] <= DD_ON:
            pk = max(range(max(0, i - 249), i + 1), key=lambda j: e[j])
            cur = {"start": i, "peak": pk, "entries": [i], "low": i}
        elif cur is not None:
            if e[i] < e[cur["low"]]:
                cur["low"] = i
            if dd[i] >= DD_OFF:
                cur["end"] = i
                episodes.append(cur)
                cur = None
                continue
            t = dates[i]
            first_after_11 = int(t[6:]) >= 11 and (dates[i - 1][:6] != t[:6] or int(dates[i - 1][6:]) < 11)
            if first_after_11 and dd[i] <= DD_ON and i not in cur["entries"]:
                    cur["entries"].append(i)
    if cur is not None:
        cur["end"] = None
        episodes.append(cur)
    for ep in episodes:
        ep["name"] = dates[ep["start"]][:6]
        ep["entries"] = [i for i in ep["entries"] if i + H < N]
    episodes = [ep for ep in episodes if ep["entries"]]
    u.log("大跌事件：" + "; ".join(f"{ep['name']} 高點 {dates[ep['peak']]} 觸發 {dates[ep['start']]} 低點 {dates[ep['low']]}"
                                f"（{dd[ep['low']] * 100:.0f}%）進場點 {len(ep['entries'])}" for ep in episodes))

    # ---- 其他資料
    qf_months = u.hist_months("qf")
    qf = {}
    for ym in qf_months:
        qf.update(u.hist_read("qf", ym))
    qf_dates = sorted(qf)
    mg = {}
    for ym in u.hist_months("mg"):
        mg.update(u.hist_read("mg", ym))
    mg_dates = sorted(mg)
    revh = json.loads((HIST / "rev.json").read_text(encoding="utf-8"))
    for code, h in u.load("fundamentals.json", {}).get("rev_hist", {}).items():
        revh.setdefault(code, {}).update(h)
    pe_m = u.load("pe_monthly.json", {})
    pe_keys = sorted(pe_m)
    rev_now = u.load("fundamentals.json", {}).get("revenue", {})
    incp = HIST / "inc.json.gz"
    incdb = json.loads(gzip.decompress(incp.read_bytes())) if incp.exists() else {}
    IND = {}

    def ind_of(c):
        if c not in IND:
            IND[c] = u.industry(c, rev_now) or "?"
        return IND[c]

    def last_le(keys, t):
        import bisect
        j = bisect.bisect_right(keys, t) - 1
        return keys[j] if j >= 0 else None

    def factor(h, rk):
        cur = [h.get(shift(rk, i), [None])[0] for i in range(12)]
        x = est12(h, rk)
        return x / sum(cur) if x and None not in cur and sum(cur) > 0 else None

    def q_margin(c, k):
        h = incdb.get(c, {})
        v = h.get(k)
        if not v or not v[0]:
            return None
        if k.endswith("Q1"):
            q = v
        else:
            y, qq = int(k[:-2]), int(k[-1])
            pv = h.get(f"{y}Q{qq - 1}")
            if not pv:
                return None
            q = [a - b if a is not None and b is not None else None for a, b in zip(v, pv)]
        if not q[0] or q[0] <= 0:
            return None
        return (q[1] / q[0] * 100 if q[1] is not None else None, q[2] / q[0] * 100 if q[2] is not None else None)

    def avail_q(t):
        y, mo = int(t[:4]) - 1911, int(t[4:6])
        return f"{y}Q3" if mo >= 12 else f"{y}Q2" if mo >= 9 else f"{y}Q1" if mo >= 6 else f"{y - 1}Q4" if mo >= 4 else f"{y - 1}Q3"

    def zlog(v, xs):
        lx = [math.log(x) for x in xs]
        mu = sum(lx) / len(lx)
        sd = (sum((a - mu) ** 2 for a in lx) / len(lx)) ** 0.5
        return (math.log(v) - mu) / sd if sd > 0 else None

    # ---- 每個進場點的特徵與之後 12 個月報酬
    def snapshot(i, ep):
        t = dates[i]
        qk = last_le(qf_dates, t)
        qd = qf.get(qk, {}) if qk else {}
        qk0 = last_le(qf_dates, dates[ep["peak"]])
        qd0 = qf.get(qk0, {}) if qk0 else {}
        mk = last_le(mg_dates, t)
        mgd = mg.get(mk, {}) if mk else {}
        rk = avail_rev_key(t)
        pk = [k for k in pe_keys if k < t[:6]][-60:]
        k0 = avail_q(t)
        y0 = int(k0[:-2])
        k4 = f"{y0 - 1}{k0[-2:]}"
        m50_crash = ret50(ep["peak"], i)
        snap = {}
        for c, cc in close.items():
            if c == "0050" or i not in cc:
                continue
            p = cc[i]
            vals = [val[c].get(j) for j in range(max(0, i - 19), i + 1)]
            vals = [x for x in vals if x]
            if len(vals) < 15 or sum(vals) / len(vals) < u.MIN_AVG_VALUE:
                continue
            q = qd.get(c)
            if not q or len(q) < 2 or not q[1]:
                continue
            cap = q[1] * p
            if cap < MIN_CAP:
                continue
            m = {"cap": cap, "qf": q[0]}
            q0 = qd0.get(c)
            if q0 and q0[0] is not None and q[0] is not None:
                m["qf_chg"] = q[0] - q0[0]             # 大跌期間外資持股變化（百分點）
            # 價格面
            s = series(c, max(0, i - 260), i)
            s = [x for x in s if x]
            if len(s) < 120:
                continue
            m["dd_own"] = p / max(s[-250:]) - 1         # 自己距一年高點
            rc = chain_ret(c, ep["peak"], i)
            if rc is not None:
                m["rel_crash"] = rc - m50_crash          # 大跌期間相對 0050（> 0 = 比大盤抗跌）
            r12 = chain_ret(c, max(0, i - 250), i)
            if r12 is not None:
                m["mom12"] = r12
            # 大跌前一年的波動與 beta（用高點之前的資料）
            j1 = ep["peak"]
            a = [close[c].get(j) for j in range(max(1, j1 - 249), j1 + 1)]
            bb = [e[j] for j in range(max(1, j1 - 249), j1 + 1)]
            ra, rm = [], []
            for x in range(1, len(a)):
                if a[x] and a[x - 1] and bb[x] and bb[x - 1] and 0.85 <= a[x] / a[x - 1] <= 1.15:
                    ra.append(a[x] / a[x - 1] - 1)
                    rm.append(bb[x] / bb[x - 1] - 1)
            if len(ra) >= 120:
                mu_a, mu_m = sum(ra) / len(ra), sum(rm) / len(rm)
                cv = sum((x - mu_a) * (y - mu_m) for x, y in zip(ra, rm))
                vm = sum((y - mu_m) ** 2 for y in rm)
                m["beta"] = cv / vm if vm else None
                m["vol"] = (sum((x - mu_a) ** 2 for x in ra) / len(ra)) ** 0.5 * math.sqrt(250)
            # 融資使用率
            x = mgd.get(c)
            if x and x[0] is not None and x[1]:
                m["fin_use"] = x[0] / x[1]
            # 營收
            h = revh.get(c, {})
            ests = [est12(h, shift(rk, j)) for j in range(13)]
            if ests[0]:
                n_up = 0
                for j in range(12):
                    if ests[j] and ests[j + 1] and ests[j] > ests[j + 1]:
                        n_up += 1
                    else:
                        break
                m["rev_up_n"] = n_up
            if ests[0] and ests[1]:
                m["rev_rev1"] = ests[0] / ests[1] - 1
            cur12 = [h.get(shift(rk, k), [None])[0] for k in range(12)]
            for yrs in (2, 3):
                old = [h.get(shift(rk, k + 12 * yrs), [None])[0] for k in range(12)]
                if None not in cur12 + old and sum(old) > 0 and sum(cur12) > 0:
                    m[f"cagr{yrs}"] = (sum(cur12) / sum(old)) ** (1 / yrs) - 1
            r3 = [h.get(shift(rk, k), [None])[0] for k in range(3)]
            p3 = [h.get(shift(rk, k + 12), [None])[0] for k in range(3)]
            if None not in r3 + p3 and sum(p3) > 0:
                m["rev3"] = sum(r3) / sum(p3) - 1
            prev = [h.get(shift(rk, k), [None])[0] for k in range(1, 13)]
            prev = [v for v in prev if v]
            if h.get(rk) and h[rk][0] and len(prev) >= 10:
                m["rev_hi"] = h[rk][0] / max(prev) - 1
            # 本益比位階（傳統／前瞻，歷史至少 18 個月）
            s0 = pe_m[pk[-1]].get(c) if pk else None
            if s0 and s0[0] and s0[1] and s0[0] > 0:
                pe_now = s0[0] * p / s0[1]
                m["pe"] = pe_now
                hist = [(k, (pe_m[k].get(c) or [None])[0]) for k in pk]
                hist = [(k, v) for k, v in hist if v and v > 0]
                if len(hist) >= 18:
                    m["pe_zt"] = zlog(pe_now, [v for _, v in hist])
                    fn = factor(h, rk)
                    if fn and fn > 0 and ind_of(c) not in u.FWD_SKIP_IND:
                        fh = []
                        for k, v in hist:
                            f = factor(h, avail_rev_key(k + "28"))
                            if f and f > 0:
                                fh.append(v / f)
                        if len(fh) >= 18:
                            m["pe_zf"] = zlog(pe_now / fn, fh)
                            m["fpe"] = pe_now / fn
            # 毛利率／營益率
            a0, a4 = q_margin(c, k0), q_margin(c, k4)
            if a0 and a0[0] is not None:
                m["gm"] = a0[0]
                if a4 and a4[0] is not None:
                    m["gm_yoy"] = a0[0] - a4[0]
            if a0 and a0[1] is not None:
                m["om"] = a0[1]
            m["tech"] = 1 if any(k in ind_of(c) for k in ("半導體", "電子", "電腦", "通信", "光電", "資訊")) else 0
            # 之後 12 個月
            r = chain_ret(c, i, i + H)
            if r is None:
                continue
            m["fwd"] = r
            m["ex"] = r - ret50(i, i + H)
            snap[c] = m
        return snap

    import os
    import pickle
    cache = os.environ.get("CRASH_CACHE")
    if cache and Path(cache).exists():
        snaps = pickle.loads(Path(cache).read_bytes())
    else:
        snaps = {i: snapshot(i, ep) for ep in episodes for i in ep["entries"]}
        if cache:
            Path(cache).write_bytes(pickle.dumps(snaps))
    for ep in episodes:
        for i in ep["entries"]:
            u.log(f"  {dates[i]}（{ep['name']}）距高點 {dd[i] * 100:.0f}%：{len(snaps[i])} 檔，"
                  f"之後 12 個月 0050 {ret50(i, i + H) * 100:+.0f}%")
    out = {"說明": __doc__.strip(), "事件": [], "特徵": {}, "規則": {}}
    for ep in episodes:
        out["事件"].append({"事件": ep["name"], "0050 高點": dates[ep["peak"]], "觸發": dates[ep["start"]],
                          "最低點": dates[ep["low"]], "最深跌幅": round(dd[ep["low"]] * 100, 1),
                          "進場點": [dates[i] for i in ep["entries"]],
                          "第一次進場後 12 個月 0050": round(ret50(ep["start"], ep["start"] + H) * 100, 1) if ep["start"] + H < N else None,
                          "選股範圍檔數": len(snaps[ep["entries"][0]])})

    # ---- 1. 單一特徵：每個進場點的 IC、最高 1/3 減最低 1/3 的 12 個月報酬；同事件先平均
    FEATS = {"qf": "外資持股比例", "qf_chg": "大跌期間外資持股變化", "cagr3": "近 3 年營收年成長", "cagr2": "近 2 年營收年成長",
             "rev_up_n": "營收預估連續上修月數", "rev_rev1": "營收預估本月修正幅度", "rev3": "近 3 月營收年增",
             "rev_hi": "月營收距一年高點", "cap": "市值", "pe_zf": "前瞻本益比位階（σ）", "pe_zt": "傳統本益比位階（σ）",
             "pe": "本益比", "fpe": "前瞻本益比",
             "dd_own": "自己距一年高點（越負跌越深）", "rel_crash": "大跌期間相對 0050（越高越抗跌）", "mom12": "近 12 個月漲幅",
             "beta": "大跌前 beta", "vol": "大跌前波動度", "fin_use": "融資使用率", "gm": "毛利率", "gm_yoy": "毛利率年變化",
             "om": "營益率", "tech": "電子科技股"}
    for k, lab in FEATS.items():
        per_ep = {}
        for ep in episodes:
            ics, sp, cov = [], [], []
            for i in ep["entries"]:
                s = snaps[i]
                f = {c: m.get(k) for c, m in s.items() if m.get(k) is not None}
                cov.append(len(f))
                if len(f) < 30:
                    continue
                v = spearman(f, {c: s[c]["ex"] for c in f})
                if v is not None:
                    ics.append(v)
                cs = sorted(f, key=lambda c: f[c])
                n3 = len(cs) // 3
                if k == "tech":
                    hi_ = [c for c in cs if f[c] == 1]
                    lo_ = [c for c in cs if f[c] == 0]
                else:
                    hi_, lo_ = cs[-n3:], cs[:n3]
                if hi_ and lo_:
                    sp.append(sum(s[c]["ex"] for c in hi_) / len(hi_) - sum(s[c]["ex"] for c in lo_) / len(lo_))
            if ics:
                per_ep[ep["name"]] = {"IC": round(sum(ics) / len(ics) * 100, 1),
                                      "高組減低組": round(sum(sp) / len(sp) * 100, 1) if sp else None, "檔數": min(cov)}
        if per_ep:
            sps = [v["高組減低組"] for v in per_ep.values() if v["高組減低組"] is not None]
            out["特徵"][lab] = {"事件數": len(per_ep), "高組勝出的事件數": f"{sum(x > 0 for x in sps)}/{len(sps)}",
                              "平均 IC": round(sum(v["IC"] for v in per_ep.values()) / len(per_ep), 1),
                              "平均高組減低組": round(sum(sps) / len(sps), 1) if sps else None, "各事件": per_ep}

    # ---- 2. 選股規則：篩選 + 排序取前 10，等權；每個事件的進場點平均
    def rank_score(s, cands, W):
        rk = {k: pct_rank({c: (s[c].get(k.lstrip("-")) if s[c].get(k.lstrip("-")) is None else
                                s[c][k.lstrip("-")] * (-1 if k.startswith("-") else 1)) for c in cands}) for k in W}
        out_ = {}
        for c in cands:
            parts = [(rk[k].get(c), w) for k, w in W.items() if rk[k].get(c) is not None]
            tw = sum(w for _, w in parts)
            if tw >= 0.5 * sum(W.values()):
                out_[c] = sum(v * w for v, w in parts) / tw
        return out_

    up = lambda m, n=1: (m.get("rev_up_n") or 0) >= n
    zf_lt = lambda m, v: m.get("pe_zf") is not None and m["pe_zf"] < v
    SITE_W = {"qf": .4, "cagr3": .4, "cap": .2}
    RULES = {
        "舊版網站（～2026-11-18）：上修＋前瞻 < −0.5σ，外資40/3年成長40/市值20 排序": (lambda m: up(m) and zf_lt(m, -0.5), SITE_W),
        "上修＋前瞻 < −0.5σ，隨便排（市值）": (lambda m: up(m) and zf_lt(m, -0.5), {"cap": 1}),
        "不看本益比：上修，外資40/3年成長40/市值20": (lambda m: up(m), SITE_W),
        "不篩選：全部大型股，外資40/3年成長40/市值20": (lambda m: True, SITE_W),
        "只看外資持股最高": (lambda m: True, {"qf": 1}),
        "上修＋外資持股最高": (lambda m: up(m), {"qf": 1}),
        "上修＋3 年成長最高": (lambda m: up(m), {"cagr3": 1}),
        "上修＋外資50/3年成長50": (lambda m: up(m), {"qf": .5, "cagr3": .5}),
        "上修＋外資40/3年成長40/抗跌20": (lambda m: up(m), {"qf": .4, "cagr3": .4, "rel_crash": .2}),
        "上修＋外資40/3年成長40/跌最深20": (lambda m: up(m), {"qf": .4, "cagr3": .4, "-rel_crash": .2}),
        "上修＋外資持股≥30%＋3年成長≥10%，市值排序（＝平常選股名單）": (lambda m: up(m) and (m.get("qf") or 0) >= 30 and (m.get("cagr3") or -1) >= 0.10, {"cap": 1}),
        "市值前 10 大（接近 0050 前段）": (lambda m: True, {"cap": 1}),
        "大跌期間最抗跌前 10": (lambda m: True, {"rel_crash": 1}),
        "大跌期間跌最深前 10": (lambda m: True, {"-rel_crash": 1}),
        "低 beta 前 10": (lambda m: True, {"-beta": 1}),
        "高 beta 前 10": (lambda m: True, {"beta": 1}),
        "前瞻本益比最低 10 檔（純便宜）": (lambda m: True, {"-pe_zf": 1}),
        "外資逆勢加碼最多＋上修": (lambda m: up(m), {"qf_chg": 1}),
        "低融資使用率＋上修，外資40/3年成長40/市值20": (lambda m: up(m) and m.get("fin_use") is not None and m["fin_use"] < 0.2, SITE_W),
        "上修＋外資40/3年成長40/beta20": (lambda m: up(m), {"qf": .4, "cagr3": .4, "beta": .2}),
        "網站現行（2026-11-18 起）：上修，外資30/3年成長30/beta40": (lambda m: up(m), {"qf": .3, "cagr3": .3, "beta": .4}),
        "上修＋前瞻 < −0.5σ，外資40/3年成長40/beta20": (lambda m: up(m) and zf_lt(m, -0.5), {"qf": .4, "cagr3": .4, "beta": .2}),
        "上修＋電子科技股，外資40/3年成長40/市值20": (lambda m: up(m) and m.get("tech") == 1, SITE_W),
        "上修＋非電子，外資40/3年成長40/市值20": (lambda m: up(m) and m.get("tech") == 0, SITE_W),
        "上修＋外資≥30%＋3年成長≥10%，beta 排序": (lambda m: up(m) and (m.get("qf") or 0) >= 30 and (m.get("cagr3") or -1) >= 0.10, {"beta": 1}),
        "上修＋外資≥30%（不看成長），外資40/3年成長40/市值20": (lambda m: up(m) and (m.get("qf") or 0) >= 30, SITE_W),
        "營收創新高（距高點≥0）＋外資40/3年成長40/市值20": (lambda m: m.get("rev_hi") is not None and m["rev_hi"] >= 0, SITE_W),
        "高 beta＋上修": (lambda m: up(m), {"beta": 1}),
    }
    TOPN = 10
    rng = random.Random(7)

    def run_rule(filt, W, entries):
        rs = []
        for i in entries:
            s = snaps[i]
            cands = [c for c, m in s.items() if filt(m)]
            sc = rank_score(s, cands, W)
            pick = sorted(sc, key=lambda c: -sc[c])[:TOPN]
            if len(pick) < 3:
                continue
            rs.append({"t": dates[i], "ex": sum(s[c]["ex"] for c in pick) / len(pick),
                       "ret": sum(s[c]["fwd"] for c in pick) / len(pick), "n": len(pick), "n_cand": len(cands),
                       "hit": sum(s[c]["ex"] > 0 for c in pick) / len(pick), "pick": pick})
        return rs

    # 隨機基準：同一進場點，從全部大型股隨機挑 10 檔，2000 次
    rand_ep = {}
    for ep in episodes:
        vals = []
        for _ in range(2000):
            xs = []
            for i in ep["entries"]:
                cs = list(snaps[i])
                pick = rng.sample(cs, min(TOPN, len(cs)))
                xs.append(sum(snaps[i][c]["ex"] for c in pick) / len(pick))
            vals.append(sum(xs) / len(xs))
        vals.sort()
        rand_ep[ep["name"]] = vals
    out["隨機挑 10 檔大型股（各事件平均超額的中位數）"] = {k: round(v[len(v) // 2] * 100, 1) for k, v in rand_ep.items()}
    eqw = {}
    for ep in episodes:
        eqw[ep["name"]] = round(sum(sum(m["ex"] for m in snaps[i].values()) / len(snaps[i]) for i in ep["entries"]) / len(ep["entries"]) * 100, 1)
    out["全部大型股等權（各事件平均超額）"] = eqw

    for name, (filt, W) in RULES.items():
        per = {}
        first = {}
        for ep in episodes:
            rs = run_rule(filt, W, ep["entries"])
            if not rs:
                continue
            ex = sum(r["ex"] for r in rs) / len(rs)
            pctl = sum(v < ex for v in rand_ep[ep["name"]]) / len(rand_ep[ep["name"]]) * 100
            per[ep["name"]] = {"平均超額": round(ex * 100, 1), "平均報酬": round(sum(r["ret"] for r in rs) / len(rs) * 100, 1),
                               "贏 0050 的進場點": f"{sum(r['ex'] > 0 for r in rs)}/{len(rs)}",
                               "個股贏 0050 比例": round(sum(r["hit"] for r in rs) / len(rs) * 100),
                               "勝過隨機挑股的百分位": round(pctl), "平均候選檔數": round(sum(r["n_cand"] for r in rs) / len(rs))}
            r0 = rs[0]
            if r0["t"] == dates[ep["start"]]:
                first[ep["name"]] = {"超額": round(r0["ex"] * 100, 1), "名單": r0["pick"]}
        if not per:
            continue
        exs = [v["平均超額"] for v in per.values()]
        out["規則"][name] = {"贏 0050 的事件數": f"{sum(x > 0 for x in exs)}/{len(exs)}",
                            "事件平均超額": round(sum(exs) / len(exs), 1), "事件超額中位數": sorted(exs)[len(exs) // 2], "最差事件": min(exs),
                            "勝過隨機的事件數（>50 百分位）": f"{sum(v['勝過隨機挑股的百分位'] > 50 for v in per.values())}/{len(per)}",
                            "各事件": per, "第一次觸發當天": first}
        u.log(f"{name}: " + json.dumps({k: v for k, v in out['規則'][name].items() if k != '第一次觸發當天'}, ensure_ascii=False)[:600])

    # ---- 3. 留一事件驗證：用其他事件挑權重，套到被留下的事件
    GRID = []
    for wq in (0, .2, .4, .6):
        for wg in (0, .2, .4, .6):
            for wc in (0, .2):
                for wr in (0, .2, -.2):
                    for wb in (0, .2, .4):
                        if wq + wg + wc + abs(wr) + wb == 0:
                            continue
                        W = {"qf": wq, "cagr3": wg, "cap": wc, "beta": wb}
                        if wr:
                            W["rel_crash" if wr > 0 else "-rel_crash"] = abs(wr)
                        GRID.append({k: v for k, v in W.items() if v})
    for fname, filt in (("上修＋前瞻 < −0.5σ", lambda m: up(m) and zf_lt(m, -0.5)), ("上修", lambda m: up(m))):
        cache = {}
        for gi, W in enumerate(GRID):
            for ep in episodes:
                rs = run_rule(filt, W, ep["entries"])
                cache[(gi, ep["name"])] = sum(r["ex"] for r in rs) / len(rs) if rs else None
        loo = {}
        for ep in episodes:
            others = [x["name"] for x in episodes if x["name"] != ep["name"]]
            def tr(gi):
                v = sorted(cache[(gi, o)] for o in others if cache[(gi, o)] is not None)
                return (v[(len(v) - 1) // 2] + v[len(v) // 2]) / 2 if v else -9
            best = max(range(len(GRID)), key=tr)
            loo[ep["name"]] = {"用其他事件挑出的權重": GRID[best], "其他事件超額中位數": round(tr(best) * 100, 1),
                               "套到這次的超額": None if cache[(best, ep["name"])] is None else round(cache[(best, ep["name"])] * 100, 1)}
        vs = [v["套到這次的超額"] for v in loo.values() if v["套到這次的超額"] is not None]
        out[f"留一事件驗證｜{fname}"] = {"樣本外贏 0050 的事件數": f"{sum(x > 0 for x in vs)}/{len(vs)}",
                                    "樣本外平均超額": round(sum(vs) / len(vs), 1) if vs else None, "各事件": loo}
        u.log(f"留一驗證 {fname}: " + json.dumps(out[f'留一事件驗證｜{fname}'], ensure_ascii=False)[:1200])

    (u.DATA / "research_crash.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
