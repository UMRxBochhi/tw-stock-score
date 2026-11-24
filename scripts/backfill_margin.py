"""
補每日融資資料（2006 起），給融資維持率研究用（scripts/research_margin.py）。
由 .github/workflows/backfill_margin.yml 執行；可重複執行、會跳過已有的日期，一輪跑不完會自己再觸發下一輪。
上市、上櫃各用一個執行緒同時抓（兩個網站各自限速）。

- data/hist/mgd_YYYYMM.json.gz   上市：{日期: {代號: [融資餘額(張), 次一營業日限額(張)], "__amt": 上市融資金額(仟元), "__bal": 上市融資餘額(張)}}
- data/hist/mgdo_YYYYMM.json.gz  上櫃：{日期: {代號: [融資餘額(張), 融資限額(張)]}}
交易日以 data/hist/px_*.json.gz 裡有的日期為準。
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MAX_RWD_CALLS", "8000")
os.environ.setdefault("MAX_TPEX_CALLS", "8000")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as u      # noqa: E402

HIST = u.DATA / "hist"
START = os.environ.get("MARGIN_START", "20060101")
TIME_BUDGET = float(os.environ.get("BACKFILL_MINUTES", "160")) * 60
T0 = time.time()
STATE = HIST / "backfill_margin_state.json"
st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
S = {k: set(st.get(k, [])) for k in ("nodata", "nodata_otc")}
LOCK = threading.Lock()
FETCHED = {"mgd": 0, "mgdo": 0}
ERR = {"mgd": 0, "mgdo": 0}


def save_state():
    with LOCK:
        STATE.write_text(json.dumps({k: sorted(v) for k, v in S.items()}), encoding="utf-8")


def out_of_time():
    return time.time() - T0 > TIME_BUDGET


margin_twse = u.fetch_margin           # 抓取函式放在 update.py（每日更新也用）
margin_tpex = u.fetch_margin_otc


def worker(prefix, days, fetch, nd_key, min_n):
    cache_ym, rows = None, None
    n = 0
    for t in days:
        if out_of_time() or ERR[prefix] >= 30:
            break
        ym = t[:6]
        if ym != cache_ym:
            cache_ym, rows = ym, u.hist_read(prefix, ym)
        if t in rows or t in S[nd_key]:
            continue
        res = fetch(t)
        if res is None:
            ERR[prefix] += 1
            u.log(f"  {prefix} {t} error，暫停 60 秒")
            time.sleep(60)
            continue
        if len([c for c in res if not c.startswith("__")]) < min_n:
            with LOCK:
                S[nd_key].add(t)
            continue
        rows[t] = res
        u.hist_write(prefix, ym, rows)
        n += 1
        with LOCK:
            FETCHED[prefix] += 1
        if n % 50 == 0:
            u.log(f"  {prefix} {t}（本輪 {n} 天，{(time.time() - T0) / 60:.0f} 分）")
            save_state()


def main():
    days = sorted({t for ym in u.hist_months("px") for t in u.hist_read("px", ym) if t >= START})
    otc_days = [t for t in days if t >= "20070701"]          # 上櫃股價從 2007 年中才有
    u.log(f"交易日 {len(days)}（{days[0]}~{days[-1]}）")
    th = [threading.Thread(target=worker, args=("mgd", days, margin_twse, "nodata", 100)),
          threading.Thread(target=worker, args=("mgdo", otc_days, margin_tpex, "nodata_otc", 50))]
    for x in th:
        x.start()
    for x in th:
        x.join()
    save_state()
    have = {p: {t for ym in u.hist_months(p) for t in u.hist_read(p, ym)} for p in ("mgd", "mgdo")}
    miss = {"mgd": [t for t in days if t not in have["mgd"] and t not in S["nodata"]],
            "mgdo": [t for t in otc_days if t not in have["mgdo"] and t not in S["nodata_otc"]]}
    done = not miss["mgd"] and not miss["mgdo"]
    u.save("probe_backfill_margin.json", {"rwd_calls": u.rwd_calls, "tpex_calls": u.tpex_calls, "errors": u.probe["errors"][-20:],
                                          "minutes": round((time.time() - T0) / 60, 1), "fetched": FETCHED, "done": done,
                                          "have": {p: len(v) for p, v in have.items()}, "missing": {p: len(v) for p, v in miss.items()},
                                          "nodata": {k: len(v) for k, v in S.items()}})
    if done:
        (HIST / "backfill_margin_done.json").write_text(json.dumps({"at": datetime.now(u.TW).isoformat()}))
    elif sum(FETCHED.values()) == 0:
        (HIST / "backfill_margin_stalled.json").write_text(json.dumps({"at": datetime.now(u.TW).isoformat(),
                                                                      "errors": u.probe["errors"][-10:]}, ensure_ascii=False))
    u.log(f"完成={done}，本輪 {FETCHED}，缺 {({p: len(v) for p, v in miss.items()})}，{(time.time() - T0) / 60:.1f} 分鐘")


if __name__ == "__main__":
    main()
