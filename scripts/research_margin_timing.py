# 大跌分批 × 大盤融資維持率 × 個股融資占股本（research_timing.py 的延伸）
"""
問題：1) 分批買進時，第 2、3 批用「大盤融資維持率 < 130%／125%」當加碼條件，會不會比只看跌幅好？
      2) 大跌名單先排除融資占股本 ≥ 6% 或 ≥ 10% 的股票，會不會更好？
輸出 data/research_margin_timing.json
以下沿用 research_timing.py 的設定：
- 大跌事件：0050（含股利）距近一年高點 ≤ −15% 那天＝觸發日；回到 −5% 以內才算結束。每個事件看觸發日起 18 個月
- 選股＝網站大跌名單：營收預估上修中、市值 ≥ 100 億，依 外資持股 30%／3 年營收成長 30%／大跌前一年 beta 40% 排序取前 N 檔
  （用上車當時最近一次月快照的資料，不偷看未來）
- A. 0050 本身的上車時機：還沒上車的錢放現金；看 18 個月後的報酬，以及上車後最深跌多少
- B. 大跌名單的上車＋下車：還沒上車的錢、下車後的錢都放 0050，每次換手扣 0.3%；
     和「觸發日買 0050 抱 18 個月」比，算超額；同時記錄整段路上的最大回撤
- 報酬含股利。2008~2017 的事件是規則定出來之前的資料
- 留一事件驗證：用其他事件挑出最好的組合，套到被留下的那次，看實際表現（比「全部事件裡最好的」誠實）
輸出 data/research_timing.json
"""
import bisect
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u              # noqa: E402
import research_winrate as W    # noqa: E402
import research_margin as RM     # noqa: E402

WIN = 375           # 18 個月
COST = 0.003
MIN_CAP = 1e10
OLD_END = "20180101"


def main():
    dates, close, val = W.load_prices()
    N = len(dates)
    G = {c: W.growth(a, dates, c) for c, a in close.items()}
    E = G["0050"]
    for i in range(1, N):
        if E[i] is None:
            E[i] = E[i - 1]
    u.log(f"交易日 {N}（{dates[0]}~{dates[-1]}）")

    bal, _otc, _amt, _tb, M = RM.load_margin(dates, close)
    u.log("維持率資料天數 %d" % sum(1 for x in M if x))
    DDa = [E[i] / max(E[max(0, i - 249):i + 1]) - 1 for i in range(N)]

    def dd(i):
        return DDa[i]

    def ma(i, n):
        return sum(E[i - n + 1:i + 1]) / n if i >= n - 1 else None

    # ---- 事件
    events, on = [], False
    for i in range(250, N):
        d_ = dd(i)
        if not on and d_ <= -0.15:
            on = True
            events.append(i)
        elif on and d_ >= -0.05:
            on = False
    months = [i for i, t in enumerate(dates)
              if int(t[6:]) >= 11 and (i == 0 or dates[i - 1][:6] != t[:6] or int(dates[i - 1][6:]) < 11)]
    need = sorted({m for t0 in events for m in months if t0 - 40 <= m <= t0 + WIN})
    snaps = W.build_snaps(dates, close, val, G, E, need)
    events = [t0 for t0 in events
              if len([c for c, m in (snaps.get(max([m for m in need if m <= t0] or [-1])) or {}).items()
                      if m.get("cap", 0) >= MIN_CAP]) >= 30]
    u.log("事件：" + ", ".join(dates[t] for t in events))

    bpre = {}

    def beta_pre(c, i):
        pk = max(range(max(0, i - 249), i + 1), key=lambda k: E[k])
        if (c, pk) not in bpre:
            gs, ra, rm = G[c], [], []
            for k in range(max(1, pk - 249), pk + 1):
                if gs[k] and gs[k - 1] and gs[k] != gs[k - 1]:
                    ra.append(gs[k] / gs[k - 1] - 1)
                    rm.append(E[k] / E[k - 1] - 1)
            b = None
            if len(ra) >= 120:
                a_, m_ = sum(ra) / len(ra), sum(rm) / len(rm)
                vm = sum((y - m_) ** 2 for y in rm)
                b = sum((x - a_) * (y - m_) for x, y in zip(ra, rm)) / vm if vm else None
            bpre[(c, pk)] = b
        return bpre[(c, pk)]

    def prank(d):
        xs = sorted(v for v in d.values() if v is not None)
        n = len(xs)
        return {c: (bisect.bisect_left(xs, v) + bisect.bisect_right(xs, v) - 1) / 2 / max(1, n - 1)
                for c, v in d.items() if v is not None}

    pick_cache = {}

    def crash_list(k, n, excl=None):
        """上車日 k 的大跌名單前 n 檔（用 k 之前最近一次月快照）"""
        j = max([m for m in need if m <= k] or [-1])
        if (j, k, n, excl) in pick_cache:
            return pick_cache[(j, k, n, excl)]
        s = snaps.get(j) or {}
        cands = [c for c, m in s.items() if m.get("cap", 0) >= MIN_CAP and (m.get("rev_up_n") or 0) >= 1 and G[c][k]]
        if excl is not None:
            def use(c):   # 融資占股本（%）：上車前一天的融資張數 × 1000 ÷ 股數（市值 ÷ 快照當天收盤）
                b = bal.get(c)
                if not b or not close[c][j] or b[k - 1] is None:
                    return 0.0
                return b[k - 1] * 1000 / (s[c]["cap"] / close[c][j]) * 100
            cands = [c for c in cands if use(c) < excl]
        feats = {"qf": {c: s[c].get("qf") for c in cands}, "cagr3": {c: s[c].get("cagr3") for c in cands},
                 "beta": {c: beta_pre(c, j) for c in cands}}
        Wt = {"qf": .3, "cagr3": .3, "beta": .4}
        rk = {f: prank(v) for f, v in feats.items()}
        sc = {}
        for c in cands:
            parts = [(rk[f][c], w) for f, w in Wt.items() if c in rk[f]]
            tw = sum(w for _, w in parts)
            if tw >= 0.5:
                sc[c] = sum(v * w for v, w in parts) / tw
        res = sorted(sc, key=lambda c: -sc[c])[:n]
        pick_cache[(j, k, n, excl)] = res
        return res

    # ---- 上車方式：回傳 [(上車日, 權重)]；等不到的部分權重留在等待資產
    def first(t0, end, f):
        return next((k for k in range(t0, end) if f(k)), None)

    def entry_plans(t0, end):
        lo = [E[t0]]
        for k in range(t0 + 1, end):
            lo.append(min(lo[-1], E[k]))
        reb = lambda x: first(t0, end, lambda k: E[k] >= lo[k - t0] * (1 + x))
        mlo = lambda x: first(t0, end, lambda k: M[k] is not None and M[k] < x)
        k20, k25 = first(t0, end, lambda k: dd(k) <= -0.20), first(t0, end, lambda k: dd(k) <= -0.25)
        k130, k125 = mlo(130), mlo(125)
        mn = lambda a, b: b if a is None else a if b is None else min(a, b)
        both = lambda lv, x: first(t0, end, lambda k: dd(k) <= -lv and M[k] is not None and M[k] < x)
        return {
            "觸發當天（跌 15%）": [(t0, 1.0)],
            "跌 15%／20%／25% 各買 1/3（網站現行）": [(t0, 1 / 3), (k20, 1 / 3), (k25, 1 / 3)],
            "15/20/25 各 1/3，但維持率跌破 130% 就把剩下的買完": [(t0, 1 / 3), (mn(k20, k130), 1 / 3), (mn(k25, k130), 1 / 3)],
            "跌 15% 買 1/3，維持率 < 130% 再買 1/3、< 125% 買最後 1/3": [(t0, 1 / 3), (k130, 1 / 3), (k125, 1 / 3)],
            "跌 15% 買 1/2，維持率 < 130% 買另 1/2": [(t0, 1 / 2), (k130, 1 / 2)],
            "第 2、3 批要「跌 20%/25%」且維持率 < 130%": [(t0, 1 / 3), (both(.20, 130), 1 / 3), (both(.25, 130), 1 / 3)],
            "全部等維持率 < 130% 才買": [(k130, 1.0)],
            "全部等維持率 < 125% 才買": [(k125, 1.0)],
        }

    # ---- 下車方式：rule(st) -> True 就把這檔換成 0050
    EXITS = {
        "0050 回到距高點 −5% 內全換（網站現行）": lambda st: st["dd"] >= -0.05,
        "抱 12 個月": lambda st: st["held"] >= 250,
    }

    def simulate(t0, end, plan, exit_rule, n, wait_in_0050=True, asset_0050=False, excl=None):
        """回傳 (期末財富, 路徑最大回撤, 實際上車比例)。初始財富 1"""
        path = [0.0] * (end - t0)
        used = 0.0
        for k, w in plan:
            if k is None or k >= end:
                continue
            used += w
        # 等待資產
        for x in range(end - t0):
            path[x] += (1 - used) * (E[t0 + x] / E[t0] if wait_in_0050 else 1.0)
        for k, w in plan:
            if k is None or k >= end:
                continue
            pre = E[k] / E[t0] if wait_in_0050 else 1.0
            for x in range(k - t0):
                path[x] += w * (E[t0 + x] / E[t0] if wait_in_0050 else 1.0)
            if asset_0050:
                for x in range(k - t0, end - t0):
                    path[x] += w * pre * E[t0 + x] / E[k]
                continue
            picks = crash_list(k, n, excl)
            if not picks:
                for x in range(k - t0, end - t0):
                    path[x] += w * pre * E[t0 + x] / E[k]
                continue
            each = w * pre * (1 - COST) / len(picks)
            for c in picks:
                g = G[c]
                hi, out_v, out_k = 1.0, None, None
                last = 1.0
                for x in range(k - t0, end - t0):
                    j = t0 + x
                    if out_v is None:
                        r = (g[j] / g[k]) if g[j] else last
                        last = r
                        hi = max(hi, r)
                        st = {"held": j - k, "r": r - 1, "dd": dd(j), "fromhi": r / hi - 1, "rel": r / (E[j] / E[k]) - 1}
                        path[x] += each * r
                        if j > k and exit_rule(st):
                            out_v, out_k = r * (1 - COST), j
                    else:
                        path[x] += each * out_v * E[j] / E[out_k]
        pk, mdd = 0.0, 0.0
        for v in path:
            pk = max(pk, v)
            mdd = min(mdd, v / pk - 1)
        return path[-1], mdd, used

    out = {"說明": __doc__.strip(), "期間": f"{dates[0]}~{dates[-1]}"}
    evinfo, done_ev = [], []
    for t0 in events:
        end = min(N, t0 + WIN)
        full = t0 + WIN <= N
        lo_k = min(range(t0, end), key=lambda k: E[k])
        evinfo.append({"觸發": dates[t0], "完整 18 個月": full, "0050 觸發前一年漲幅": round((E[t0] / E[t0 - 250] - 1) * 100, 1),
                       "觸發後最深再跌": round((E[lo_k] / E[t0] - 1) * 100, 1), "最低點": dates[lo_k],
                       "觸發日買 0050 抱 18 個月": round((E[end - 1] / E[t0] - 1) * 100, 1),
                       "觸發日維持率": round(M[t0]) if M[t0] else None,
                       "期間最低維持率": round(min(M[k] for k in range(t0, end) if M[k])) if any(M[k] for k in range(t0, end)) else None,
                       "跌破 130% 那天": next((dates[k] for k in range(t0, end) if M[k] and M[k] < 130), None)})
        if full:
            done_ev.append(t0)
    out["事件"] = evinfo
    u.log("事件資訊: " + json.dumps(evinfo, ensure_ascii=False))

    def summarize(rows):
        ex = [r["ex"] for r in rows]
        srt = sorted(ex)
        o = {"事件數": len(rows), "贏 0050 次數": sum(x > 0 for x in ex), "超額中位數": round(srt[len(srt) // 2], 1),
             "超額平均": round(sum(ex) / len(ex), 1), "最差": round(min(ex), 1),
             "最大回撤中位數": round(sorted(r["mdd"] for r in rows)[len(rows) // 2], 1), "最大回撤最深": round(min(r["mdd"] for r in rows), 1),
             "2008~17 贏/次數": f"{sum(r['ex'] > 0 for r in rows if r['t'] < OLD_END)}/{sum(1 for r in rows if r['t'] < OLD_END)}",
             "2018~ 贏/次數": f"{sum(r['ex'] > 0 for r in rows if r['t'] >= OLD_END)}/{sum(1 for r in rows if r['t'] >= OLD_END)}",
             "各事件": {r["t"][:6]: r["ex"] for r in rows}}
        return o

    # ---- A. 0050 本身的上車時機（沒上車的錢放現金）
    A = {}
    for t0 in done_ev:
        end = t0 + WIN
        base = E[end - 1] / E[t0]
        for pn, plan in entry_plans(t0, end).items():
            v, mdd, used = simulate(t0, end, plan, None, 0, wait_in_0050=False, asset_0050=True)
            A.setdefault(pn, []).append({"t": dates[t0], "ex": round((v / base - 1) * 100, 1), "mdd": round(mdd * 100, 1),
                                         "used": used, "abs": round((v - 1) * 100, 1)})
    out["A. 0050 上車時機（沒上車放現金；超額＝相對觸發日全買）"] = {
        pn: {**summarize(rs), "18 個月報酬中位數": sorted(r["abs"] for r in rs)[len(rs) // 2],
             "有等到的比例": round(sum(r["used"] for r in rs) / len(rs) * 100)} for pn, rs in A.items()}
    u.log("A: " + json.dumps({k: {kk: v[kk] for kk in ("贏 0050 次數", "超額中位數", "最差", "最大回撤中位數")}
                              for k, v in out["A. 0050 上車時機（沒上車放現金；超額＝相對觸發日全買）"].items()}, ensure_ascii=False))

    # ---- B. 大跌名單：上車 × 下車 × 檔數
    B = {}
    EXN = {None: "不排除", 10: "排除融資占股本 ≥ 10%", 6: "排除融資占股本 ≥ 6%"}
    for t0 in done_ev:
        end = t0 + WIN
        base = E[end - 1] / E[t0]
        plans = entry_plans(t0, end)
        for n in (10,):
            for pn, plan in plans.items():
                for en, rule in EXITS.items():
                  for ex_ in (None, 10, 6):
                    v, mdd, used = simulate(t0, end, plan, rule, n, excl=ex_)
                    B.setdefault((EXN[ex_], pn, en), []).append({"t": dates[t0], "ex": round((v / base - 1) * 100, 1), "mdd": round(mdd * 100, 1)})
        u.log(f"  {dates[t0]} 完成")
    # 0050 自己的回撤（比較用）
    m50 = []
    for t0 in done_ev:
        pk, mdd = 0, 0
        for k in range(t0, t0 + WIN):
            pk = max(pk, E[k])
            mdd = min(mdd, E[k] / pk - 1)
        m50.append(round(mdd * 100, 1))
    out["觸發日買 0050 的最大回撤"] = dict(zip([dates[t][:6] for t in done_ev], m50))

    table = [{"排除": n, "上車": pn, "下車": en, **summarize(rs)} for (n, pn, en), rs in B.items()]
    key = lambda x: (x["贏 0050 次數"], x["最差"], x["超額中位數"])
    out["B. 全部組合（依贏的次數→最差→中位數排序）"] = sorted(table, key=lambda x: tuple(-v for v in key(x)))
    out["B. 網站現行做法"] = next(x for x in table if x["排除"] == "不排除" and x["上車"].endswith("（網站現行）") and x["下車"].endswith("（網站現行）"))
    out["B. 回撤最小前 15（贏的次數 ≥ 全部事件 − 2）"] = sorted([x for x in table if x["贏 0050 次數"] >= len(done_ev) - 2],
                                                         key=lambda x: -x["最大回撤中位數"])[:15]
    # 各因素平均（看哪個維度影響大）
    for dim in ("排除", "上車", "下車"):
        g = {}
        for x in table:
            g.setdefault(x[dim], []).append(x)
        out[f"B. 依{dim}平均"] = {str(k): {"平均贏次數": round(sum(y["贏 0050 次數"] for y in v) / len(v), 2),
                                        "平均超額中位數": round(sum(y["超額中位數"] for y in v) / len(v), 1),
                                        "平均最差": round(sum(y["最差"] for y in v) / len(v), 1),
                                        "平均最大回撤中位數": round(sum(y["最大回撤中位數"] for y in v) / len(v), 1)} for k, v in g.items()}
        u.log(f"依{dim}: " + json.dumps(out[f"B. 依{dim}平均"], ensure_ascii=False))

    # ---- 留一事件驗證
    loo = []
    for t in done_ev:
        tn = dates[t]
        best, bk = None, None
        for kk, rs in B.items():
            others = [r for r in rs if r["t"] != tn]
            ex = sorted(r["ex"] for r in others)
            sc = (sum(x > 0 for x in ex), ex[0], ex[len(ex) // 2])
            if best is None or sc > best:
                best, bk = sc, kk
        held = next(r for r in B[bk] if r["t"] == tn)
        loo.append({"留下的事件": tn[:6], "用其他事件挑出的組合": f"{bk[0]}｜{bk[1]}｜{bk[2]}",
                    "其他事件 贏/最差/中位數": f"{best[0]}/{best[1]}/{best[2]}", "套到這次的超額": held["ex"], "這次最大回撤": held["mdd"]})
    out["留一事件驗證"] = {"樣本外贏 0050": f"{sum(x['套到這次的超額'] > 0 for x in loo)}/{len(loo)}",
                       "樣本外超額中位數": sorted(x["套到這次的超額"] for x in loo)[len(loo) // 2],
                       "樣本外最差": min(x["套到這次的超額"] for x in loo), "明細": loo}
    u.log("留一: " + json.dumps(out["留一事件驗證"], ensure_ascii=False))

    # ---- 現在這次（2026）：觸發以來的狀態
    if events and events[-1] not in done_ev:
        t0 = events[-1]
        out["進行中的事件"] = {"觸發": dates[t0], "已過交易日": N - 1 - t0, "0050 觸發以來": round((E[-1] / E[t0] - 1) * 100, 1),
                          "目前距高點": round(dd(N - 1) * 100, 1),
                          "大跌名單前 10（最新快照）": crash_list(N - 1, 10) if any(m <= N - 1 for m in need) else None}
    (u.DATA / "research_margin_timing.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    u.log("完成")


if __name__ == "__main__":
    main()
