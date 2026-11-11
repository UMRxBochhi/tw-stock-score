"""
台股評分資料更新程式（給 GitHub Actions 每天跑）

流程：
1. 從證交所抓資料（收盤行情、三大法人、本益比/殖利率、月營收、財報）
2. 歷史資料存在 data/*.json（取代資料庫，由 Actions commit 回 repo）
3. 計算基本面 / 籌碼面 / 技術面分數，輸出 site/data/latest.json 給網頁用

只用 Python 標準函式庫 + requests。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "data"
TW = timezone(timedelta(hours=8))

PRICE_KEEP = 130         # 保留幾個交易日的收盤價（支撐/大量區需要較長區間）
T86_KEEP = 25            # 保留幾個交易日的法人買賣超
SCORE_KEEP = 60          # 保留幾天的分數歷史
REV_MONTHS = 26          # 月營收歷史（算近 12 月年增需要 24 個月）
PE_MONTHS = 60           # 本益比位階用幾個月的月取樣（5 年）
MAX_RWD_CALLS = int(os.environ.get("MAX_RWD_CALLS", "320"))  # 每次執行最多打幾次 twse.com.tw
RWD_SLEEP = 3.2          # 證交所網站有頻率限制，每次間隔秒數
MIN_AVG_VALUE = 3e7      # 流動性門檻：20 日平均成交金額（元）

# 兩種策略：同樣的五個面向，權重不同
STRATS = {
    "short": {"chip": 0.30, "sup": 0.30, "tech": 0.25, "fund": 0.15},
    "long": {"value": 0.35, "fund": 0.40, "sup": 0.10, "chip": 0.10, "tech": 0.05},
}
VAL_W = {"pe_cheap": 0.70, "yld": 0.30}
# 只用少數核心指標計分，避免雜訊（其他數字仍會顯示在網頁上）
FUND_W = {"cum_yoy": 0.35, "accel": 0.25, "roe": 0.40}
CHIP_W = {"f20": 0.50, "t20": 0.50}
TECH_W = {"b60": 1.0}

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
})

probe: dict = {"started": datetime.now(TW).isoformat(), "calls": {}, "errors": []}
rwd_calls = 0


# ---------------------------------------------------------------- utils
def log(*a):
    print(*a, flush=True)


def load(name, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # 檔案壞掉就重來
            probe["errors"].append(f"load {name}: {e}")
    return default


def save(name, obj, path=None):
    path = path or DATA
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                             encoding="utf-8")


def num(v):
    """'1,234.50' -> 1234.5；'--'、''、None -> None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"<[^>]+>", "", str(v)).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "---", "N/A", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_stock(code: str) -> bool:
    """一般股票：4 碼數字、不是 0 開頭（排除 ETF）"""
    return bool(re.fullmatch(r"[1-9]\d{3}", code))


def record_probe(key, js):
    """把每個資料源的欄位結構記下來，方便除錯"""
    old = probe["calls"].get(key)
    if old and old.get("ok"):
        return
    info = {"type": type(js).__name__}
    if isinstance(js, dict):
        info["keys"] = list(js.keys())[:20]
        info["stat"] = js.get("stat")
        t = find_table(js, [])
        if t:
            info["fields"] = t[0]
            info["sample"] = t[1][:2]
            info["ok"] = js.get("stat") == "OK"
    elif isinstance(js, list):
        info["len"] = len(js)
        info["sample"] = js[:2]
        info["ok"] = len(js) > 0
    probe["calls"][key] = info


def get_json(url, params=None, rwd=False, tries=3):
    global rwd_calls
    for i in range(tries):
        if rwd:
            if rwd_calls >= MAX_RWD_CALLS:
                return None
            rwd_calls += 1
            time.sleep(RWD_SLEEP)
        try:
            r = S.get(url, params=params, timeout=40)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.json()
        except Exception as e:
            msg = f"{url} {params} -> {e}"
            log("  ! " + msg)
            if i == tries - 1:
                probe["errors"].append(msg[:300])
            time.sleep(5 * (i + 1))
    return None


def find_table(js, must):
    """證交所 JSON 格式有好幾種（tables[] / fields+data / fields9+data9），統一找出含指定欄位的表"""
    cands = []
    if isinstance(js, dict):
        if isinstance(js.get("tables"), list):
            for t in js["tables"]:
                if isinstance(t, dict) and t.get("fields") and t.get("data"):
                    cands.append((t["fields"], t["data"]))
        for k, v in js.items():
            if k.startswith("fields") and isinstance(v, list):
                d = js.get("data" + k[len("fields"):])
                if isinstance(d, list) and d:
                    cands.append((v, d))
    for fields, data in cands:
        if all(any(m in f for f in fields) for m in must):
            return fields, data
    return None


def col(fields, *keys, exclude=()):
    """找欄位 index：依序嘗試 keys（子字串比對）"""
    for k in keys:
        for i, f in enumerate(fields):
            if k in f and not any(x in f for x in exclude):
                return i
    return None


def weekdays_back(start: date, n_days: int):
    d = start
    for _ in range(n_days):
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------- fetchers
def fetch_prices(d: date):
    js = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                  {"date": ymd(d), "type": "ALLBUT0999", "response": "json"}, rwd=True)
    if js is None:
        return None, "error"
    record_probe("MI_INDEX", js)
    t = find_table(js, ["證券代號", "收盤價"])
    if not t:
        return None, "nodata"
    f, rows = t
    ic, inm = col(f, "證券代號"), col(f, "證券名稱")
    icl, ish, iv = col(f, "收盤價"), col(f, "成交股數"), col(f, "成交金額")
    ihi, ilo = col(f, "最高價"), col(f, "最低價")
    out, names = {}, {}
    for r in rows:
        code = str(r[ic]).strip()
        close = num(r[icl])
        if close is None:
            continue
        hi = num(r[ihi]) if ihi is not None else None
        lo = num(r[ilo]) if ilo is not None else None
        out[code] = [close, num(r[ish]) or 0, num(r[iv]) or 0, hi or close, lo or close]
        names[code] = str(r[inm]).strip()
    return (out, names), "ok"


def fetch_t86(d: date):
    js = get_json("https://www.twse.com.tw/rwd/zh/fund/T86",
                  {"date": ymd(d), "selectType": "ALLBUT0999", "response": "json"}, rwd=True)
    if js is None:
        return None, "error"
    record_probe("T86", js)
    t = find_table(js, ["證券代號", "投信買賣超"])
    if not t:
        return None, "nodata"
    f, rows = t
    ic = col(f, "證券代號")
    i_f = col(f, "外陸資買賣超股數(不含外資自營商)", "外陸資買賣超", "外資買賣超")
    i_t = col(f, "投信買賣超")
    i_d = col(f, "自營商買賣超股數", exclude=("自行", "避險"))
    out = {}
    for r in rows:
        code = str(r[ic]).strip()
        out[code] = [num(r[i_f]) or 0, num(r[i_t]) or 0,
                     (num(r[i_d]) or 0) if i_d is not None else 0]
    return out, "ok"


def fetch_bwibbu(d: date):
    """個股本益比、殖利率、股價淨值比"""
    js = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d",
                  {"date": ymd(d), "selectType": "ALL", "response": "json"}, rwd=True)
    if js is None:
        return None, "error"
    record_probe("BWIBBU_d", js)
    t = find_table(js, ["證券代號", "本益比"])
    if not t:
        return None, "nodata"
    f, rows = t
    ic, ipe, iy, ipb = col(f, "證券代號"), col(f, "本益比"), col(f, "殖利率"), col(f, "股價淨值比")
    icl = col(f, "收盤價")
    out = {}
    for r in rows:
        out[str(r[ic]).strip()] = [num(r[ipe]),
                                   num(r[iy]) if iy is not None else None,
                                   num(r[ipb]) if ipb is not None else None,
                                   num(r[icl]) if icl is not None else None]
    return out, "ok"


def roc_to_ymd(s):
    y, m, d = str(s).strip().split("/")
    return f"{int(y) + 1911}{int(m):02d}{int(d):02d}"


def update_index(idx: dict):
    today = datetime.now(TW).date()
    for i in range(4):
        yy, mm = today.year, today.month - i
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        if i >= 2 and any(k.startswith(f"{yy}{mm:02d}") for k in idx):
            continue
        js = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                      {"date": f"{yy}{mm:02d}01", "response": "json"}, rwd=True)
        if js is None:
            return
        record_probe("FMTQIK", js)
        t = find_table(js, ["日期", "加權"])
        if not t:
            continue
        f, rows = t
        idt, ic, ich = col(f, "日期"), col(f, "加權"), col(f, "漲跌")
        for r in rows:
            try:
                idx[roc_to_ymd(r[idt])] = [num(r[ic]), num(r[ich])]
            except Exception:
                pass
    for k in sorted(idx)[:-PRICE_KEEP]:
        del idx[k]


def fetch_mops_revenue(roc_y: int, m: int):
    """公開資訊觀測站：上市公司某月營收彙總（一次一個月、全部公司）"""
    url = f"https://mops.twse.com.tw/nas/t21/sii/t21sc03_{roc_y}_{m}_0.html"
    try:
        time.sleep(2)
        r = S.get(url, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        html = r.content.decode("cp950", errors="ignore")
    except Exception as e:
        probe["errors"].append(f"mops {roc_y}/{m}: {e}"[:200])
        return None
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        tds = [re.sub(r"<[^>]+>|&nbsp;", "", x).strip()
               for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(tds) >= 7 and re.fullmatch(r"\d{4}", tds[0]):
            out[tds[0]] = [num(tds[2]), num(tds[6])]
    probe["calls"].setdefault("mops", {"ok": bool(out), "n": len(out), "url": url})
    return out


def backfill_revenue(fund: dict):
    hist = fund.setdefault("rev_hist", {})
    have = {k for h in hist.values() for k in h}
    today = datetime.now(TW).date()
    fails = 0
    for i in range(1, REV_MONTHS + 2):
        yy, mm = today.year, today.month - i
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        key = f"{yy - 1911}{mm:02d}"
        if key in have:
            continue
        if i == 1 and today.day < 12:  # 上個月營收 10 號前才陸續公布
            continue
        res = fetch_mops_revenue(yy - 1911, mm)
        if not res:
            fails += 1
            if fails >= 2:
                return
            continue
        for code, v in res.items():
            hist.setdefault(code, {})[key] = v
        log(f"  月營收 {key} ok ({len(res)})")


def revenue_metrics(h: dict):
    """h = {'11509': [營收, 年增率], ...}（ROC 年月）"""
    def shift(k, n):
        y, m = int(k[:-2]), int(k[-2:]) - n
        while m <= 0:
            y, m = y - 1, m + 12
        return f"{y}{m:02d}"

    if not h:
        return {}
    last = max(h)
    out = {}

    def ratio(n):
        cur = [h.get(shift(last, i), [None])[0] for i in range(n)]
        prev = [h.get(shift(last, i + 12), [None])[0] for i in range(n)]
        if None in cur or None in prev or not sum(prev):
            return None
        return round((sum(cur) / sum(prev) - 1) * 100, 2)

    out["rev3"] = ratio(3)
    out["rev12"] = ratio(12)
    if out["rev3"] is not None and out["rev12"] is not None:
        out["accel"] = round(out["rev3"] - out["rev12"], 2)
    streak = 0
    k = last
    while k in h and h[k][1] is not None and h[k][1] > 0:
        streak += 1
        k = shift(k, 1)
    out["streak"] = streak
    # 全年推估：今年已公布 + 去年剩餘月份 × (1 + 近 3 月年增率)
    y, m = int(last[:-2]), int(last[-2:])
    ytd = [h.get(f"{y}{i:02d}", [None])[0] for i in range(1, m + 1)]
    ly_all = [h.get(f"{y - 1}{i:02d}", [None])[0] for i in range(1, 13)]
    if out["rev3"] is not None and None not in ytd and None not in ly_all and sum(ly_all):
        est = sum(ytd) + sum(ly_all[m:]) * (1 + out["rev3"] / 100)
        out["est_year"] = round(est)
        out["est_growth"] = round((est / sum(ly_all) - 1) * 100, 2)
        out["est_y"] = y
    return out


def openapi(path):
    js = get_json("https://openapi.twse.com.tw/v1/" + path)
    if isinstance(js, list):
        record_probe("openapi:" + path, js)
        return js
    return None


def pick(rec: dict, *keys, exclude=()):
    for k in keys:
        for rk, v in rec.items():
            if k in rk and not any(x in rk for x in exclude):
                return v
    return None


def fetch_fundamentals(old):
    fund = dict(old) if old else {}
    # 月營收
    rev = openapi("opendata/t187ap05_L")
    if rev:
        m = {}
        for r in rev:
            code = str(pick(r, "公司代號") or "").strip()
            if not code:
                continue
            m[code] = {
                "ym": pick(r, "資料年月"),
                "ind": pick(r, "產業別"),
                "rev": num(pick(r, "當月營收")),
                "rev_yoy": num(pick(r, "去年同月增減")),
                "rev_mom": num(pick(r, "上月比較增減")),
                "cum_yoy": num(pick(r, "前期比較增減")),
            }
        fund["revenue"] = m
        hist = fund.setdefault("rev_hist", {})
        for code, r in m.items():
            if r["ym"] and r["rev"] is not None:
                hist.setdefault(code, {})[str(r["ym"])] = [r["rev"], r["rev_yoy"]]
    # 綜合損益表（各產業別格式）
    inc = {}
    for suf in ["ci", "basi", "bd", "fh", "ins", "mim"]:
        rows = openapi(f"opendata/t187ap06_L_{suf}")
        for r in rows or []:
            code = str(pick(r, "公司代號") or "").strip()
            if not code:
                continue
            revenue = num(pick(r, "營業收入", exclude=("營業收入淨額以外",)))
            gross = num(pick(r, "營業毛利（毛損）淨額", "營業毛利（毛損）", "營業毛利"))
            inc[code] = {
                "y": pick(r, "年度"), "q": num(pick(r, "季別")),
                "eps": num(pick(r, "基本每股盈餘")),
                "ni": num(pick(r, "淨利（淨損）歸屬於母公司業主", "歸屬於母公司業主")),
                "gm": round(gross / revenue * 100, 2) if gross is not None and revenue else None,
            }
    if inc:
        # 新一季財報是陸續公布的，還沒公布的公司保留上一季資料
        fund["income"] = {**fund.get("income", {}), **inc}
    # 資產負債表（算 ROE 用）
    eq = {}
    for suf in ["ci", "basi", "bd", "fh", "ins", "mim"]:
        rows = openapi(f"opendata/t187ap07_L_{suf}")
        for r in rows or []:
            code = str(pick(r, "公司代號") or "").strip()
            v = num(pick(r, "歸屬於母公司業主之權益合計", "權益總額", "權益合計"))
            if code and v:
                eq[code] = v
    if eq:
        fund["equity"] = {**fund.get("equity", {}), **eq}
    fund["updated"] = datetime.now(TW).isoformat()
    return fund


# ---------------------------------------------------------------- update history
def update_daily(store: dict, nodata: list, fetcher, keep: int, window_days: int, label: str,
                 names: dict | None = None):
    today = datetime.now(TW).date()
    nd = set(nodata)
    fetched = 0
    for d in weekdays_back(today, window_days):
        k = ymd(d)
        if k in store or k in nd:
            continue
        if len(store) >= keep and k < min(store):
            break
        res, st = fetcher(d)
        if st == "ok":
            if names is not None:
                data, nm = res
                store[k] = data
                names.update(nm)
            else:
                store[k] = res
            fetched += 1
            log(f"  {label} {k} ok ({len(store[k])})")
        elif st == "nodata" and d < today:
            nd.add(k)
        elif st == "error":
            break  # 被擋或網路問題就先停，下次再補
    for k in sorted(store)[:-keep]:
        del store[k]
    cutoff = ymd(today - timedelta(days=window_days + 5))
    return sorted(x for x in nd if x >= cutoff), fetched


def update_pe_monthly(pe: dict):
    # 舊版只存本益比數字，換成 [本益比, 收盤價] 格式需重抓
    if any(not isinstance(v, list) for mon in pe.values() for v in list(mon.values())[:1]):
        pe.clear()
    today = datetime.now(TW).date()
    y, m = today.year, today.month
    months = []
    for _ in range(PE_MONTHS):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append((y, m))
    for (y, m) in months:
        key = f"{y}{m:02d}"
        if key in pe:
            continue
        for day in (15, 16, 17, 18, 14, 13, 19, 20, 21, 22, 23, 24, 25, 26, 12, 11, 10):
            d = date(y, m, day)
            if d.weekday() >= 5:
                continue
            res, st = fetch_bwibbu(d)
            if st == "ok":
                closes = {c: v[3] for c, v in res.items() if v[3]}
                if len(closes) < 100:  # 舊格式沒有收盤價，改抓同一天的收盤行情
                    pr, st2 = fetch_prices(d)
                    if st2 == "ok":
                        closes = {c: v[0] for c, v in pr[0].items()}
                pe[key] = {c: [v[0], closes.get(c)] for c, v in res.items() if v[0]}
                log(f"  PE {key} ok ({len(pe[key])})")
                break
            if st == "error":
                return
    keep = {f"{y}{m:02d}" for (y, m) in months}
    for k in list(pe):
        if k not in keep:
            del pe[k]


# ---------------------------------------------------------------- 支撐 / 大量區
def dist_score(d):
    """股價高於支撐 d%：越貼近支撐（但沒跌破）分數越高"""
    if d is None:
        return None
    if d < 0:
        return 10.0
    for lim, sc in ((3, 100), (6, 85), (10, 65), (15, 45)):
        if d <= lim:
            return float(sc)
    return 30.0


def support_levels(ser):
    """ser: [[close, shares, value, high, low], ...] 由舊到新"""
    n = len(ser)
    if n < 30:
        return {}
    closes = [x[0] for x in ser]
    lows = [x[4] if len(x) > 4 and x[4] else x[0] for x in ser]
    p = closes[-1]
    out = {}
    # 波段低點：前後各 5 日內最低，且之後至少 3 天確認
    swings = [i for i in range(1, n - 3) if lows[i] == min(lows[max(0, i - 5):i + 6])]
    if swings:
        last_sw = lows[swings[-1]]
        out["sup_last"] = last_sw                      # 最近一個波段低點（前波支撐）
        out["sup_last_d"] = int(n - 1 - swings[-1])    # 幾個交易日前
        out["sup_broke"] = p < last_sw
        below = [lows[i] for i in swings if lows[i] <= p]
        if below:
            out["sup"] = max(below)                    # 目前價位下方最近的支撐
            out["sup_dist"] = round((p - out["sup"]) / p * 100, 2)
        elif out["sup_broke"]:
            out["sup_dist"] = round((p - last_sw) / p * 100, 2)
    # 大量區（分價量表）：依收盤價分 24 格，以成交金額加權
    lo, hi = min(lows), max(x[3] if len(x) > 3 and x[3] else x[0] for x in ser)
    if hi > lo:
        nb = 24
        w = (hi - lo) / nb
        bins = [0.0] * nb
        for x in ser:
            b = min(nb - 1, int((x[0] - lo) / w))
            bins[b] += x[2]
        center = lambda b: round(lo + (b + 0.5) * w, 2)
        poc = max(range(nb), key=lambda b: bins[b])
        out["poc"] = center(poc)
        avg = sum(bins) / nb
        heavy = [b for b in range(nb) if bins[b] >= avg * 1.2]
        below = [b for b in heavy if center(b) <= p]
        above = [b for b in heavy if center(b) > p]
        if below:
            vb = max(below, key=lambda b: (bins[b], b))
            out["vsup"] = center(vb)
            out["vsup_dist"] = round((p - out["vsup"]) / p * 100, 2)
        if above:
            va = min(above, key=lambda b: abs(center(b) - p))
            out["vres"] = center(va)
        out["vp"] = [[center(b), round(bins[b] / 1e8, 2)] for b in range(nb)]
    s1 = 10.0 if out.get("sup_broke") else dist_score(out.get("sup_dist"))
    s2 = dist_score(out.get("vsup_dist")) if "vsup" in out else (20.0 if "poc" in out else None)
    parts = [x for x in (s1, s2) if x is not None]
    if parts:
        out["sup_score"] = round(sum(parts) / len(parts), 1)
    return out


# ---------------------------------------------------------------- scoring
def pct_rank(values: dict):
    """{code: v} -> {code: 0~1 百分位}（越大越好）"""
    items = [(c, v) for c, v in values.items() if v is not None]
    if not items:
        return {}
    items.sort(key=lambda x: x[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = ((i + j) / 2) / (n - 1) if n > 1 else 0.5
        for k in range(i, j + 1):
            out[items[k][0]] = p
        i = j + 1
    return out


def combine(ranks: dict, weights: dict, code: str):
    s = w = 0.0
    for k, wt in weights.items():
        v = ranks[k].get(code)
        if v is not None:
            s += v * wt
            w += wt
    return round(s / w * 100, 1) if w >= 0.3 else None


def build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores):
    pdates = sorted(prices)
    tdates = sorted(t86)
    if not pdates:
        return None
    last = pdates[-1]
    rev = fund.get("revenue", {})
    inc = fund.get("income", {})
    eqt = fund.get("equity", {})
    bw = (bw_latest or {}).get("data", {})

    metrics = {}
    vp_cache = {}
    for code, row in prices[last].items():
        close = row[0]
        if not is_stock(code):
            continue
        ser = [prices[d][code] for d in pdates if code in prices[d]]
        closes = [x[0] for x in ser]
        m = {"p": close}
        last20 = ser[-20:]
        avg_val = sum(x[2] for x in last20) / len(last20)
        m["avgval"] = round(avg_val)
        if len(closes) >= 21:
            m["r20"] = round((closes[-1] / closes[-21] - 1) * 100, 2)
        if len(closes) >= 2:
            m["chg"] = round((closes[-1] / closes[-2] - 1) * 100, 2)
        n60 = closes[-60:]
        if len(n60) >= 40:
            m["b60"] = round((closes[-1] / (sum(n60) / len(n60)) - 1) * 100, 2)
        # 籌碼：法人買超股數 / 同期間成交股數（%）
        vol_by_date = {d: prices[d][code][1] for d in pdates if code in prices[d]}

        def ratio(days, idx):
            ds = tdates[-days:]
            if len(ds) < max(3, days // 2):
                return None
            buy = sum(t86[d].get(code, [0, 0, 0])[idx] if idx >= 0 else sum(t86[d].get(code, [0, 0, 0]))
                      for d in ds)
            vol = sum(vol_by_date.get(d, 0) for d in ds)
            return round(buy / vol * 100, 2) if vol else None

        m["f20"], m["t20"], m["i5"] = ratio(20, 0), ratio(20, 1), ratio(5, -1)
        r = rev.get(code)
        if r:
            m["rev_yoy"], m["rev_mom"], m["cum_yoy"] = r["rev_yoy"], r["rev_mom"], r["cum_yoy"]
            m["rev_ym"] = r["ym"]
        m.update({k: v for k, v in revenue_metrics(fund.get("rev_hist", {}).get(code, {})).items()
                  if v is not None})
        i = inc.get(code)
        if i:
            m["eps"], m["eps_q"], m["gm"] = i["eps"], i["q"], i["gm"]
            m["eps_y"] = i["y"]
            if i.get("ni") is not None and eqt.get(code) and i.get("q"):
                m["roe"] = round(i["ni"] / eqt[code] * 4 / i["q"] * 100, 2)
        v = bw.get(code)
        if v:
            m["pe"], m["yld"], m["pb"] = v[:3]
        if m.get("pe"):
            hist = [(pe_monthly[k].get(code) or [None])[0] for k in pe_monthly]
            hist = [h for h in hist if h]
            if len(hist) >= 24:
                m["pe_pos"] = round(sum(1 for h in hist if h < m["pe"]) / len(hist) * 100, 1)
                m["pe_lo"], m["pe_hi"] = min(hist), max(hist)
                m["pe_n"] = len(hist)
        lv = support_levels(ser)
        vp_cache[code] = lv.pop("vp", None)
        m.update(lv)
        if m.get("pe_pos") is not None:
            m["pe_cheap"] = -m["pe_pos"]
        if m.get("pb"):
            m["pb_cheap"] = -m["pb"]
        m["liquid"] = avg_val >= MIN_AVG_VALUE
        metrics[code] = m

    uni = {c: m for c, m in metrics.items() if m["liquid"]}
    keys = set(FUND_W) | set(CHIP_W) | set(TECH_W) | set(VAL_W)
    ranks = {k: pct_rank({c: m.get(k) for c, m in uni.items()}) for k in keys}

    stocks = []
    for code, m in metrics.items():
        sc = {}
        if m["liquid"]:
            sc["fund"] = combine(ranks, FUND_W, code)
            sc["chip"] = combine(ranks, CHIP_W, code)
            sc["tech"] = combine(ranks, TECH_W, code)
            sc["value"] = combine(ranks, VAL_W, code)
            sc["sup"] = m.get("sup_score")
            for name, wts in STRATS.items():
                parts = [(sc[k], w) for k, w in wts.items() if sc.get(k) is not None]
                tw = sum(w for _, w in parts)
                sc[name] = round(sum(v * w for v, w in parts) / tw, 1) if tw >= 0.55 else None
            m["pct"] = {k: round(ranks[k][code] * 100) for k in keys if code in ranks[k]}
        stocks.append({"c": code, "n": names.get(code, ""), "ind": (rev.get(code) or {}).get("ind"),
                       "s": sc, "m": m})
    stocks.sort(key=lambda x: (x["s"].get("long") is None, -(x["s"].get("long") or 0)))

    # 分數歷史 → 5 日分數變化（{日期: {代號: [短線, 長線]}}）
    for k in list(scores):
        if any(not isinstance(v, list) for v in scores[k].values()):
            del scores[k]
    scores[last] = {x["c"]: [x["s"].get("short"), x["s"].get("long")] for x in stocks
                    if x["s"].get("short") is not None or x["s"].get("long") is not None}
    for k in sorted(scores)[:-SCORE_KEEP]:
        del scores[k]
    sdates = sorted(scores)
    ref = scores[sdates[-6]] if len(sdates) >= 6 else (scores[sdates[0]] if len(sdates) > 1 else {})
    for x in stocks:
        old = ref.get(x["c"])
        if not old:
            continue
        for i, name in enumerate(("short", "long")):
            t = x["s"].get(name)
            if t is not None and old[i] is not None:
                x["s"]["d5_" + name] = round(t - old[i], 1)

    # 市場概況
    chgs = [m.get("chg") for m in metrics.values() if m.get("chg") is not None]
    idates = sorted(index)
    market = {
        "up": sum(1 for c in chgs if c > 0), "down": sum(1 for c in chgs if c < 0),
        "flat": sum(1 for c in chgs if c == 0),
        "index": [[d, index[d][0]] for d in idates if index[d][0]],
    }
    if idates:
        market["taiex"], market["taiex_chg"] = index[idates[-1]]
        market["taiex_date"] = idates[-1]

    # 產業統計
    inds = {}
    for x in stocks:
        if x["ind"] and x["s"].get("long") is not None and x["s"].get("short") is not None:
            inds.setdefault(x["ind"], []).append(x)
    industries = []
    for name, lst in inds.items():
        r20 = [x["m"]["r20"] for x in lst if x["m"].get("r20") is not None]
        chg = [x["m"]["chg"] for x in lst if x["m"].get("chg") is not None]
        industries.append({
            "ind": name, "n": len(lst),
            "short": round(sum(x["s"]["short"] for x in lst) / len(lst), 1),
            "long": round(sum(x["s"]["long"] for x in lst) / len(lst), 1),
            "r20": round(sum(r20) / len(r20), 2) if r20 else None,
            "chg": round(sum(chg) / len(chg), 2) if chg else None,
            "top_short": [[x["c"], x["n"], x["s"]["short"]]
                          for x in sorted(lst, key=lambda x: -x["s"]["short"])[:3]],
            "top_long": [[x["c"], x["n"], x["s"]["long"]]
                         for x in sorted(lst, key=lambda x: -x["s"]["long"])[:3]],
        })
    industries.sort(key=lambda i: -i["long"])

    # 個股明細檔（點開個股才載入）
    sdir = OUT / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    rev_hist = fund.get("rev_hist", {})
    pmonths = sorted(pe_monthly)
    for x in stocks:
        code = x["c"]
        ds = [d for d in pdates if code in prices[d]]
        det = {"d": ds, "c": [prices[d][code][0] for d in ds],
               "v": [round(prices[d][code][2] / 1e8, 2) for d in ds],
               "vp": vp_cache.get(code)}
        td = [d for d in tdates if code in t86[d]]
        det["t"] = {"d": td, "f": [round(t86[d][code][0] / 1000) for d in td],
                    "i": [round(t86[d][code][1] / 1000) for d in td],
                    "s": [round(t86[d][code][2] / 1000) for d in td]}
        # 本益比河流圖：隱含 EPS = 收盤價 / 本益比；各帶 = 歷史本益比分位 × EPS
        rm, rp, re_ = [], [], []
        for k in pmonths:
            v = pe_monthly[k].get(code)
            if v and v[0] and v[1]:
                rm.append(k)
                rp.append(v[1])
                re_.append(round(v[1] / v[0], 3))
        m = x["m"]
        if len(rm) >= 24:
            pes = sorted(round(p / e, 2) for p, e in zip(rp, re_))
            qs = [pes[min(len(pes) - 1, int(len(pes) * q))] for q in (0.1, 0.3, 0.5, 0.7, 0.9)]
            if m.get("pe") and m.get("p"):
                rm.append(last)
                rp.append(m["p"])
                re_.append(round(m["p"] / m["pe"], 3))
            det["river"] = {"m": rm, "p": rp, "e": re_, "q": qs}
        sd = [d for d in sdates if code in scores[d]]
        det["sc"] = {"d": sd, "short": [scores[d][code][0] for d in sd],
                     "long": [scores[d][code][1] for d in sd]}
        h = rev_hist.get(code, {})
        det["rev"] = [[k, *h[k]] for k in sorted(h)]
        (sdir / f"{code}.json").write_text(json.dumps(det, ensure_ascii=False, separators=(",", ":")),
                                            encoding="utf-8")

    return {
        "market": market,
        "industries": industries,
        "date": last,
        "generated_at": datetime.now(TW).isoformat(timespec="minutes"),
        "price_days": len(pdates),
        "t86_days": len(tdates),
        "pe_months": len(pe_monthly),
        "fund_updated": fund.get("updated"),
        "weights": {"strats": STRATS, "fund": FUND_W, "chip": CHIP_W, "tech": TECH_W, "value": VAL_W},
        "min_avg_value": MIN_AVG_VALUE,
        "stocks": stocks,
    }


# ---------------------------------------------------------------- main
def main():
    prices = load("prices.json", {})
    names = load("names.json", {})
    t86 = load("t86.json", {})
    pe_monthly = load("pe_monthly.json", {})
    bw_latest = load("bwibbu_latest.json", {})
    meta = load("meta.json", {"nodata_prices": [], "nodata_t86": []})
    fund = load("fundamentals.json", {})
    index = load("index.json", {})
    scores = load("scores.json", {})

    log("== 收盤行情")
    meta["nodata_prices"], n1 = update_daily(prices, meta["nodata_prices"], fetch_prices,
                                             PRICE_KEEP, 200, "price", names)
    log("== 三大法人")
    meta["nodata_t86"], n2 = update_daily(t86, meta["nodata_t86"], fetch_t86, T86_KEEP, 40, "t86")
    log("== 本益比/殖利率（最新）")
    if prices:
        last = max(prices)
        # 當天本益比可能還沒公布，往前找最近有資料的交易日
        for k in sorted(prices, reverse=True)[:4]:
            if bw_latest.get("date") and bw_latest["date"] >= k:
                break
            res, st = fetch_bwibbu(datetime.strptime(k, "%Y%m%d").date())
            if st == "ok":
                bw_latest = {"date": k, "data": res}
                log(f"  本益比 {k} ok ({len(res)})")
                break
    log("== 本益比月取樣（位階用）")
    update_pe_monthly(pe_monthly)
    log("== 加權指數")
    update_index(index)
    log("== 月營收 / 財報")
    fund = fetch_fundamentals(fund)
    log("== 月營收歷史（公開資訊觀測站）")
    backfill_revenue(fund)
    for h in fund.get("rev_hist", {}).values():
        for k in sorted(h)[:-REV_MONTHS]:
            del h[k]

    save("prices.json", prices)
    save("names.json", names)
    save("t86.json", t86)
    save("pe_monthly.json", pe_monthly)
    save("bwibbu_latest.json", bw_latest)
    save("meta.json", meta)
    save("fundamentals.json", fund)

    out = build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores)
    save("index.json", index)
    save("scores.json", scores)
    save("fundamentals.json", fund)
    probe["rwd_calls"] = rwd_calls
    probe["finished"] = datetime.now(TW).isoformat()
    probe["counts"] = {"price_days": len(prices), "t86_days": len(t86), "pe_months": len(pe_monthly),
                       "revenue": len(fund.get("revenue", {})), "income": len(fund.get("income", {})),
                       "equity": len(fund.get("equity", {})), "index_days": len(index),
                       "rev_hist_months": len({k for h in fund.get("rev_hist", {}).values() for k in h})}
    save("probe.json", probe)
    if out:
        save("latest.json", out, OUT)
        n = sum(1 for s in out["stocks"] if s["s"].get("long") is not None)
        log(f"輸出完成：{out['date']}，共 {len(out['stocks'])} 檔，有總分 {n} 檔")
    else:
        log("沒有收盤資料，無法輸出")
        if not (OUT / "latest.json").exists():
            sys.exit(1)


if __name__ == "__main__":
    main()
