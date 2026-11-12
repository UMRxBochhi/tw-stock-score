"""
一次性補歷史資料（回測用）。由 .github/workflows/backfill.yml 觸發，可重複執行、會跳過已有的資料。

data/hist/px_YYYYMM.json   每日收盤行情 {日期: {代號: [收盤, 成交股數, 成交金額, 最高, 最低]}}
data/hist/t86_YYYYMM.json  每日三大法人 {日期: {代號: [外資, 投信, 自營商]}}（股）
data/hist/rev.json         月營收 {代號: {民國年月: [營收, 年增率]}}
data/pe_monthly.json       本益比月取樣（由 update.py 的函式補到 7 年）
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("MAX_RWD_CALLS", "3000")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u  # noqa: E402

HIST = u.DATA / "hist"
START = date(2024, 4, 1)          # 回測從約 2024/10 開始（前面留 130 個交易日算支撐/均線）
REV_START = (2022, 1)             # 月營收從 2022/01（算 2023 起的近 12 月年增）
TIME_BUDGET = float(os.environ.get("BACKFILL_MINUTES", "150")) * 60
T0 = time.time()


def month_file(prefix, d):
    return HIST / f"{prefix}_{d[:6]}.json"


def load_month(prefix, ym):
    p = HIST / f"{prefix}_{ym}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_month(prefix, ym, obj):
    HIST.mkdir(parents=True, exist_ok=True)
    (HIST / f"{prefix}_{ym}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                                              encoding="utf-8")


def out_of_time():
    return time.time() - T0 > TIME_BUDGET


def backfill_daily(prefix, fetcher, nodata):
    today = datetime.now(u.TW).date()
    d = START
    months = {}
    while d < today and not out_of_time():
        if d.weekday() < 5:
            k = u.ymd(d)
            ym = k[:6]
            if ym not in months:
                months[ym] = load_month(prefix, ym)
            if k not in months[ym] and k not in nodata:
                res, st = fetcher(d)
                if st == "ok":
                    months[ym][k] = res[0] if prefix == "px" else res
                    u.log(f"  {prefix} {k} ok")
                    save_month(prefix, ym, months[ym])
                elif st == "nodata":
                    nodata.add(k)
                else:
                    u.log(f"  {prefix} {k} error，暫停 60 秒")
                    time.sleep(60)
        d += timedelta(days=1)


def backfill_revenue():
    p = HIST / "rev.json"
    rev = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    have = {k for h in rev.values() for k in h}
    today = datetime.now(u.TW).date()
    y, m = REV_START
    while (y, m) < (today.year, today.month) and not out_of_time():
        key = f"{y - 1911}{m:02d}"
        last_month = (y, m) == ((today.replace(day=1) - timedelta(days=1)).year,
                                (today.replace(day=1) - timedelta(days=1)).month)
        if key not in have and not (last_month and today.day < 12):
            res = u.fetch_mops_revenue(y - 1911, m)
            if res:
                for code, v in res.items():
                    rev.setdefault(code, {})[key] = v
                u.log(f"  rev {key} ok ({len(res)})")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    HIST.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rev, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    nd_path = HIST / "nodata.json"
    nodata = set(json.loads(nd_path.read_text())) if nd_path.exists() else set()
    u.log("== 月營收")
    backfill_revenue()
    u.log("== 本益比月取樣（7 年）")
    pe = u.load("pe_monthly.json", {})
    u.update_pe_monthly(pe)
    u.save("pe_monthly.json", pe)
    u.log("== 收盤行情")
    backfill_daily("px", u.fetch_prices, nodata)
    u.log("== 三大法人")
    backfill_daily("t86", u.fetch_t86, nodata)
    HIST.mkdir(parents=True, exist_ok=True)
    nd_path.write_text(json.dumps(sorted(nodata)))
    u.save("probe_backfill.json", {"rwd_calls": u.rwd_calls, "errors": u.probe["errors"][-20:],
                                   "minutes": round((time.time() - T0) / 60, 1),
                                   "finished_all": not out_of_time()})
    u.log(f"完成，用了 {u.rwd_calls} 次請求、{(time.time() - T0) / 60:.1f} 分鐘")


if __name__ == "__main__":
    main()
