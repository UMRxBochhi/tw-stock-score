"""
實驗：融資、融券、借券、外資持股比例 有沒有預測力（2018/07 起、每週取樣；等補歷史資料完成後手動觸發）
由 .github/workflows/chips.yml 執行（另一個排隊群組，不影響每日更新與補歷史資料）
輸出：data/exp_chips.json（結果＋各資料源欄位，除錯用）
"""
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MAX_RWD_CALLS", "3000")
os.environ.setdefault("MAX_TPEX_CALLS", "3000")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402
import backtest as b    # noqa: E402

START = os.environ.get("CHIPS_START", "20180701")
OUT = {"probe": {}, "errors": []}


def probe(key, js):
    if key in OUT["probe"]:
        return
    info = {"type": type(js).__name__}
    if isinstance(js, dict):
        info["keys"] = list(js)[:15]
        info["stat"] = js.get("stat")
        ts = js.get("tables") or []
        info["tables"] = [{"title": t.get("title"), "fields": t.get("fields"), "n": len(t.get("data") or []),
                           "sample": (t.get("data") or [])[:2]} for t in ts[:3] if isinstance(t, dict)]
        for k in js:
            if k.startswith("fields"):
                info[k] = js[k]
    OUT["probe"][key] = info


def ymd_slash(d):
    return f"{d[:4]}/{d[4:6]}/{d[6:]}"


# ---------------- 融資融券（張）：{代號: [融資餘額, 融資限額, 融券餘額]}
def margin_twse(d):
    js = u.get_json("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
                    {"date": d, "selectType": "ALL", "response": "json"}, rwd=True)
    if js is None:
        return None
    probe("twse_margin", js)
    t = u.find_table(js, ["代號", "今日餘額"])
    if not t:
        return {}
    f, rows = t
    bal = [i for i, x in enumerate(f) if "今日餘額" in x]
    lim = [i for i, x in enumerate(f) if "限額" in x]
    out = {}
    for r in rows:
        out[str(r[0]).strip()] = [u.num(r[bal[0]]), u.num(r[lim[0]]) if lim else None,
                                  u.num(r[bal[1]]) if len(bal) > 1 else None]
    return out


TPEX_MARGIN = ["margin/balance", "margin/marginBalance", "margin/margin"]


def margin_tpex(d):
    for path in list(TPEX_MARGIN):
        js = u.tpex_json(path, datetime.strptime(d, "%Y%m%d").date())
        if js is None:
            continue
        probe("tpex_margin:" + path, js)
        t = u.find_table(js, ["代號"])
        if not t:
            continue
        f, rows = t
        fs = [str(x) for x in f]
        ib = u.col(fs, "資餘額", exclude=("前",))
        il = u.col(fs, "資限額")
        isb = u.col(fs, "券餘額", exclude=("前",))
        if ib is None:
            continue
        TPEX_MARGIN[:] = [path]        # 找到能用的網址就固定
        return {str(r[0]).strip(): [u.num(r[ib]), u.num(r[il]) if il is not None else None,
                                    u.num(r[isb]) if isb is not None else None] for r in rows}
    return {}


# ---------------- 外資持股比例 %、發行股數：{代號: [比例, 發行股數]}
def qfii_twse(d):
    js = u.get_json("https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS",
                    {"date": d, "selectType": "ALLBUT0999", "response": "json"}, rwd=True)
    if js is None:
        return None
    probe("twse_qfii", js)
    t = u.find_table(js, ["證券代號", "持股比率"])
    if not t:
        return {}
    f, rows = t
    ir = u.col(f, "全體外資及陸資持股比率", "持股比率", exclude=("尚可", "上限"))
    isu = u.col(f, "發行股數")
    return {str(r[0]).strip(): [u.num(r[ir]), u.num(r[isu]) if isu is not None else None] for r in rows}


TPEX_QFII = ["insti/qfii", "insti/qfiiHolding", "insti/foreignHolding", "insti/qfiiStat"]


def qfii_tpex(d):
    for path in list(TPEX_QFII):
        js = u.tpex_json(path, datetime.strptime(d, "%Y%m%d").date())
        if js is None:
            continue
        probe("tpex_qfii:" + path, js)
        t = u.find_table(js, ["代號"])
        if not t:
            continue
        f, rows = t
        fs = [str(x) for x in f]
        ir = u.col(fs, "持股比率", "持股比例", exclude=("尚可", "上限"))
        isu = u.col(fs, "發行股數")
        ic = u.col(fs, "代號")          # 櫃買的表第一欄是「排行」，代號在第二欄
        if ir is None or ic is None:
            continue
        TPEX_QFII[:] = [path]
        return {str(r[ic]).strip(): [u.num(r[ir]), u.num(r[isu]) if isu is not None else None] for r in rows}
    return {}


# ---------------- 借券賣出餘額（股）：{代號: 餘額}
def sbl_twse(d):
    js = u.get_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U",
                    {"date": d, "response": "json"}, rwd=True)
    if js is None:
        return None
    probe("twse_sbl", js)
    t = u.find_table(js, ["代號"])
    if not t:
        return {}
    f, rows = t
    idx = [i for i, x in enumerate(f) if "餘額" in x and "前日" not in x]
    if not idx:
        return {}
    return {str(r[0]).strip(): u.num(r[idx[-1]]) for r in rows}


SOURCES = {"mg": (margin_twse, margin_tpex), "qf": (qfii_twse, qfii_tpex), "sbl": (sbl_twse, None)}


def fetch_all(dates):
    store = {k: {} for k in SOURCES}
    for k in SOURCES:
        for ym in sorted({d[:6] for d in dates}):
            store[k].update(u.hist_read(k, ym))
    for i, d in enumerate(dates):
        for k, (ftw, fto) in SOURCES.items():
            if d in store[k] and len(store[k][d]) > 1500 and (k != "qf" or "8299" in store[k][d] or d < "20200101"):
                continue
            row = {}
            a = ftw(d)
            if a is None:
                OUT["errors"].append(f"{k} {d} twse error")
                continue
            row.update(a)
            if fto:
                row.update(fto(d) or {})
            if row:
                store[k][d] = row
                ym = d[:6]
                u.hist_write(k, ym, {x: v for x, v in store[k].items() if x[:6] == ym})
        if i % 10 == 0:
            u.log(f"  {d}：融資 {len(store['mg'].get(d, {}))}、外資持股 {len(store['qf'].get(d, {}))}、借券 {len(store['sbl'].get(d, {}))}")
    return store


def main():
    records = b.build_records()
    records = [r for r in records if r[0] >= START]
    dates = [t for t, _ in records]
    u.log(f"實驗期間 {dates[0]}~{dates[-1]}，{len(dates)} 個時間點")
    st = fetch_all(dates)
    OUT["counts"] = {k: {d: len(v) for d, v in st[k].items()} for k in st}

    # 特徵
    for i, (t, snap) in enumerate(records):
        p4 = dates[i - 4] if i >= 4 else None
        mg, mg0 = st["mg"].get(t, {}), st["mg"].get(p4, {}) if p4 else {}
        qf, qf0 = st["qf"].get(t, {}), st["qf"].get(p4, {}) if p4 else {}
        sb, sb0 = st["sbl"].get(t, {}), st["sbl"].get(p4, {}) if p4 else {}
        for c, m in snap.items():
            x = mg.get(c)
            if x and x[0] is not None:
                if x[1]:
                    m["fin_use"] = x[0] / x[1]
                if m.get("avgval") and m.get("p"):
                    m["fin_days"] = x[0] * 1000 * m["p"] / m["avgval"]      # 融資餘額是幾天的成交金額
                if x[2] is not None and x[0] > 0:
                    m["ss_ratio"] = x[2] / x[0]                          # 券資比
                y = mg0.get(c)
                if y and y[0]:
                    m["fin_chg"] = x[0] / y[0] - 1
            q = qf.get(c)
            if q and q[0] is not None:
                m["qf"] = q[0]
                q0 = qf0.get(c)
                if q0 and q0[0] is not None:
                    m["qf_chg"] = q[0] - q0[0]
                if sb.get(c) is not None and q[1]:
                    m["sbl"] = sb[c] / q[1]
                    if sb0.get(c) is not None:
                        m["sbl_chg"] = (sb[c] - sb0[c]) / q[1]
            m["SCORE"] = u.strat_score(m["sc"], m, u.SCORE_W)

    feats = {"fin_use": "融資使用率", "fin_days": "融資餘額/日均量", "fin_chg": "融資 20 日增減",
             "ss_ratio": "券資比", "qf": "外資持股比例", "qf_chg": "外資持股比例 20 日變化",
             "sbl": "借券賣出餘額/股本", "sbl_chg": "借券賣出 20 日變化", "f20": "（對照）外資 20 日買超",
             "SCORE": "（對照）綜合分數"}
    nt = int(len(records) * 0.6)
    res = {}
    for k, name in feats.items():
        res[k] = {"name": name}
        for h, hz in (("short", 20), ("long", 60)):
            def ics(part):
                v = [b.spearman({c: m.get(k) for c, m in s.items()}, {c: m.get("ex_" + h) for c, m in s.items()}) for _, s in part]
                return [x for x in v if x is not None]
            a, tr, te = ics(records), ics(records[:nt]), ics(records[nt:])
            if len(a) < 10:
                continue
            ta, ttr, tte = b.nw_t(a, hz // 5), b.nw_t(tr, hz // 5), b.nw_t(te, hz // 5)
            res[k][hz] = {"ic": round(sum(a) / len(a) * 100, 2), "t": ta and round(ta, 2), "p": ta and round(b.p_two(ta), 4),
                          "t_train": ttr and round(ttr, 2), "t_test": tte and round(tte, 2), "n": len(a)}
    OUT["ic"] = res

    # 加進綜合分數（15%）是否提升驗證期 IC；當長線篩選條件（排除最差 20%）是否改善組合
    def ic_mean(part, key, h):
        v = [b.spearman({c: m.get(key) for c, m in s.items()}, {c: m.get("ex_" + h) for c, m in s.items()}) for _, s in part]
        v = [x for x in v if x is not None]
        return round(sum(v) / len(v) * 100, 2) if v else None
    inc = {}
    for k in [x for x in feats if not x.isupper() and x != "f20"]:
        tr = res[k].get(60, {}).get("t_train")
        if tr is None:
            continue
        sign = 1 if tr > 0 else -1
        for t, snap in records:
            rk = u.pct_rank({c: (m.get(k) * sign if m.get(k) is not None else None) for c, m in snap.items()})
            for c, m in snap.items():
                if m.get("SCORE") is not None:
                    m["S2_" + k] = m["SCORE"] * 0.85 + rk.get(c, 0.5) * 100 * 0.15
                m["R_" + k] = rk.get(c)
        test = records[nt:]
        base = []
        filt = []
        for t, snap in test:
            pool = [m for m in snap.values() if "long_core" in m.get("rules", []) and "fwd_long" in m]
            top = lambda ms: sorted(ms, key=lambda m: -(m.get("f20") if m.get("f20") is not None else -999))[:5]
            a = top(pool)
            f2 = top([m for m in pool if m.get("R_" + k) is None or m["R_" + k] > 0.2])
            if a:
                base.append(sum(m["fwd_long"] for m in a) / len(a))
            if f2:
                filt.append(sum(m["fwd_long"] for m in f2) / len(f2))
        pos = lambda xs: round(sum(x > 0 for x in xs) / len(xs) * 100, 1) if xs else None
        avg = lambda xs: round(sum(xs) / len(xs) * 100, 2) if xs else None
        inc[k] = {"sign": sign, "ic60_test_base": ic_mean(test, "SCORE", "long"), "ic60_test_new": ic_mean(test, "S2_" + k, "long"),
                  "ic20_test_base": ic_mean(test, "SCORE", "short"), "ic20_test_new": ic_mean(test, "S2_" + k, "short"),
                  "filter_long_test": {"base_pos": pos(base), "base_avg": avg(base), "base_worst": base and round(min(base) * 100, 2),
                                       "new_pos": pos(filt), "new_avg": avg(filt), "new_worst": filt and round(min(filt) * 100, 2)}}
    OUT["incremental"] = inc
    OUT["period"] = [dates[0], dates[-1]]
    OUT["finished"] = datetime.now(u.TW).isoformat()
    u.save("exp_chips.json", OUT)
    u.log(json.dumps({k: {h: v.get(h) for h in (20, 60)} for k, v in res.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
