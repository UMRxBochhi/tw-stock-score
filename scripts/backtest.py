"""
回測：在過去每週的時間點，只用「當時已公布」的資料算分數，再看之後的實際報酬。

- 短線分數看 20 個交易日後、長線分數看 60 個交易日後
- 報酬一律用「超額報酬」= 個股報酬 − 同期全部評分股票的平均報酬
- 前 60% 期間當訓練、後 40% 當驗證，找出有預測力的指標並校正權重

限制（會寫進報告）：ROE、殖利率沒有歷史資料，回測中不計；本益比用月取樣近似。
輸出：data/backtest.json（網頁用）
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u  # noqa: E402

HIST = u.DATA / "hist"
STEP = 5                 # 每 5 個交易日評估一次
HORIZON = {"short": 20, "long": 60}
LOOKBACK = 130
TRAIN_FRAC = 0.6
BANDS = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]


def load_hist(prefix):
    out = {}
    for p in sorted(HIST.glob(f"{prefix}_*.json")):
        out.update(json.loads(p.read_text(encoding="utf-8")))
    return out


def spearman(xs, ys):
    """xs, ys: {code: value}"""
    common = [c for c in xs if xs[c] is not None and ys.get(c) is not None]
    if len(common) < 30:
        return None
    rx = u.pct_rank({c: xs[c] for c in common})
    ry = u.pct_rank({c: ys[c] for c in common})
    mx = sum(rx.values()) / len(rx)
    my = sum(ry.values()) / len(ry)
    cov = sum((rx[c] - mx) * (ry[c] - my) for c in common)
    vx = math.sqrt(sum((rx[c] - mx) ** 2 for c in common))
    vy = math.sqrt(sum((ry[c] - my) ** 2 for c in common))
    return cov / (vx * vy) if vx and vy else None


def roc_shift(y, m, n):
    m -= n
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m


def avail_rev_key(t):
    """日期 t 時已公布的最新月營收（每月 10 日前公布上個月）"""
    y, m, d = int(t[:4]), int(t[4:6]), int(t[6:])
    y, m = roc_shift(y, m, 1 if d >= 11 else 2)
    return f"{y - 1911}{m:02d}"


def cum_yoy(h, last):
    y, m = int(last[:-2]), int(last[-2:])
    cur = [h.get(f"{y}{i:02d}", [None])[0] for i in range(1, m + 1)]
    prev = [h.get(f"{y - 1}{i:02d}", [None])[0] for i in range(1, m + 1)]
    if None in cur or None in prev or not sum(prev):
        return None
    return round((sum(cur) / sum(prev) - 1) * 100, 2)


def build_records():
    px = load_hist("px")
    px.update({d: {c: (v + [v[0], v[0]] if len(v) == 3 else v) for c, v in rows.items()}
               for d, rows in u.load("prices.json", {}).items()})
    t86 = load_hist("t86")
    t86.update(u.load("t86.json", {}))
    revh = json.loads((HIST / "rev.json").read_text(encoding="utf-8")) if (HIST / "rev.json").exists() else {}
    for code, h in u.load("fundamentals.json", {}).get("rev_hist", {}).items():
        revh.setdefault(code, {}).update(h)
    pe_m = u.load("pe_monthly.json", {})
    pe_keys = sorted(pe_m)
    rev_ind = {c: r.get("ind") for c, r in u.load("fundamentals.json", {}).get("revenue", {}).items()}

    dates = sorted(px)
    N = len(dates)
    codes = sorted({c for d in dates for c in px[d] if u.is_stock(c)})
    idx = {d: i for i, d in enumerate(dates)}
    rows = {c: [px[d].get(c) for d in dates] for c in codes}
    tdates = sorted(t86)
    u.log(f"交易日 {N}（{dates[0]}~{dates[-1]}），法人 {len(tdates)} 日，股票 {len(codes)}")

    eval_idx = list(range(LOOKBACK, N - HORIZON["short"], STEP))
    records = []   # 每個時間點：{code: {...}}
    for ti in eval_idx:
        t = dates[ti]
        rk = avail_rev_key(t)
        tds = [d for d in tdates if d <= t][-20:]
        pe_avail = [k for k in pe_keys if k < t[:6]]
        snap = {}
        for code in codes:
            r = rows[code][ti]
            if not r:
                continue
            ser = u.after_split([x for x in rows[code][ti - LOOKBACK + 1:ti + 1] if x])
            if len(ser) < 60:
                continue
            avg_val = sum(x[2] for x in ser[-20:]) / 20
            if avg_val < u.MIN_AVG_VALUE:
                continue
            p = r[0]
            m = {"p": p}
            closes = [x[0] for x in ser]
            m["b60"] = (p / (sum(closes[-60:]) / 60) - 1) * 100
            m["b20"] = (p / (sum(closes[-20:]) / 20) - 1) * 100
            vals = [x[2] for x in ser]
            m["vr"] = (sum(vals[-5:]) / 5) / (sum(vals[-60:]) / 60) if sum(vals[-60:]) else None  # 量能放大倍數
            m["lv"] = math.log10(avg_val)                                                      # 流動性（規模）
            m["avgval"] = avg_val
            m["r20"] = (p / closes[-21] - 1) * 100
            m["r60"] = (p / closes[-61] - 1) * 100 if len(closes) > 60 else None
            lv = u.support_levels(ser)
            m["sup_score"] = lv.get("sup_score")
            if len(tds) >= 10:
                vol = sum((px[d].get(code) or [0, 0])[1] for d in tds)
                if vol:
                    m["f20"] = sum(t86[d].get(code, [0, 0, 0])[0] for d in tds) / vol * 100
                    m["t20"] = sum(t86[d].get(code, [0, 0, 0])[1] for d in tds) / vol * 100
            h = {k: v for k, v in revh.get(code, {}).items() if k <= rk}
            if h:
                rm = u.revenue_metrics(h)
                m["accel"] = rm.get("accel")
                m["rev3"], m["rev12"], m["streak"] = rm.get("rev3"), rm.get("rev12"), rm.get("streak")
                m["cum_yoy"] = cum_yoy(h, max(h))
                gs = [x for x in (rm.get("rev3"), rm.get("rev12")) if x is not None]
            else:
                gs = []
            # 本益比：最近一次月取樣，依股價變化換算（EPS 視為不變）
            if pe_avail:
                s = pe_m[pe_avail[-1]].get(code)
                hist = [(pe_m[k].get(code) or [None])[0] for k in pe_avail[-u.PE_POS_MONTHS:]]
                hist = [x for x in hist if x]
                if s and s[0] and s[1] and len(hist) >= 24:
                    pe = s[0] * p / s[1]
                    pos = sum(1 for x in hist if x < pe) / len(hist) * 100
                    if gs and rev_ind.get(code) not in u.FWD_SKIP_IND:
                        g = max(-50.0, min(80.0, sum(gs) / len(gs)))
                        fpe = pe / (1 + g / 100)
                        if fpe > 0:
                            pos = sum(1 for x in hist if x < fpe) / len(hist) * 100
                    m["val_cheap"] = -pos
            # 未來報酬
            for name, hz in HORIZON.items():
                if ti + hz < N and rows[code][ti + hz]:
                    path = [x for x in rows[code][ti:ti + hz + 1] if x]
                    if len(u.after_split(path)) == len(path):   # 持有期間有分割/減資就不計
                        m["fwd_" + name] = rows[code][ti + hz][0] / p - 1
            snap[code] = m
        # 超額報酬：相對同期「中位數」（隨機挑股的勝率基準 = 50%）
        for name in HORIZON:
            fw = sorted(m["fwd_" + name] for m in snap.values() if "fwd_" + name in m)
            if len(fw) >= 30:
                med = fw[len(fw) // 2]
                for m in snap.values():
                    if "fwd_" + name in m:
                        m["ex_" + name] = m["fwd_" + name] - med
        # 分數（與上線版相同規則）
        keys = set(u.FUND_W) | set(u.CHIP_W) | set(u.TECH_W) | {"lv"}
        ranks = {k: u.pct_rank({c: m.get(k) for c, m in snap.items()}) for k in keys}
        for c, m in snap.items():
            m["rules"] = u.rule_hits(m, ranks["lv"].get(c), ranks["rev3"].get(c))
            sc = {
                "liq": ranks["lv"][c] * 100 if c in ranks["lv"] else None,
                "fund": u.combine(ranks, u.FUND_W, c),
                "chip": u.combine(ranks, u.CHIP_W, c),
                "tech": u.combine(ranks, u.TECH_W, c),
                "value": round(100 + m["val_cheap"], 1) if m.get("val_cheap") is not None else None,
                "sup": m.get("sup_score"),
            }
            m["sc"] = sc
        records.append((t, snap))
        u.log(f"  {t}：{len(snap)} 檔")
    return records


def main():
    records = build_records()


    n_train = int(len(records) * TRAIN_FRAC)
    report = {"period": [records[0][0], records[-1][0]] if records else None, "n_dates": len(records),
              "train_until": records[n_train - 1][0] if n_train else None,
              "notes": ["ROE、殖利率沒有歷史資料，回測中不計入", "本益比位階用月取樣依股價換算，屬近似",
                        "超額報酬 = 個股報酬 − 同期所有評分股票的中位數（隨機挑股勝率約 50%）"],
              "strats": {}, "ic": {}}

    comps = ["fund", "liq", "chip", "tech", "value", "sup"]
    raw = ["streak", "rev3", "cum_yoy", "accel", "f20", "t20", "b60", "vr", "lv", "sup_score", "val_cheap"]

    def ic_table(name, recs):
        out = {}
        for k in comps + raw:
            vals = []
            for _, snap in recs:
                xs = {c: (m["sc"].get(k) if k in comps else m.get(k)) for c, m in snap.items()}
                ys = {c: m.get("ex_" + name) for c, m in snap.items()}
                v = spearman(xs, ys)
                if v is not None:
                    vals.append(v)
            if vals:
                mean = sum(vals) / len(vals)
                sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1))
                out[k] = {"ic": round(mean, 4), "t": round(mean / sd * math.sqrt(len(vals)), 2) if sd else None,
                          "n": len(vals)}
        return out

    def evaluate(name, recs, wts):
        """各分數區間的勝率、平均超額報酬；前 20% vs 後 20%；綜合分數 IC"""
        bands = {f"{lo}-{hi if hi <= 100 else 100}": [] for lo, hi in BANDS}
        top, bot, ics = [], [], []
        for _, snap in recs:
            scored = {c: u.strat_score(m["sc"], m, wts) for c, m in snap.items()}
            ex = {c: m.get("ex_" + name) for c, m in snap.items()}
            for c, s in scored.items():
                if s is None or ex[c] is None:
                    continue
                for lo, hi in BANDS:
                    if lo <= s < hi:
                        bands[f"{lo}-{hi if hi <= 100 else 100}"].append(ex[c])
            ok = sorted([(s, ex[c]) for c, s in scored.items() if s is not None and ex[c] is not None])
            if len(ok) >= 50:
                q = len(ok) // 5
                bot.append(sum(e for _, e in ok[:q]) / q)
                top.append(sum(e for _, e in ok[-q:]) / q)
            v = spearman(scored, ex)
            if v is not None:
                ics.append(v)
        bstat = {}
        for b, xs in bands.items():
            if xs:
                bstat[b] = {"n": len(xs), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1),
                            "avg": round(sum(xs) / len(xs) * 100, 2)}
        return {"bands": bstat,
                "top20_avg": round(sum(top) / len(top) * 100, 2) if top else None,
                "bot20_avg": round(sum(bot) / len(bot) * 100, 2) if bot else None,
                "top20_win": round(sum(1 for x in top if x > 0) / len(top) * 100, 1) if top else None,
                "ic": round(sum(ics) / len(ics), 4) if ics else None}

    for name, hz in HORIZON.items():
        recs = [r for r in records if any("ex_" + name in m for m in r[1].values())]
        nt = int(len(recs) * TRAIN_FRAC)
        train, test = recs[:nt], recs[nt:]
        ic_all = ic_table(name, recs)
        ic_train = ic_table(name, train)
        report["ic"][name] = {"all": ic_all, "train": ic_train, "test": ic_table(name, test)}
        cur = u.STRATS[name]
        # 校正：訓練期 IC > 0 的面向依 IC 分配權重，與目前權重各半（避免過度擬合）
        pos = {k: max(0.0, ic_train.get(k, {}).get("ic", 0.0)) for k in comps}
        tot = sum(pos.values())
        if tot > 0:
            data_w = {k: v / tot for k, v in pos.items()}
            keys = set(cur) | {k for k, v in data_w.items() if v > 0}
            cal = {k: round(0.5 * cur.get(k, 0) + 0.5 * data_w.get(k, 0), 3) for k in keys}
            cal = {k: v for k, v in cal.items() if v >= 0.02}
        else:
            cal = dict(cur)
        res_cur_test = evaluate(name, test, cur)
        res_cal_test = evaluate(name, test, cal)
        report["strats"][name] = {
            "horizon": hz, "current_weights": cur, "calibrated_weights": cal,
            "current": {"all": evaluate(name, recs, cur), "test": res_cur_test},
            "calibrated": {"all": evaluate(name, recs, cal), "test": res_cal_test},
            "calibrated_better_on_test": (res_cal_test["ic"] or -9) > (res_cur_test["ic"] or -9),
        }
    # 策略選股規則：每檔被選中的股票是否贏過同期中位數
    report["rules"] = {}
    for rid, rule in u.RULES.items():
        name = rule["h"]
        recs = [r for r in records if any("ex_" + name in m for m in r[1].values())]
        nt = int(len(recs) * TRAIN_FRAC)
        out = {"h": u.STRATS and HORIZON[name], "t": rule["t"]}
        for lab, part in (("all", recs), ("train", recs[:nt]), ("test", recs[nt:])):
            ex = [m["ex_" + name] for _, s in part for m in s.values() if rid in m.get("rules", []) and "ex_" + name in m]
            raw_ret = [m["fwd_" + name] for _, s in part for m in s.values() if rid in m.get("rules", []) and "fwd_" + name in m]
            if ex:
                out[lab] = {"n_per": round(len(ex) / len(part), 1), "win": round(sum(1 for x in ex if x > 0) / len(ex) * 100, 1),
                            "avg_ex": round(sum(ex) / len(ex) * 100, 2),
                            "pos": round(sum(1 for x in raw_ret if x > 0) / len(raw_ret) * 100, 1),
                            "avg_ret": round(sum(raw_ret) / len(raw_ret) * 100, 2)}
        # 精選前 N 檔（外資排序）
        for lab, part in (("top_all", recs), ("top_train", recs[:nt]), ("top_test", recs[nt:])):
            picks, bex, bret = [], [], []
            for _, snap in part:
                codes = u.top_picks(list(snap.items()), rid)
                ms = [snap[c] for c in codes if "ex_" + name in snap[c]]
                if not ms:
                    continue
                picks += [m["ex_" + name] for m in ms]
                bex.append(sum(m["ex_" + name] for m in ms) / len(ms))
                bret.append(sum(m["fwd_" + name] for m in ms) / len(ms))
            if picks:
                out[lab] = {"n": rule.get("n"), "periods": len(bex),
                            "win": round(sum(1 for x in picks if x > 0) / len(picks) * 100, 1),
                            "basket_win": round(sum(1 for x in bex if x > 0) / len(bex) * 100, 1),
                            "basket_pos": round(sum(1 for x in bret if x > 0) / len(bret) * 100, 1),
                            "avg_ret": round(sum(bret) / len(bret) * 100, 2),
                            "worst": round(min(bret) * 100, 2)}
        # 自己挑：從候選中隨機挑 N 檔（重複 200 次）的組合表現
        import random
        rnd = random.Random(7)
        for lab, part in (("rand_train", recs[:nt]), ("rand_test", recs[nt:])):
            bex, bret = [], []
            for _ in range(200):
                for _, snap in part:
                    c = [m for m in snap.values() if rid in m.get("rules", []) and "ex_" + name in m]
                    if not c:
                        continue
                    p = rnd.sample(c, min(rule["n"], len(c)))
                    bex.append(sum(m["ex_" + name] for m in p) / len(p))
                    bret.append(sum(m["fwd_" + name] for m in p) / len(p))
            if bex:
                out[lab] = {"basket_win": round(sum(1 for x in bex if x > 0) / len(bex) * 100, 1),
                            "basket_pos": round(sum(1 for x in bret if x > 0) / len(bret) * 100, 1),
                            "avg_ret": round(sum(bret) / len(bret) * 100, 2)}
        report["rules"][rid] = out
    u.save("backtest.json", report)
    save_pool(records)


POOL_BANDS = [0, 30, 40, 50, 60, 70, 80, 90]


def band_of(v):
    b = 0
    for i, lo in enumerate(POOL_BANDS):
        if v >= lo:
            b = i
    return b


def save_pool(records):
    """自選股推估用：每個歷史時間點、每個分數區間，股票後來的 [超額報酬, 報酬]（×1000 取整數）"""
    pool = {"bands": POOL_BANDS, "h": HORIZON}
    for name in HORIZON:
        periods = []
        for _, snap in records:
            bins = [[] for _ in POOL_BANDS]
            for m in snap.values():
                if "ex_" + name not in m:
                    continue
                v = u.strat_score(m["sc"], m, u.STRATS[name])
                if v is None:
                    continue
                hit = 1 if (("long_core" if name == "long" else "short_core") in m.get("rules", [])) else 0
                bins[band_of(v)].append([round(m["ex_" + name] * 1000), round(m["fwd_" + name] * 1000), hit])
            if any(bins):
                periods.append(bins)
        pool[name] = periods
    u.save("pool.json", pool)
    u.log(json.dumps(report["strats"], ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
