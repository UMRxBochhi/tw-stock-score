"""
往前補 2006~2017 的歷史資料（驗證 2008 金融海嘯、2011 歐債、2015 股災等大跌，以及三檔組合規則）。
由 .github/workflows/backfill_old.yml 執行；可重複執行、會跳過已有的資料，一輪跑不完會自己再觸發下一輪。

- data/hist/px_YYYYMM.json.gz   每日收盤（上市＋上櫃），格式同 backfill.py
- data/hist/qf_YYYYMM.json.gz   外資持股比例＋發行股數：每月一天（11 日後第一個交易日，研究的每月快照用）
- data/hist/pe_old.json         本益比月取樣 {yyyymm: {代號: [本益比, 收盤]}}（2005/09 起；網站的 pe_monthly.json 只留 10 年，所以分開存）
- data/hist/rev.json            月營收往前補到 2004 年（算 3 年成長要多 3 年）
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("MAX_RWD_CALLS", "6000")
os.environ.setdefault("MAX_TPEX_CALLS", "6000")
os.environ.setdefault("BACKFILL_START", "2006-01-02")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402
import backfill as bf   # noqa: E402

HIST = u.DATA / "hist"
START = date.fromisoformat(os.environ["BACKFILL_START"])
END = date(2018, 1, 1)                     # 2018 之後 backfill.py 已補過
PE_START = (2005, 9)                       # 證交所本益比從 2005/09/02 起
TIME_BUDGET = float(os.environ.get("BACKFILL_MINUTES", "160")) * 60
T0 = time.time()
STATE = HIST / "backfill_old_state.json"
st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
for k in ("nodata", "nodata_otc", "otc_done", "qf_done", "pe_done", "pe_otc_done"):
    st.setdefault(k, [])
S = {k: set(v) for k, v in st.items() if isinstance(v, list)}
FETCHED = [0]


def save_state():
    STATE.write_text(json.dumps({k: sorted(v) for k, v in S.items()}, ensure_ascii=False), encoding="utf-8")


def out_of_time():
    return time.time() - T0 > TIME_BUDGET


def weekdays(a, b):
    d = a
    while d < b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def px_days():
    """每個交易日：上市收盤（MI_INDEX）＋同一天上櫃收盤"""
    cache = {}
    n = 0
    for d in weekdays(START, END):
        if out_of_time():
            return False
        k = u.ymd(d)
        ym = k[:6]
        if ym not in cache:
            cache = {ym: u.hist_read("px", ym)}
        rows = cache[ym]
        changed = False
        if k not in rows and k not in S["nodata"]:
            res, s = u.fetch_prices(d)
            if s == "ok" and len(res[0]) > 100:
                rows[k] = res[0]
                changed = True
            elif s == "error":
                u.log(f"  px {k} error，暫停 60 秒")
                time.sleep(60)
                continue
            else:
                S["nodata"].add(k)
        if k in rows and k not in S["otc_done"] and k not in S["nodata_otc"]:
            res, s = u.fetch_prices_otc(d)
            if s == "ok":
                rows[k].update(res[0])
                S["otc_done"].add(k)
                changed = True
            elif s == "nodata":
                S["nodata_otc"].add(k)
            else:
                u.log(f"  上櫃 px {k} error，暫停 60 秒")
                time.sleep(60)
        if changed:
            u.hist_write("px", ym, rows)
            FETCHED[0] += 1
            n += 1
            if n % 20 == 0:
                u.log(f"  px {k}（本輪 {n} 天，{(time.time() - T0) / 60:.0f} 分）")
                save_state()
    return True


def month_days(y, m):
    """11 日後的交易日（研究的每月快照日），往後找"""
    for day in range(11, 29):
        d = date(y, m, day)
        if d.weekday() < 5:
            yield d


def months(a, b):
    y, m = a
    while (y, m) < b:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def qf_months():
    """外資持股：每月一天（上市＋上櫃），用同一天的上市收盤代號判斷資料完整"""
    for y, m in months((START.year, START.month), (END.year, END.month)):
        ym = f"{y}{m:02d}"
        if ym in S["qf_done"]:
            continue
        if out_of_time():
            return False
        px = u.hist_read("px", ym)
        for d in month_days(y, m):
            k = u.ymd(d)
            if k not in px:
                continue
            u._TWSE_CODES[:] = [c for c in px[k] if c not in u.OTC_CODES]
            res = u.fetch_qfii(d)
            if res:
                cur = u.hist_read("qf", ym)
                cur[k] = res
                u.hist_write("qf", ym, cur)
                S["qf_done"].add(ym)
                FETCHED[0] += 1
                u.log(f"  qf {k} ok ({len(res)})")
                break
        else:
            if any(u.ymd(d) in px for d in month_days(y, m)):
                u.log(f"  qf {ym} 沒抓到")
            else:
                u.log(f"  qf {ym}：股價還沒補到，下輪再抓")
    return True


def pe_months():
    p = HIST / "pe_old.json"
    pe = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    last_save = time.time()
    for y, m in months(PE_START, (2016, 12)):
        key = f"{y}{m:02d}"
        if out_of_time():
            break
        if key not in S["pe_done"]:
            for day in (15, 16, 17, 18, 14, 13, 19, 20, 21, 22, 12, 11):
                d = date(y, m, day)
                if d.weekday() >= 5:
                    continue
                res, s = u.fetch_bwibbu(d)
                if s == "ok":
                    closes = {c: v[3] for c, v in res.items() if v[3]}
                    if len(closes) < 100:
                        pr, s2 = u.fetch_prices(d)
                        if s2 == "ok":
                            closes = {c: v[0] for c, v in pr[0].items()}
                    pe[key] = {c: [v[0], closes.get(c)] for c, v in res.items() if v[0]}
                    S["pe_done"].add(key)
                    FETCHED[0] += 1
                    u.log(f"  PE {key} ok ({len(pe[key])})")
                    break
                if s == "error":
                    break
        if key in pe and key not in S["pe_otc_done"]:
            for day in (15, 16, 17, 18, 14, 13, 19, 20, 21, 22, 12, 11):
                d = date(y, m, day)
                if d.weekday() >= 5:
                    continue
                res, s = u.fetch_bwibbu_otc(d)
                if s == "ok":
                    pr, s2 = u.fetch_prices_otc(d)
                    if s2 == "ok":
                        closes = {c: v[0] for c, v in pr[0].items()}
                        pe[key].update({c: [v[0], closes.get(c)] for c, v in res.items() if v[0]})
                        S["pe_otc_done"].add(key)
                        u.log(f"  上櫃 PE {key} ok ({len(res)})")
                    break
                if s == "error":
                    break
            else:
                S["pe_otc_done"].add(key)          # 整個月都沒有上櫃本益比：不再重試
        if time.time() - last_save > 300:
            p.write_text(json.dumps(pe, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            last_save = time.time()
    p.write_text(json.dumps(pe, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return not out_of_time()


def main():
    meta = u.load("meta.json", {})
    u.OTC_CODES.update(meta.get("otc_codes", []))
    done = True
    u.log("== 除權息／減資（2005 起）")
    u._safe(u.update_divs, "除權息")
    u.log("== 月營收（2004 起）")
    bf.TIME_BUDGET = TIME_BUDGET
    bf.T0 = T0
    bf.backfill_revenue()
    u.log("== 本益比月取樣（2005/09~2016/11）")
    done &= pe_months()
    save_state()
    u.log("== 每日收盤（上市＋上櫃）")
    done &= px_days()
    save_state()
    u.log("== 外資持股（每月一天）")
    done &= qf_months()
    save_state()
    u.save("probe_backfill_old.json", {"rwd_calls": u.rwd_calls, "tpex_calls": u.tpex_calls, "errors": u.probe["errors"][-20:],
                                       "minutes": round((time.time() - T0) / 60, 1), "fetched": FETCHED[0], "done": done,
                                       "px_days": len([1 for ym in u.hist_months("px") if ym < "201801"])})
    if done:
        (HIST / "backfill_old_done.json").write_text(json.dumps({"start": START.isoformat(), "at": datetime.now(u.TW).isoformat()}))
    elif FETCHED[0] == 0:
        (HIST / "backfill_old_stalled.json").write_text(json.dumps({"at": datetime.now(u.TW).isoformat(),
                                                                   "errors": u.probe["errors"][-10:]}, ensure_ascii=False))
    u.log(f"完成={done}，本輪 {FETCHED[0]} 筆，{u.rwd_calls} 次證交所請求、{u.tpex_calls} 次櫃買請求、{(time.time() - T0) / 60:.1f} 分鐘")


if __name__ == "__main__":
    main()
