# 融資維持率研究：大盤融資維持率、個股融資維持率（推估）能不能當買點／避開訊號
"""
資料：data/hist/mgd_*（上市每日融資餘額＋融資金額）、mgdo_*（上櫃每日融資餘額），2006 起（scripts/backfill_margin.py）
- 大盤融資維持率（上市）＝ Σ 個股融資餘額 × 收盤價 ÷ 上市融資金額。分子只算股票＋0050，其他 ETF 的融資沒算進分子，數字會略低於新聞報的值
- 個股融資維持率（推估）：融資餘額增加的那天，用當天價格算進融資平均成本；減少時成本不變；餘額歸零就重算。
  維持率 ＝ 價格 ÷（平均成本 × 融資成數），上市成數 60%、上櫃 50%。價格用含股利還原的指數，所以除權息不會讓維持率掉下去（實際上會）
- 報酬都含股利；個股看相對 0050 的超額；「2008~17」是規則定出來之前的資料，可當獨立驗證
輸出 data/research_margin.json
"""
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u              # noqa: E402
import research_winrate as W    # noqa: E402

OLD_END = "20180101"
MIN_CAP = 1e10
H = {"1 個月": 21, "3 個月": 63, "6 個月": 125, "12 個月": 250}


def pct(xs):
    return round(sum(xs) / len(xs) * 100, 1) if xs else None


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))] * 100, 1) if xs else None


def stat(rs, ds=None):
    """rs：報酬比（1.05 = +5%）。ds：對應日期，用來分 2008~17 / 2018~"""
    if not rs:
        return None
    o = {"樣本": len(rs), "勝率": pct([r > 1 for r in rs]), "中位數": q([r - 1 for r in rs], 0.5),
         "平均": round((sum(rs) / len(rs) - 1) * 100, 1), "最差 10%": q([r - 1 for r in rs], 0.1)}
    if ds:
        a = [r for r, d in zip(rs, ds) if d < OLD_END]
        b = [r for r, d in zip(rs, ds) if d >= OLD_END]
        o["2008~17 勝率/中位數/樣本"] = f"{pct([r > 1 for r in a])} / {q([r - 1 for r in a], 0.5)} / {len(a)}"
        o["2018~ 勝率/中位數/樣本"] = f"{pct([r > 1 for r in b])} / {q([r - 1 for r in b], 0.5)} / {len(b)}"
    return o


def main():
    if not (u.DATA / "hist" / "backfill_margin_done.json").exists() and not os.environ.get("MARGIN_FORCE"):
        u.log("融資資料還沒補完（沒有 backfill_margin_done.json），先不跑")
        return
    dates, close, val = W.load_prices()
    N = len(dates)
    idx = {t: i for i, t in enumerate(dates)}
    G = {c: W.growth(a, dates, c) for c, a in close.items()}
    E = G["0050"]
    for i in range(1, N):
        if E[i] is None:
            E[i] = E[i - 1]
    u.log(f"交易日 {N}（{dates[0]}~{dates[-1]}）")

    # ---- 每日融資
    bal = {}            # 代號 -> [張數 or None]*N
    otc = set()
    amt = [None] * N    # 上市融資金額（仟元）
    tbal = [None] * N   # 上市融資餘額（張）
    for pre in ("mgd", "mgdo"):
        for ym in u.hist_months(pre):
            for t, rows in u.hist_read(pre, ym).items():
                i = idx.get(t)
                if i is None:
                    continue
                for c, v in rows.items():
                    if c == "__amt":
                        amt[i] = v
                    elif c == "__bal":
                        tbal[i] = v
                    elif isinstance(v, list) and v and v[0] is not None:
                        bal.setdefault(c, [None] * N)[i] = v[0]
                        if pre == "mgdo":
                            otc.add(c)
    u.log(f"有融資資料的股票 {len(bal)}，上市融資金額天數 {sum(1 for x in amt if x)}")

    # ---- 1. 大盤融資維持率
    M = [None] * N
    for i in range(N):
        if not amt[i]:
            continue
        mv = sum(b[i] * 1000 * close[c][i] for c, b in bal.items()
                 if c not in otc and b[i] and c in close and close[c][i])
        M[i] = mv / (amt[i] * 1000) * 100 if amt[i] > 0 else None
    out = {"說明": __doc__.strip(), "期間": f"{dates[0]}~{dates[-1]}"}
    out["大盤維持率每年"] = {}
    for y in sorted({t[:4] for t in dates}):
        xs = [M[i] for i in range(N) if dates[i][:4] == y and M[i]]
        if xs:
            out["大盤維持率每年"][y] = {"最低": round(min(xs)), "中位數": round(sorted(xs)[len(xs) // 2]), "最高": round(max(xs)),
                                   "最低那天": dates[min((i for i in range(N) if dates[i][:4] == y and M[i]), key=lambda i: M[i])]}
    u.log("大盤維持率每年: " + json.dumps(out["大盤維持率每年"], ensure_ascii=False))

    def fwd50(i, h):
        return E[i + h] / E[i] if i + h < N and E[i] else None

    def dd_hi(i):
        w = [x for x in E[max(0, i - 249):i + 1] if x]
        return E[i] / max(w) - 1

    def ma120(i):
        return sum(E[i - 119:i + 1]) / 120 if i >= 119 else None

    BK = [(0, 130, "< 130%"), (130, 140, "130~140%"), (140, 150, "140~150%"), (150, 160, "150~160%"),
          (160, 170, "160~170%"), (170, 999, "≥ 170%")]
    lvl = {}
    for lo, hi, name in BK:
        row = {}
        for hn, h in H.items():
            ii = [i for i in range(N) if M[i] and lo <= M[i] < hi and i + h < N]
            row[hn] = stat([fwd50(i, h) for i in ii], [dates[i] for i in ii])
        row["天數"] = sum(1 for i in range(N) if M[i] and lo <= M[i] < hi)
        lvl[name] = row
    allr = {hn: stat([fwd50(i, h) for i in range(N) if M[i] and i + h < N], [dates[i] for i in range(N) if M[i] and i + h < N])
            for hn, h in H.items()}
    out["大盤維持率位置 → 0050 之後報酬（每天都算，樣本重疊）"] = {"全部日子": allr, **lvl}

    # 跌破門檻事件：第一次跌破 X，回到 X+15 以上才算下一次
    ev = {}
    for X in (125, 130, 135, 140, 145, 150):
        arm, xs = True, []
        for i in range(N):
            if M[i] is None:
                continue
            if arm and M[i] < X:
                arm = False
                xs.append(i)
            elif not arm and M[i] >= X + 15:
                arm = True
        ev[f"跌破 {X}%"] = [{"日期": dates[i], "維持率": round(M[i]), "0050 距一年高點": round(dd_hi(i) * 100, 1),
                           **{f"0050 之後 {hn}": None if fwd50(i, h) is None else round((fwd50(i, h) - 1) * 100, 1) for hn, h in H.items()},
                           "之後最低維持率": round(min(M[k] for k in range(i, min(N, i + 120)) if M[k]))} for i in xs]
    out["跌破門檻事件（買 0050）"] = ev
    u.log("跌破 140: " + json.dumps(ev["跌破 140%"], ensure_ascii=False))
    # 和現有規則比：0050 距高點 ≤ −15%（大跌名單啟用）那天 vs 再等維持率跌破 X
    crash = []
    on = False
    for i in range(N):
        d_ = dd_hi(i)
        if not on and d_ <= -0.15:
            on = True
            nxt = {X: next((k for k in range(i, min(N, i + 250)) if M[k] and M[k] < X), None) for X in (130, 140, 150)}
            crash.append({"0050 跌 15% 那天": dates[i], "維持率": round(M[i]) if M[i] else None,
                          "0050 之後 12 個月": None if fwd50(i, 250) is None else round((fwd50(i, 250) - 1) * 100, 1),
                          **{f"等跌破 {X}%": None if k is None else
                             {"日期": dates[k], "0050 從跌 15% 那天到這天": round((E[k] / E[i] - 1) * 100, 1),
                              "之後 12 個月": None if fwd50(k, 250) is None else round((fwd50(k, 250) - 1) * 100, 1)}
                             for X, k in nxt.items()}})
        elif on and d_ >= -0.05:
            on = False
    out["0050 跌 15% 後要不要等維持率"] = crash
    # 半年線下（三檔組合啟用時）× 大盤維持率
    sub = {}
    for name, f in (("半年線下＋維持率 < 150%", lambda i: M[i] < 150), ("半年線下＋維持率 150~165%", lambda i: 150 <= M[i] < 165),
                    ("半年線下＋維持率 ≥ 165%", lambda i: M[i] >= 165)):
        ii = [i for i in range(N) if M[i] and ma120(i) and E[i] < ma120(i) and f(i)]
        sub[name] = {hn: stat([fwd50(i, h) for i in ii if i + h < N], [dates[i] for i in ii if i + h < N]) for hn, h in H.items()}
        sub[name]["天數"] = len(ii)
    out["半年線下 × 大盤維持率 → 0050"] = sub

    # 大盤融資餘額 20 日變化（斷頭／散戶退場）
    chg = {}
    for lo, hi, name in ((-9, -0.15, "20 日減少 > 15%"), (-0.15, -0.05, "減少 5~15%"), (-0.05, 0.05, "±5%"), (0.05, 9, "增加 > 5%")):
        ii = [i for i in range(20, N) if tbal[i] and tbal[i - 20] and lo <= tbal[i] / tbal[i - 20] - 1 < hi]
        chg[name] = {hn: stat([fwd50(i, h) for i in ii if i + h < N], [dates[i] for i in ii if i + h < N]) for hn, h in ("3 個月", 63), ("12 個月", 250)}
        chg[name]["天數"] = len(ii)
    out["上市融資餘額 20 日變化 → 0050"] = chg

    # ---- 2. 個股融資維持率（推估）
    ratio, use = {}, {}
    for c, b in bal.items():
        g = G.get(c)
        if not g:
            continue
        f = 0.5 if c in otc else 0.6
        cost, last, r = None, None, [None] * N
        for i in range(N):
            if b[i] is None or not g[i]:
                continue
            if b[i] <= 0:
                cost, last = None, 0
                continue
            if cost is None or not last:
                cost = g[i]
            elif b[i] > last:
                cost = (cost * last + g[i] * (b[i] - last)) / b[i]
            last = b[i]
            r[i] = g[i] / (cost * f) * 100
        ratio[c] = r

    months = [i for i, t in enumerate(dates)
              if int(t[6:]) >= 11 and (i == 0 or dates[i - 1][:6] != t[:6] or int(dates[i - 1][6:]) < 11)]
    snaps = W.build_snaps(dates, close, val, G, E, months)
    site = lambda m: (m.get("rev_up_n") or 0) >= 1 and (m.get("qf") or 0) >= 30 and (m.get("cagr3") or -9) >= 0.10
    hi_ = lambda m: m.get("rev_hi") is not None and m["rev_hi"] >= 0

    def rel(c, i, h):
        g = G[c]
        if i + h >= N or not g[i] or g[i + h] is None:
            return None
        return (g[i + h] / g[i]) / (E[i + h] / E[i])

    obs = []
    for i in months:
        if i < 250:
            continue
        s = snaps.get(i) or {}
        for c, m in s.items():
            if m.get("cap", 0) < MIN_CAP or c not in bal:
                continue
            b = bal[c]
            r = ratio[c][i]
            if r is None or b[i] is None:
                continue
            shares = m["cap"] / close[c][i] / 1000 if close[c][i] else None
            b20 = b[i - 20] if i >= 20 else None
            b60 = b[i - 60] if i >= 60 else None
            p60 = G[c][i] / G[c][i - 60] - 1 if i >= 60 and G[c][i - 60] else None
            obs.append({"i": i, "c": c, "ratio": r, "use": b[i] / shares * 100 if shares else None,
                        "chg20": b[i] / b20 - 1 if b20 else None, "chg60": b[i] / b60 - 1 if b60 else None, "p60": p60,
                        "site": site(m), "trio": site(m) and hi_(m),
                        "below": bool(ma120(i) and E[i] < ma120(i)), "M": M[i],
                        **{hn: rel(c, i, h) for hn, h in H.items() if hn != "1 個月"}})
    u.log(f"個股觀察 {len(obs)}")
    out["個股觀察數"] = len(obs)

    def table(xs, key, buckets):
        res = {}
        for lo, hi, name in buckets:
            ys = [o for o in xs if o.get(key) is not None and lo <= o[key] < hi]
            res[name] = {hn: stat([o[hn] for o in ys if o.get(hn)], [dates[o["i"]] for o in ys if o.get(hn)]) for hn in ("3 個月", "6 個月", "12 個月")}
        return res

    RB = [(0, 130, "< 130%"), (130, 150, "130~150%"), (150, 170, "150~170%"), (170, 200, "170~200%"), (200, 9e9, "≥ 200%")]
    UB = [(0, 1, "融資占股本 < 1%"), (1, 3, "1~3%"), (3, 6, "3~6%"), (6, 10, "6~10%"), (10, 999, "≥ 10%")]
    CB = [(-9, -0.2, "60 日融資減少 > 20%"), (-0.2, 0, "減少 0~20%"), (0, 0.2, "增加 0~20%"), (0.2, 0.5, "增加 20~50%"), (0.5, 99, "增加 > 50%")]
    groups = {"全部大型股": obs, "網站選股條件": [o for o in obs if o["site"]],
              "三檔組合候選（網站條件＋創新高）": [o for o in obs if o["trio"]],
              "三檔組合候選＋0050 半年線下": [o for o in obs if o["trio"] and o["below"]]}
    ind = {}
    for gname, xs in groups.items():
        ind[gname] = {"樣本": len(xs),
                      "不分": {hn: stat([o[hn] for o in xs if o.get(hn)], [dates[o["i"]] for o in xs if o.get(hn)]) for hn in ("3 個月", "6 個月", "12 個月")},
                      "個股維持率（推估）": table(xs, "ratio", RB),
                      "融資占股本": table(xs, "use", UB),
                      "60 日融資增減": table(xs, "chg60", CB)}
        # 散戶追高：股價 60 日漲 > 20% 且融資增加 > 30%；融資退場：股價跌 > 15% 且融資減少 > 20%
        for name, f in (("股價漲 > 20%＋融資增 > 30%（散戶追高）", lambda o: o["p60"] > 0.2 and o["chg60"] > 0.3),
                        ("股價漲 > 20%＋融資減少（法人買、散戶賣）", lambda o: o["p60"] > 0.2 and o["chg60"] < 0),
                        ("股價跌 > 15%＋融資減 > 20%（融資退場）", lambda o: o["p60"] < -0.15 and o["chg60"] < -0.2),
                        ("股價跌 > 15%＋融資增加（散戶攤平）", lambda o: o["p60"] < -0.15 and o["chg60"] > 0)):
            ys = [o for o in xs if o["p60"] is not None and o["chg60"] is not None and f(o)]
            ind[gname][name] = {hn: stat([o[hn] for o in ys if o.get(hn)], [dates[o["i"]] for o in ys if o.get(hn)]) for hn in ("3 個月", "12 個月")}
        u.log(f"{gname}: " + json.dumps(ind[gname]["個股維持率（推估）"], ensure_ascii=False)[:1500])
    out["個股"] = ind

    # 每月橫切面：維持率最低 20% vs 最高 20%（避免不同年份水位不同）
    cs = {"最低 20%": [], "中間": [], "最高 20%": []}
    for i in months:
        xs = sorted([o for o in obs if o["i"] == i and o.get("12 個月")], key=lambda o: o["ratio"])
        if len(xs) < 20:
            continue
        k = len(xs) // 5
        cs["最低 20%"] += xs[:k]
        cs["中間"] += xs[k:-k]
        cs["最高 20%"] += xs[-k:]
    out["每月個股維持率排序（全部大型股）"] = {k: {hn: stat([o[hn] for o in v if o.get(hn)], [dates[o["i"]] for o in v if o.get(hn)])
                                         for hn in ("3 個月", "12 個月")} for k, v in cs.items()}
    (u.DATA / "research_margin.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    u.log("完成")


if __name__ == "__main__":
    main()
