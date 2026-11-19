"""
台股評分資料更新程式（給 GitHub Actions 每天跑）

流程：
1. 從證交所抓資料（收盤行情、三大法人、本益比/殖利率、月營收、財報）
2. 歷史資料存在 data/*.json（取代資料庫，由 Actions commit 回 repo）
3. 計算基本面 / 籌碼面 / 技術面分數，輸出 site/data/latest.json 給網頁用

只用 Python 標準函式庫 + requests。
"""
from __future__ import annotations

import gzip
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
PE_MONTHS = 120          # 本益比月取樣保留 10 年（回測從 2018 起需要；位階只用最近 5 年）
PE_POS_MONTHS = 60
MAX_RWD_CALLS = int(os.environ.get("MAX_RWD_CALLS", "320"))  # 每次執行最多打幾次 twse.com.tw
RWD_SLEEP = 3.2          # 證交所網站有頻率限制，每次間隔秒數
MIN_AVG_VALUE = 3e7      # 流動性門檻：20 日平均成交金額（元）

# 兩種策略（依 2024/10~2026/10 回測結果設計，見 scripts/backtest.py）
# 回測中有穩定預測力的只有：營收成長（連續年增、近3月、動能、累計）、流動性（成交金額規模）、外資買超
# 估值、支撐、趨勢在回測中沒有預測力 → 不計分，只當參考資訊顯示
# 短線、長線回測找不到不同的有效指標（兩個分數相關 0.99）→ 合併成一個綜合分數；
# 短線 / 長線只差在「選股規則」（進場條件、持有天數）
SCORE_W = {"fund": 0.40, "liq": 0.20, "peg": 0.20, "chip": 0.20}
STRATS = {"score": SCORE_W}
# 策略選股規則（回測驗證期勝率見網頁「策略選股」）
RULES = {
    "long_core": {"h": "long", "n": 5, "sort": "f20", "t": "長線：營收連續年增 ≥ 12 個月＋20 日均成交金額 ≥ 10 億＋不過熱"},
    "short_core": {"h": "short", "n": 3, "sort": "qf", "t": "短線：營收連續年增 ≥ 12 個月＋營收動能 > 0＋20 日均成交金額 ≥ 10 億＋股價在 20 日線下＋不過熱"},
}
# 精選：符合規則的股票中，外資 20 日買超比例最高的前 N 檔（回測中排序方式以訓練期表現選出）


# （試過「同產業最多 2 檔」：回測沒有比較好，訓練期最差一期反而從 −24% 變 −32%，所以不限制）
def top_picks(cands: list, rid: str):
    """cands: [(code, m), ...] → 精選代號：長線外資 20 日買超比例高的優先、短線 PEG 高的優先（回測選出）"""
    k = RULES[rid].get("sort", "f20")
    c = [(code, m) for code, m in cands if rid in (m.get("rules") or [])]
    c.sort(key=lambda x: -(x[1].get(k) if x[1].get(k) is not None else -1e9))
    return [code for code, _ in c[:RULES[rid]["n"]]]
# 乖離過大：20 日乖離 > +20% 或季線乖離 > +50%。回測中這類股票勝率掉到 50% 上下 → 分數扣分、選股排除
OVER_B20, OVER_B60, OVER_PENALTY = 20.0, 50.0, 15.0


def is_over(m: dict) -> bool:
    return (m.get("b20") or 0) > OVER_B20 or (m.get("b60") or 0) > OVER_B60


SCORE_VER = 6          # 評分公式版本（改公式就 +1，分數歷史會重新累積）
RULE_MIN_VALUE = 1e9   # 20 日均成交金額 10 億


def rule_hits(m: dict):
    """固定門檻，使用者自己用看盤軟體也能照做。m 需要 streak、accel、b20、b60、avgval"""
    hits = []
    if (m.get("streak") or 0) >= 12 and (m.get("avgval") or 0) >= RULE_MIN_VALUE and not is_over(m):
        hits.append("long_core")
        if (m.get("accel") or -1) > 0 and m.get("b20") is not None and m["b20"] < 0:
            hits.append("short_core")
    return hits


def strat_score(sc: dict, m: dict, wts: dict):
    parts = [(sc[k], w) for k, w in wts.items() if sc.get(k) is not None]
    tw = sum(w for _, w in parts)
    if tw < 0.55:
        return None
    v = sum(x * w for x, w in parts) / tw
    return round(max(0.0, v - OVER_PENALTY) if is_over(m) else v, 1)


VAL_W = {"val_cheap": 0.80, "yld": 0.20}
# 營收與獲利關係不穩定的產業（建案認列、金融），不做預估本益比，用一般本益比位階
FWD_SKIP_IND = {"建材營造", "金融保險業"}


def industry(code: str, rev: dict):
    """產業別來自月營收資料；金控、保險不在一般月營收表裡 → 依代號補（28xx、5880 = 金融保險業）"""
    ind = (rev.get(code) or {}).get("ind")
    if not ind and (code.startswith("28") or code == "5880"):
        ind = "金融保險業"
    return ind
# 只用少數核心指標計分，避免雜訊（其他數字仍會顯示在網頁上）
FUND_W = {"streak": 0.35, "rev_hi": 0.35, "accel": 0.30}   # 連續年增、營收創 12 月新高、營收動能
CHIP_W = {"qf": 1.0}     # 外資持股比例（外資 20 日買超經 Newey-West 修正後不顯著，改用持股比例）
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


def hist_read(prefix: str, ym: str) -> dict:
    """data/hist 的月檔：新格式 .json.gz（壓縮），相容舊的 .json；兩個都有就合併"""
    out = {}
    js, gz = DATA / "hist" / f"{prefix}_{ym}.json", DATA / "hist" / f"{prefix}_{ym}.json.gz"
    if js.exists():
        out.update(json.loads(js.read_text(encoding="utf-8")))
    if gz.exists():
        for d, rows in json.loads(gzip.decompress(gz.read_bytes()).decode("utf-8")).items():
            if d not in out or len(rows) > len(out[d]):
                out[d] = rows
    return out


def hist_write(prefix: str, ym: str, obj: dict):
    h = DATA / "hist"
    h.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    (h / f"{prefix}_{ym}.json.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    old = h / f"{prefix}_{ym}.json"
    if old.exists():
        old.unlink()


def hist_months(prefix: str):
    h = DATA / "hist"
    return sorted({p.name.split("_")[1].split(".")[0] for p in h.glob(f"{prefix}_*.json*")}) if h.exists() else []


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
            info["ok"] = str(js.get("stat")).upper() == "OK"
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


# ---------------------------------------------------------------- 上櫃（櫃買中心）
TPEX = "https://www.tpex.org.tw/www/zh-tw"
TPEX_SLEEP = 2.5
MAX_TPEX_CALLS = int(os.environ.get("MAX_TPEX_CALLS", "250"))
tpex_calls = 0
OTC_CODES: set = set()          # 曾經出現在上櫃行情的代號（判斷某天資料是否已含上櫃用）
OTC_LATEST = {"d": "", "codes": []}   # 最新一天的上櫃代號（標示「櫃」用；轉上市的股票不會誤標）
ETF_RE = re.compile(r"00\d{2,4}[A-Z]?")


def has_otc(rows) -> bool:
    """這一天（或這個月取樣）的資料是否已含上櫃"""
    return bool(OTC_CODES) and len(OTC_CODES & set(rows)) >= 50


def tpex_json(path, d: date, **params):
    global tpex_calls
    if tpex_calls >= MAX_TPEX_CALLS:
        return None
    tpex_calls += 1
    time.sleep(TPEX_SLEEP)
    return get_json(f"{TPEX}/{path}", {**params, "date": d.strftime("%Y/%m/%d"), "response": "json"})


def fetch_prices_otc(d: date):
    js = tpex_json("afterTrading/dailyQuotes", d, type="EW")
    if js is None:
        return None, "error"
    record_probe("tpex_quotes", js)
    t = find_table(js, ["代號", "收盤"])
    if not t:
        return None, "nodata"
    f, rows = t
    ic, inm, icl = col(f, "代號"), col(f, "名稱"), col(f, "收盤")
    ish, iv, ihi, ilo = col(f, "成交股數"), col(f, "成交金額"), col(f, "最高"), col(f, "最低")
    out, names = {}, {}
    for r in rows:
        code = str(r[ic]).strip()
        if not (is_stock(code) or ETF_RE.fullmatch(code)):
            continue
        close = num(r[icl])
        if close is None:
            continue
        hi = num(r[ihi]) if ihi is not None else None
        lo = num(r[ilo]) if ilo is not None else None
        out[code] = [close, num(r[ish]) or 0, num(r[iv]) or 0, hi or close, lo or close]
        names[code] = str(r[inm]).strip()
    if len(out) < 100:
        return None, "nodata"
    OTC_CODES.update(out)
    return (out, names), "ok"


def fetch_t86_otc(d: date):
    """欄位：外資(不含外資自營商) 買/賣/超 = 2~4、外資自營商 5~7、外資合計 8~10、投信 11~13、
    自營商自行 14~16、自營商避險 17~19、自營商合計 20~22、三大法人合計 23"""
    js = tpex_json("insti/dailyTrade", d, type="Daily", sect="EW")
    if js is None:
        return None, "error"
    record_probe("tpex_insti", js)
    t = find_table(js, ["代號", "買賣超股數"])
    if not t:
        return None, "nodata"
    f, rows = t
    nets = [i for i, x in enumerate(f) if "買賣超" in str(x) or "淨買" in str(x)]   # 舊格式叫「淨買股數」
    if len(f) >= 24:
        idx = (4, 13, 22)
    elif len(nets) >= 4:      # 舊格式：外資、投信、自營商、合計
        idx = (nets[0], nets[1], nets[2])
    else:
        probe["errors"].append(f"tpex insti 欄位無法對應 {f}"[:200])
        return None, "nodata"
    out = {}
    for r in rows:
        out[str(r[0]).strip()] = [num(r[idx[0]]) or 0, num(r[idx[1]]) or 0, num(r[idx[2]]) or 0]
    return (out if len(out) >= 100 else None), ("ok" if len(out) >= 100 else "nodata")


def fetch_bwibbu_otc(d: date):
    js = tpex_json("afterTrading/peQryDate", d)
    if js is None:
        return None, "error"
    record_probe("tpex_pe", js)
    t = find_table(js, ["股票代號", "本益比"])
    if not t:
        return None, "nodata"
    f, rows = t
    ic, ipe, iy, ipb = col(f, "股票代號"), col(f, "本益比"), col(f, "殖利率"), col(f, "股價淨值比")
    out = {}
    for r in rows:
        out[str(r[ic]).strip()] = [num(r[ipe]), num(r[iy]) if iy is not None else None,
                                   num(r[ipb]) if ipb is not None else None, None]
    return (out if len(out) >= 100 else None), ("ok" if len(out) >= 100 else "nodata")


def merge_otc(store: dict, fetcher, label: str, nodata: set, names: dict | None = None):
    """已有上市資料的日期，補上同一天的上櫃資料（新的日期優先）"""
    today = ymd(datetime.now(TW).date())
    n = 0
    for k in sorted(store, reverse=True):
        if k in nodata or has_otc(store[k]):
            continue
        res, st = fetcher(datetime.strptime(k, "%Y%m%d").date())
        if st == "ok":
            if names is not None:
                res, nm = res
                names.update(nm)
            store[k].update(res)
            if names is not None and k > OTC_LATEST["d"]:
                OTC_LATEST.update(d=k, codes=sorted(res))
            n += 1
            log(f"  上櫃 {label} {k} ok ({len(res)})")
        elif st == "nodata" and k < today:
            nodata.add(k)
        elif st == "error":
            break
    return n


def update_tdcc(tdcc: dict):
    """集保戶股權分散表（每週五公布）：{日期: {代號: [400 張以上持股 %, 1000 張以上 %, 總人數]}}
    公開資料只有最新一週，所以每天抓一次、換週才多一筆；歷史從開始記錄起累積"""
    try:
        r = S.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        txt = r.content.decode("utf-8-sig", errors="ignore")
    except Exception as e:
        probe["errors"].append(f"tdcc: {e}"[:200])
        return
    out, day = {}, None
    for line in txt.splitlines()[1:]:
        x = [v.strip().strip('"') for v in line.split(",")]
        if len(x) < 6 or not x[2].isdigit():
            continue
        day, code, lv = x[0], x[1], int(x[2])
        if not is_stock(code):
            continue
        o = out.setdefault(code, [0.0, 0.0, None])
        pct = num(x[5]) or 0
        if 12 <= lv <= 15:      # 400,001 股以上
            o[0] += pct
        if lv == 15:            # 1,000,001 股以上
            o[1] += pct
        if lv == 17:            # 合計
            o[2] = num(x[3])
    probe["calls"]["tdcc"] = {"ok": bool(out), "date": day, "n": len(out), "head": txt[:200]}
    if day and out:
        tdcc[day] = {c: [round(a, 2), round(b, 2), n] for c, (a, b, n) in out.items()}
    for k in sorted(tdcc)[:-60]:
        del tdcc[k]


def fetch_qfii(d: date):
    """外資持股比例 %：上市 MI_QFIIS ＋ 上櫃 insti/qfii → {代號: [比例, 發行股數]}"""
    out = {}
    js = get_json("https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS",
                  {"date": ymd(d), "selectType": "ALLBUT0999", "response": "json"}, rwd=True)
    if js is not None:
        record_probe("MI_QFIIS", js)
        t = find_table(js, ["證券代號", "持股比率"])
        if t:
            f, rows = t
            ir = col(f, "全體外資及陸資持股比率", "持股比率", exclude=("尚可", "上限"))
            isu = col(f, "發行股數")
            for r in rows:
                out[str(r[0]).strip()] = [num(r[ir]), num(r[isu]) if isu is not None else None]
    js = tpex_json("insti/qfii", d)
    if js is not None:
        record_probe("tpex_qfii", js)
        t = find_table(js, ["代號", "持股比率"])
        if t:
            f, rows = t
            fs = [str(x) for x in f]
            ic, ir = col(fs, "代號"), col(fs, "持股比率", exclude=("尚可", "上限"))
            isu = col(fs, "發行股數")
            if ic is not None and ir is not None:
                for r in rows:
                    out[str(r[ic]).strip()] = [num(r[ir]), num(r[isu]) if isu is not None else None]
    tw = set(_TWSE_CODES)
    n_tw = sum(1 for c in out if c in tw)
    probe["calls"]["qfii_counts"] = {"date": ymd(d), "total": len(out), "twse": n_tw}
    return out if n_tw >= 500 else None      # 上市還沒公布（只有上櫃）就不算抓到，下次再抓


_TWSE_CODES: list = []     # 上市代號（main 從最新收盤行情填入）


# ---------------------------------------------------------------- 三步驟分數（使用者架構）
# 基本面 65%：營收預估修正、成長性（獲利能力等季報歷史補齊後加入）
# 技術面 20%：趨勢（站上季線但沒追高）、接近支撐（分批買、不追價）
# 籌碼面 15%：外資持股 4 週變化、集保大戶持股 4 週變化、負乖離（不追高）
STEP_W = {"f": 1.0, "t": 0.0, "c": 0.0}     # 研究 3：技術面、籌碼面加進分數會變差，改為只顯示不計分
STEP_F = {"rev_up_n": .30, "rev_rev3": .20, "rev_hi": .20, "accel": .15, "streak": .15}
STEP_C = {"qd4": .40, "big400_chg": .30, "-b20": .30}
MIN_CAP = 1e10            # 第二步候選：市值至少 100 億


def trend_ok(b60):
    if b60 is None:
        return None
    return 100.0 if 0 <= b60 <= 15 else 60.0 if (-10 <= b60 < 0 or 15 < b60 <= 30) else 20.0


def step_scores(stocks):
    uni = [x for x in stocks if x["m"].get("liquid")]
    def ranks(keys):
        out = {}
        for k in keys:
            key, sign = k.lstrip("-"), (-1 if k.startswith("-") else 1)
            out[k] = pct_rank({x["c"]: (x["m"][key] * sign if x["m"].get(key) is not None else None) for x in uni})
        return out
    rf, rc = ranks(STEP_F), ranks(STEP_C)
    def comb(rk, W, c):
        parts = [(rk[k][c], w) for k, w in W.items() if c in rk[k]]
        tw = sum(w for _, w in parts)
        return round(sum(v * w for v, w in parts) / tw * 100, 1) if tw >= 0.3 * sum(W.values()) else None
    for x in uni:
        m, c = x["m"], x["c"]
        f = comb(rf, STEP_F, c)
        t_parts = [v for v in (trend_ok(m.get("b60")), m.get("sup_score")) if v is not None]
        t = round(sum(t_parts) / len(t_parts), 1) if t_parts else None
        ch = comb(rc, STEP_C, c)
        x["s"]["f"], x["s"]["t"], x["s"]["c"] = f, t, ch
        parts = [(v, STEP_W[k]) for k, v in (("f", f), ("t", t), ("c", ch)) if v is not None]
        if f is None or not parts:
            x["s"]["score"] = None
            continue
        tw = sum(w for _, w in parts)
        v = sum(a * w for a, w in parts) / tw
        x["s"]["score"] = round(max(0.0, v - OVER_PENALTY) if is_over(m) else v, 1)


def step2_list(stocks, groups_ind, groups_ai, n_ind=5, n_ai=3):
    """第二步候選：前段族群（產業前 5、AI 環節前 3）→ 市值 ≥ 100 億 → 營收預估上修中 → 沒過熱 → 依分數排序"""
    top_ind = [g["seg"] for g in groups_ind[:n_ind]]
    top_ai = [g["seg"] for g in groups_ai[:n_ai]]
    pool = {c: g["seg"] for g in groups_ind[:n_ind] for c in g["codes"]}
    for g in groups_ai[:n_ai]:
        for c in g["codes"]:
            pool.setdefault(c, "AI：" + g["seg"])
    S_ = {x["c"]: x for x in stocks}
    rows = []
    for c, grp in pool.items():
        x = S_.get(c)
        if not x or x["s"].get("score") is None:
            continue
        m = x["m"]
        cap = m.get("cap")
        if cap is not None and cap < MIN_CAP:
            continue
        if cap is None and (m.get("avgval") or 0) < 2e8:
            continue
        if (m.get("rev_up_n") or 0) < 1 or m.get("over"):
            continue
        m["grp"] = grp
        rows.append(x)
    rows.sort(key=lambda x: -x["s"]["score"])
    return {"ind": top_ind, "ai": top_ai, "codes": [x["c"] for x in rows[:30]]}


# ---------------------------------------------------------------- 第一步：營收預估上修、法說會、新聞
def est12(h: dict, k: str):
    """月份 k 時推估的「未來 12 個月營收」= 近 12 個月營收 ×（1 + 近 3 個月年增率）。
    每月新營收公布後重算一次；這個數字比上個月高 = 營收預估上修"""
    def sh(k, n):
        y, m = int(k[:-2]), int(k[-2:]) - n
        while m <= 0:
            y, m = y - 1, m + 12
        return f"{y}{m:02d}"
    cur = [h.get(sh(k, i), [None])[0] for i in range(12)]
    p3 = [h.get(sh(k, i + 12), [None])[0] for i in range(3)]
    if None in cur or None in p3 or not sum(p3):
        return None
    g = max(-0.5, min(1.5, sum(cur[:3]) / sum(p3) - 1))
    return sum(cur) * (1 + g)


def revision_metrics(h: dict):
    """營收預估修正：最近一個月、近 3 個月的上修幅度，以及連續上修幾個月；回傳近 12 個月的預估歷史（畫圖用）"""
    if not h:
        return {}
    last = max(h)
    y, m = int(last[:-2]), int(last[-2:])
    keys = []
    for j in range(13):
        yy, mm = y, m - j
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        keys.append(f"{yy}{mm:02d}")
    ests = [est12(h, k) for k in keys]
    out = {"est_hist": [[k, round(e)] for k, e in zip(reversed(keys), reversed(ests)) if e]}
    if ests[0]:
        out["est12"] = round(ests[0])
        if ests[1]:
            out["rev_rev1"] = round((ests[0] / ests[1] - 1) * 100, 2)
        if ests[3]:
            out["rev_rev3"] = round((ests[0] / ests[3] - 1) * 100, 2)
        n = 0
        for j in range(12):
            if ests[j] and ests[j + 1] and ests[j] > ests[j + 1]:
                n += 1
            else:
                break
        out["rev_up_n"] = n
    return out


def update_confs(confs: dict):
    """公開資訊觀測站「法人說明會一覽表」：本月與下月、上市與上櫃 → {代號: [[日期, 摘要, 簡報連結], ...]}"""
    today = datetime.now(TW).date()
    got = 0
    for dm in (0, 1):
        y, m = today.year, today.month + dm
        if m > 12:
            y, m = y + 1, 1
        for typek in ("sii", "otc"):
            data = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "TYPEK": typek,
                    "year": str(y - 1911), "month": f"{m:02d}", "co_id": ""}
            try:
                time.sleep(2)
                r = S.post("https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1", data=data, timeout=40)
                html = r.content.decode("utf-8", errors="ignore")
            except Exception as e:
                probe["errors"].append(f"confs {typek} {y}{m}: {e}"[:200])
                continue
            probe["calls"].setdefault("confs_" + typek, {"status": r.status_code, "len": len(html), "head": re.sub(r"\s+", " ", html[:400])})
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
                tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)
                txt = [re.sub(r"<[^>]+>|&nbsp;", " ", x).strip() for x in tds]
                if len(txt) < 4 or not re.fullmatch(r"\d{4}", txt[0]):
                    continue
                dm_ = re.search(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", " ".join(txt[2:4]))
                if not dm_:
                    continue
                d = f"{int(dm_.group(1)) + 1911}{int(dm_.group(2)):02d}{int(dm_.group(3)):02d}"
                link = re.search(r'href="([^"]+\.pdf)"', tr, flags=re.I)
                summ = max(txt[3:], key=len)[:120]
                lst = confs.setdefault(txt[0], [])
                if not any(x[0] == d for x in lst):
                    lst.append([d, summ, ("https://mopsov.twse.com.tw" + link.group(1)) if link and link.group(1).startswith("/") else (link.group(1) if link else "")])
                    got += 1
    cut = (today - timedelta(days=120)).strftime("%Y%m%d")
    for c in list(confs):
        confs[c] = sorted(x for x in confs[c] if x[0] >= cut)[-6:]
        if not confs[c]:
            del confs[c]
    probe["calls"]["confs"] = {"ok": got > 0, "new": got, "companies": len(confs)}


NEWS_Q = {"up": ["調升目標價", "上調目標價", "目標價上調", "上修財測", "EPS上修", "獲利上修", "調高財測"],
          "down": ["調降目標價", "下調目標價", "目標價下修", "下修財測", "獲利下修", "調低財測"]}


def update_news(news: dict, names: dict):
    """Google 新聞 RSS（只抓標題）：找出「調升目標價、上修財測」等新聞提到的股票。只當參考，不計分"""
    from email.utils import parsedate_to_datetime
    nm = sorted(((n, c) for c, n in names.items() if is_stock(c) and len(n) >= 2), key=lambda x: -len(x[0]))
    got = 0
    for typ, qs in NEWS_Q.items():
        for q in qs:
            try:
                time.sleep(1.5)
                r = S.get("https://news.google.com/rss/search", params={"q": f"{q} when:7d", "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, timeout=30)
                xml = r.content.decode("utf-8", errors="ignore")
            except Exception as e:
                probe["errors"].append(f"news {q}: {e}"[:200])
                continue
            for it in re.findall(r"<item>(.*?)</item>", xml, flags=re.S):
                t = re.search(r"<title>(.*?)</title>", it, flags=re.S)
                ln = re.search(r"<link>(.*?)</link>", it, flags=re.S)
                pd = re.search(r"<pubDate>(.*?)</pubDate>", it, flags=re.S)
                if not t:
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).replace("&amp;", "&").strip()
                try:
                    d = parsedate_to_datetime(pd.group(1)).astimezone(TW).strftime("%Y%m%d") if pd else ymd(datetime.now(TW).date())
                except Exception:
                    d = ymd(datetime.now(TW).date())
                body = re.split(r"\s[-－]\s|｜", title)[0]
                if re.search(r"-(US|HK|JP|CN|KR)\b", body):
                    continue
                mc = re.search(r"[\(（](\d{4,6}[A-Z]?)-TW[\)）]", body)
                hit = mc.group(1) if mc and mc.group(1) in names else next((c for n, c in nm if n in body), None)
                if not hit:
                    continue
                lst = news.setdefault(hit, [])
                if not any(x[2] == title for x in lst):
                    lst.append([d, typ, title, ln.group(1).strip() if ln else ""])
                    got += 1
    cut = ymd(datetime.now(TW).date() - timedelta(days=120))
    for c in list(news):
        news[c] = sorted(x for x in news[c] if x[0] >= cut)[-12:]
        if not news[c]:
            del news[c]
    probe["calls"]["news"] = {"ok": got >= 0, "new": got, "companies": len(news)}


# ---------------------------------------------------------------- 法人共識 EPS（FactSet 調查新聞標題）
# 鉅亨、券商等每天發布「Factset 最新調查：技嘉(2376-TW)EPS預估上修至36.18元，預估目標價為496元」，
# 用 Google 新聞 RSS 只抓標題 → 每家公司的共識 EPS 與目標價變化。第一次執行會用 after/before 補過去 24 個月（只能補到一部分）。
CONS_Q = ["Factset 最新調查 EPS預估", "Factset 最新調查 目標價", "Factset EPS預估上修", "Factset EPS預估下修"]
RE_EPS = re.compile(r"[\(（](\d{4,6}[A-Z]?)-TW[\)）][^，,。]*?EPS預估(上修|下修|持平|維持)?至?\s*(-?[\d.]+)\s*元")
RE_TP = re.compile(r"[\(（](\d{4,6}[A-Z]?)-TW[\)）][^，,。]*?目標價(?:為|調升至|調降至|上修至|下修至)?\s*([\d.]+)\s*元")
RE_TP2 = re.compile(r"預估目標價為\s*([\d.]+)\s*元")


def _cons_fetch(q, cons, names):
    from email.utils import parsedate_to_datetime
    try:
        time.sleep(1.2)
        r = S.get("https://news.google.com/rss/search", params={"q": q, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, timeout=30)
        xml = r.content.decode("utf-8", errors="ignore")
    except Exception as e:
        probe["errors"].append(f"cons {q}: {e}"[:200])
        return 0
    got = 0
    for it in re.findall(r"<item>(.*?)</item>", xml, flags=re.S):
        t = re.search(r"<title>(.*?)</title>", it, flags=re.S)
        pd = re.search(r"<pubDate>(.*?)</pubDate>", it, flags=re.S)
        if not t:
            continue
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).replace("&amp;", "&").strip()
        body = re.split(r"\s[-－]\s|｜", title)[0]
        try:
            d = parsedate_to_datetime(pd.group(1)).astimezone(TW).strftime("%Y%m%d") if pd else ymd(datetime.now(TW).date())
        except Exception:
            d = ymd(datetime.now(TW).date())
        me = RE_EPS.search(body)
        mt = RE_TP.search(body)
        code = (me or mt).group(1) if (me or mt) else None
        if not code or code not in names:
            continue
        eps = float(me.group(3)) if me else None
        dr = {"上修": "up", "下修": "down"}.get(me.group(2), "flat") if me and me.group(2) else None
        tp = None
        m2 = RE_TP2.search(body)
        if m2:
            tp = float(m2.group(1))
        elif mt:
            tp = float(mt.group(2))
        lst = cons.setdefault(code, [])
        rec = [d, eps, dr, tp, body[:120]]
        if not any(x[0] == d and x[1] == eps and x[3] == tp for x in lst):
            lst.append(rec)
            got += 1
    return got


def update_consensus(cons: dict, names: dict):
    meta = cons.setdefault("_meta", {})
    data = cons.setdefault("data", {})
    got = 0
    for q in CONS_Q:
        got += _cons_fetch(f"{q} when:7d", data, names)
    if not meta.get("backfilled"):
        today = datetime.now(TW).date().replace(day=1)
        for i in range(24, 0, -1):
            a = (today - timedelta(days=31 * i)).replace(day=1)
            b2 = (a + timedelta(days=32)).replace(day=1)
            for q in CONS_Q[:2]:
                got += _cons_fetch(f"{q} after:{a.isoformat()} before:{b2.isoformat()}", data, names)
        meta["backfilled"] = ymd(datetime.now(TW).date())
    cut = ymd(datetime.now(TW).date() - timedelta(days=800))
    for c in list(data):
        data[c] = sorted((x for x in data[c] if x[0] >= cut), key=lambda x: x[0])   # 只依日期排（同日其他欄位可能是 None）
        if not data[c]:
            del data[c]
    probe["calls"]["consensus"] = {"new": got, "companies": len(data),
                                   "records": sum(len(v) for v in data.values())}


def consensus_metrics(lst: list, today: str):
    """近 90 天：共識 EPS 上修／下修次數、EPS 變化幅度；最新目標價"""
    if not lst:
        return {}
    cut = ymd(datetime.strptime(today, "%Y%m%d").date() - timedelta(days=90))
    rec = [x for x in lst if x[0] >= cut]
    eps = [x for x in lst if x[1] is not None]
    old = [x for x in eps if x[0] <= cut] or eps[:1]
    out = {"cons_up": sum(1 for x in rec if x[2] == "up") or None, "cons_dn": sum(1 for x in rec if x[2] == "down") or None}
    if eps:
        out["cons_eps"], out["cons_d"] = eps[-1][1], eps[-1][0]
        if old and old[-1][1] and old[-1][1] > 0 and eps[-1][0] != old[-1][0]:
            out["cons_chg"] = round((eps[-1][1] / old[-1][1] - 1) * 100, 1)   # 近 90 天共識 EPS 變化 %
    tps = [x for x in lst if x[3]]
    if tps:
        out["cons_tp"] = tps[-1][3]
    return out


def theme_stats(themes: dict, stocks: list, qf_latest):
    """趨勢 → 環節：用營收成長、創新高比例、動能、預估上修比例排出哪一環最強；每環龍頭 = 市值最大"""
    S_ = {x["c"]: x for x in stocks}
    shares = {c: v[1] for c, v in ((qf_latest or {}).get("data") or {}).items() if v and len(v) > 1 and v[1]}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    out = {}
    for th, segs in themes.items():
        if th.startswith("_"):
            continue
        rows = []
        for seg, codes in segs.items():
            mem = [S_[c] for c in codes if c in S_]
            if not mem:
                continue
            g = lambda k: [x["m"][k] for x in mem if x["m"].get(k) is not None]
            for x in mem:
                if x["c"] in shares and x["m"].get("p"):
                    x["m"]["cap"] = round(shares[x["c"]] * x["m"]["p"])
            lead = max(mem, key=lambda x: x["m"].get("cap") or (x["m"].get("avgval") or 0) * 1000)
            rh, up = g("rev_hi"), g("rev_up_n")
            rows.append({"seg": seg, "n": len(mem), "codes": [x["c"] for x in mem], "lead": lead["c"],
                         "rev3": med(g("rev3")), "accel": med(g("accel")), "rev_rev3": med(g("rev_rev3")),
                         "hi_pct": round(sum(x >= 0 for x in rh) / len(rh) * 100) if rh else None,
                         "up_pct": round(sum(x >= 2 for x in up) / len(up) * 100) if up else None,
                         "score": round(sum(x["s"]["score"] for x in mem if x["s"].get("score") is not None) /
                                        max(1, sum(1 for x in mem if x["s"].get("score") is not None)), 1),
                         "b60": med(g("b60"))})
        # 環節分數：四項各自在環節間排名後加權
        keys = {"rev3": .30, "hi_pct": .25, "accel": .20, "up_pct": .25}
        rk = {k: pct_rank({r["seg"]: r[k] for r in rows}) for k in keys}
        for r in rows:
            parts = [(rk[k][r["seg"]], w) for k, w in keys.items() if r["seg"] in rk[k]]
            tw = sum(w for _, w in parts)
            r["seg_score"] = round(sum(v * w for v, w in parts) / tw * 100, 1) if tw else None
        rows.sort(key=lambda r: -(r["seg_score"] or 0))
        out[th] = rows
    return out


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


def fetch_mops_revenue(roc_y: int, m: int, mk: str = "sii"):
    """公開資訊觀測站：某月營收彙總（一次一個月、全部公司）。mk = sii 上市 / otc 上櫃"""
    html = None
    for host in ("mopsov.twse.com.tw", "mops.twse.com.tw"):  # 觀測站改版後舊頁面搬到 mopsov
        for suffix in ("_0.html", ".html"):
            url = f"https://{host}/nas/t21/{mk}/t21sc03_{roc_y}_{m}{suffix}"
            try:
                time.sleep(2)
                r = S.get(url, timeout=40)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                html = r.content.decode("cp950", errors="ignore")
                break
            except Exception as e:
                probe["errors"].append(f"mops {url}: {e}"[:200])
        if html:
            break
    if not html:
        return None
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        tds = [re.sub(r"<[^>]+>|&nbsp;", "", x).strip()
               for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(tds) >= 7 and re.fullmatch(r"\d{4}", tds[0]):
            out[tds[0]] = [num(tds[2]), num(tds[6])]
    probe["calls"].setdefault("mops_" + mk, {"ok": bool(out), "n": len(out), "url": url})
    return out


def rev_months_have(hist: dict, mk: str):
    """某市場已經有哪些月份的營收（同一個月至少 50 家才算有）"""
    cnt = {}
    for code, h in hist.items():
        if (code in OTC_CODES) == (mk == "otc"):
            for k in h:
                cnt[k] = cnt.get(k, 0) + 1
    return {k for k, n in cnt.items() if n >= 50}


def backfill_revenue(fund: dict):
    hist = fund.setdefault("rev_hist", {})
    today = datetime.now(TW).date()
    for mk in ("sii", "otc"):
        have = rev_months_have(hist, mk)
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
            res = fetch_mops_revenue(yy - 1911, mm, mk)
            if not res:
                fails += 1
                if fails >= 2:
                    break
                continue
            for code, v in res.items():
                hist.setdefault(code, {})[key] = v
            log(f"  月營收 {mk} {key} ok ({len(res)})")


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
    prev = [h[k][0] for k in (shift(last, i) for i in range(1, 13)) if k in h and h[k][0]]
    if len(prev) >= 10 and h[last][0]:
        out["rev_hi"] = round((h[last][0] / max(prev) - 1) * 100, 2)   # 最新月營收距前 12 個月最高 %
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


def fund_rows(kind: str, suf: str = ""):
    """上市（證交所 OpenAPI）＋上櫃（櫃買 OpenAPI）合併"""
    tw = {"rev": "opendata/t187ap05_L", "inc": f"opendata/t187ap06_L_{suf}", "bal": f"opendata/t187ap07_L_{suf}"}[kind]
    tp = {"rev": "mopsfin_t187ap05_O", "inc": f"mopsfin_t187ap06_O_{suf}", "bal": f"mopsfin_t187ap07_O_{suf}"}[kind]
    rows = list(openapi(tw) or [])
    js = get_json("https://www.tpex.org.tw/openapi/v1/" + tp, tries=1)
    if isinstance(js, list):
        record_probe("tpex:" + tp, js)
        rows += js
    return rows


def fetch_fundamentals(old):
    fund = dict(old) if old else {}
    # 月營收
    rev = fund_rows("rev")
    if rev:
        m = {}
        for r in rev:
            code = str(pick(r, "公司代號", "SecuritiesCompanyCode") or "").strip()
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
        fund["revenue"] = {**fund.get("revenue", {}), **m}
        hist = fund.setdefault("rev_hist", {})
        for code, r in m.items():
            if r["ym"] and r["rev"] is not None:
                hist.setdefault(code, {})[str(r["ym"])] = [r["rev"], r["rev_yoy"]]
    # 綜合損益表（各產業別格式）
    inc = {}
    for suf in ["ci", "basi", "bd", "fh", "ins", "mim"]:
        rows = fund_rows("inc", suf)
        for r in rows or []:
            code = str(pick(r, "公司代號", "SecuritiesCompanyCode") or "").strip()
            if not code:
                continue
            revenue = num(pick(r, "營業收入", exclude=("營業收入淨額以外",)))
            gross = num(pick(r, "營業毛利（毛損）淨額", "營業毛利（毛損）", "營業毛利"))
            op = num(pick(r, "營業利益", exclude=("率",)))
            yq = f"{pick(r, '年度', 'Year')}Q{int(num(pick(r, '季別', 'Season')) or 0)}"
            if revenue:
                fund.setdefault("inc_hist", {}).setdefault(code, {})[yq] = [revenue, op, gross]
            inc[code] = {
                "rev": revenue, "op": op,
                "y": pick(r, "年度", "Year"), "q": num(pick(r, "季別", "Season")),
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
        rows = fund_rows("bal", suf)
        for r in rows or []:
            code = str(pick(r, "公司代號", "SecuritiesCompanyCode") or "").strip()
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


def update_pe_monthly(pe: dict, otc_nd: set | None = None):
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
    # 上櫃：同一個月另外取樣一次，合併進去
    otc_nd = otc_nd if otc_nd is not None else set()
    for (y, m) in months:
        key = f"{y}{m:02d}"
        if key not in pe or key in otc_nd or has_otc(pe[key]):
            continue
        st = "nodata"
        for day in (15, 16, 17, 18, 14, 13, 19, 20, 21, 22, 23, 24, 25, 26, 12, 11, 10):
            d = date(y, m, day)
            if d.weekday() >= 5:
                continue
            res, st = fetch_bwibbu_otc(d)
            if st == "ok":
                pr, st2 = fetch_prices_otc(d)
                if st2 != "ok":
                    st = st2
                    break
                closes = {c: v[0] for c, v in pr[0].items()}
                pe[key].update({c: [v[0], closes.get(c)] for c, v in res.items() if v[0]})
                log(f"  上櫃 PE {key} ok ({len(res)})")
                break
            if st == "error":
                break
        if st == "error":
            break
        if st == "nodata":
            otc_nd.add(key)
    keep = {f"{y}{m:02d}" for (y, m) in months}
    for k in list(pe):
        if k not in keep:
            del pe[k]


def after_split(ser):
    """台股單日漲跌幅限制 10%，單日變動超過 ±15% 視為分割/減資等事件，只保留事件之後的資料"""
    cut = 0
    for i in range(1, len(ser)):
        a, b = ser[i - 1][0], ser[i][0]
        if a and b and not (0.85 <= b / a <= 1.15):
            cut = i
    return ser[cut:]


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
    # 波段低點：前後各 10 日內最低，且之後至少 5 天確認（太短的小波動不算，避免雜訊）
    swings = [i for i in range(1, n - 5) if lows[i] == min(lows[max(0, i - 10):i + 11])]
    # 剛形成的低點：近 10 日最低、且已反彈 3% 以上，也算新的前低
    j = min(range(n - 10, n), key=lambda k: lows[k])
    if j < n - 1 and (not swings or j > swings[-1]) and p >= lows[j] * 1.03             and lows[j] == min(lows[max(0, j - 10):]):
        swings.append(j)
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


def build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores, tdcc=None, qf_latest=None,
                 news=None, confs=None, themes=None, consensus=None):
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
    est_cache = {}
    riv_cache = {}
    otc_now = set(OTC_LATEST["codes"]) or OTC_CODES
    for code, row in prices[last].items():
        close = row[0]
        if not is_stock(code):
            continue
        ser = after_split([prices[d][code] for d in pdates if code in prices[d]])
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
        if len(closes) >= 20:
            m["b20"] = round((closes[-1] / (sum(closes[-20:]) / 20) - 1) * 100, 2)
        m["over"] = is_over(m)
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
        rv = revision_metrics(fund.get("rev_hist", {}).get(code, {}))
        est_cache[code] = rv.pop("est_hist", None)
        m.update(rv)
        ih = (fund.get("inc_hist") or {}).get(code) or {}
        if ih:
            ks = sorted(ih)
            a = ih[ks[-1]]
            if a[0] and a[1] is not None:
                m["opm"] = round(a[1] / a[0] * 100, 2)          # 營業利益率（今年累計）
                m["opm_q"] = ks[-1]
                if len(ks) >= 2 and ih[ks[-2]][0] and ih[ks[-2]][1] is not None:
                    m["opm_chg"] = round(m["opm"] - ih[ks[-2]][1] / ih[ks[-2]][0] * 100, 2)
        i = inc.get(code)
        if i:
            m["eps"], m["eps_q"], m["gm"] = i["eps"], i["q"], i["gm"]
            m["eps_y"] = i["y"]
            if i.get("ni") is not None and eqt.get(code) and i.get("q"):
                m["roe"] = round(i["ni"] / eqt[code] * 4 / i["q"] * 100, 2)
        v = bw.get(code)
        if v:
            m["pe"], m["yld"], m["pb"] = v[:3]
        q = ((qf_latest or {}).get("data") or {}).get(code)
        if q and q[0] is not None:
            m["qf"] = q[0]
        hist_pe = None
        if m.get("pe"):
            hist = [(pe_monthly[k].get(code) or [None])[0] for k in sorted(pe_monthly)[-PE_POS_MONTHS:]]
            hist = [h for h in hist if h]
            if len(hist) >= 24:
                hist_pe = hist
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
        # 預估本益比（Forward PE）：近四季 EPS ×（1 + 預估成長率），成長率 = 近 3 月與近 12 月營收年增平均
        # 假設利潤率不變；沒有法人預估資料，這是用營收動能推估
        gs = [x for x in (m.get("rev3"), m.get("rev12")) if x is not None]
        ind = industry(code, rev)
        if m.get("pe") and m["pe"] > 0 and gs and ind not in FWD_SKIP_IND:
            g = max(-50.0, min(80.0, sum(gs) / len(gs)))
            feps = m["p"] / m["pe"] * (1 + g / 100)
            if feps > 0:
                m["fg"], m["feps"], m["fpe"] = round(g, 1), round(feps, 2), round(m["p"] / feps, 2)
                if hist_pe:
                    m["fpe_pos"] = round(sum(1 for h in hist_pe if h < m["fpe"]) / len(hist_pe) * 100, 1)
        if m.get("pe") and m["pe"] > 0 and m.get("rev3") is not None:
            m["peg"] = round(m["rev3"] / m["pe"], 3)       # 成長性價比：近 3 月營收年增 ÷ 本益比
        # 第二步：本益比位階（標準差）。歷史本益比取對數後算平均、標準差。
        # 位階用「目前本益比 vs 歷史本益比」（同樣是近四季 EPS，才公平）；預估本益比的位階另列參考
        # （成長股的預估本益比一定比歷史低，直接拿來比會全部顯示便宜）
        if hist_pe and m.get("pe") and m["pe"] > 0:
            import math as _m
            for lab, n in (("", 60), ("3", 36)):
                hp = [_m.log(x) for x in hist_pe[-n:] if x and x > 0]
                if len(hp) >= 24:
                    mu = sum(hp) / len(hp)
                    sd = (sum((x - mu) ** 2 for x in hp) / len(hp)) ** 0.5
                    if sd > 0:
                        m["pe_z" + lab] = round((_m.log(m["pe"]) - mu) / sd, 2)
                        if lab == "":
                            riv_cache[code] = (mu, sd)
                            m["pe_pct"] = round(sum(1 for x in hp if x < _m.log(m["pe"])) / len(hp) * 100)
                            m["pe_mean"] = round(_m.exp(mu), 1)
                            if m.get("fpe"):
                                m["fpe_z"] = round((_m.log(m["fpe"]) - mu) / sd, 2)
            z = m.get("pe_z")
            if z is not None:
                m["pe_zone"] = "cheap" if z < -0.5 else "rich" if z > 0.5 else "fair"
        pos = m.get("fpe_pos", m.get("pe_pos"))
        if pos is not None:
            m["val_cheap"] = -pos
        # 大戶持股（集保，每週）：參考用，資料累積夠了再驗證預測力
        if tdcc:
            tk = sorted(tdcc)
            cur = tdcc[tk[-1]].get(code)
            if cur:
                m["big400"], m["big1000"] = cur[0], cur[1]
                old = tdcc[tk[-5]].get(code) if len(tk) >= 5 else None
                if old:
                    m["big400_chg"] = round(cur[0] - old[0], 2)
        nw = (news or {}).get(code) or []
        cut30 = ymd(datetime.now(TW).date() - timedelta(days=30))
        m["news_up"] = sum(1 for x in nw if x[0] >= cut30 and x[1] == "up") or None
        m.update({k: v for k, v in consensus_metrics(((consensus or {}).get("data") or {}).get(code, []), last).items() if v is not None})
        m["news_dn"] = sum(1 for x in nw if x[0] >= cut30 and x[1] == "down") or None
        cf = [x for x in ((confs or {}).get(code) or []) if x[0] >= ymd(datetime.now(TW).date())]
        if cf:
            m["conf_next"] = cf[0][0]
        m["liquid"] = avg_val >= MIN_AVG_VALUE
        metrics[code] = m

    # ETF：沒有營收/本益比，不評分，只整理價格、量能與法人
    etfs = []
    for code, row in prices[last].items():
        if not re.fullmatch(r"00\d{2,4}[A-Z]?", code):
            continue
        ser = [prices[d][code] for d in pdates if code in prices[d]]
        closes = [x[0] for x in ser]
        if len(closes) < 2:
            continue
        e = {"c": code, "n": names.get(code, ""), "p": closes[-1], "mk": "o" if code in otc_now else "t",
             "chg": round((closes[-1] / closes[-2] - 1) * 100, 2),
             "avgval": round(sum(x[2] for x in ser[-20:]) / len(ser[-20:]))}
        if len(closes) >= 21:
            e["r20"] = round((closes[-1] / closes[-21] - 1) * 100, 2)
        if len(closes) >= 61:
            e["r60"] = round((closes[-1] / closes[-61] - 1) * 100, 2)
        if len(closes) >= 121:
            e["r120"] = round((closes[-1] / closes[-121] - 1) * 100, 2)
        if len(closes) >= 40:
            n60 = closes[-60:]
            e["b60"] = round((closes[-1] / (sum(n60) / len(n60)) - 1) * 100, 2)
        vol = sum(prices[d][code][1] for d in tdates[-20:] if code in prices.get(d, {}))
        if vol:
            e["inst20"] = round(sum(sum(t86[d].get(code, [0, 0, 0])) for d in tdates[-20:]) / vol * 100, 2)
        v = bw.get(code)
        if v and v[1]:
            e["yld"] = v[1]
        nm = e["n"]
        e["kind"] = ("債券" if "債" in nm else "槓桿/反向" if any(x in nm for x in ("正2", "反1", "反向", "槓桿"))
                     else "高股息" if any(x in nm for x in ("高息", "高股息", "股息", "收益")) else "股票型")
        etfs.append(e)
    etfs.sort(key=lambda e: -e["avgval"])

    uni = {c: m for c, m in metrics.items() if m["liquid"]}
    keys = set(FUND_W) | set(CHIP_W) | set(TECH_W) | set(VAL_W) | {"avgval", "peg"}
    ranks = {k: pct_rank({c: m.get(k) for c, m in uni.items()}) for k in keys}

    stocks = []
    for code, m in metrics.items():
        sc = {}
        if m["liquid"]:
            sc["fund"] = combine(ranks, FUND_W, code)
            sc["chip"] = combine(ranks, CHIP_W, code)
            sc["tech"] = combine(ranks, TECH_W, code)
            vparts = []
            if m.get("val_cheap") is not None:
                vparts.append((100 + m["val_cheap"], VAL_W["val_cheap"]))
            if ranks["yld"].get(code) is not None:
                vparts.append((ranks["yld"][code] * 100, VAL_W["yld"]))
            vw = sum(w for _, w in vparts)
            sc["value"] = round(sum(v * w for v, w in vparts) / vw, 1) if vw >= 0.5 else None
            sc["sup"] = m.get("sup_score")
            sc["liq"] = round(ranks["avgval"][code] * 100, 1)
            sc["peg"] = round(ranks["peg"][code] * 100, 1) if code in ranks["peg"] else None
            m["rules"] = rule_hits(m)
            for name, wts in STRATS.items():
                sc[name] = strat_score(sc, m, wts)
            m["pct"] = {k: round(ranks[k][code] * 100) for k in keys if code in ranks[k]}
        stocks.append({"c": code, "n": names.get(code, ""), "ind": industry(code, rev), "mk": "o" if code in otc_now else "t",
                       "s": sc, "m": m})
    # 市值、外資持股 4 週變化（三步驟分數用）
    shares = {c: v[1] for c, v in ((qf_latest or {}).get("data") or {}).items() if v and len(v) > 1 and v[1]}
    qf_old = {}
    if qf_latest and qf_latest.get("date"):
        cut = (datetime.strptime(qf_latest["date"], "%Y%m%d") - timedelta(days=28)).strftime("%Y%m%d")
        for ym in sorted({cut[:6], qf_latest["date"][:6]}):
            for d, rows in (hist_read("qf", ym) if (DATA / "hist").exists() else {}).items():
                if d <= cut:
                    qf_old = rows
    for x in stocks:
        m = x["m"]
        if x["c"] in shares and m.get("p"):
            m["cap"] = round(shares[x["c"]] * m["p"])
        o = qf_old.get(x["c"])
        if m.get("qf") is not None and o and o[0] is not None:
            m["qd4"] = round(m["qf"] - o[0], 2)
    for x in stocks:
        x["s"]["score_bt"] = x["s"].get("score")      # 回測用的資料導向分數（驗證頁）
    step_scores(stocks)
    stocks.sort(key=lambda x: (x["s"].get("score") is None, -(x["s"].get("score") or 0)))

    # 分數歷史 → 5 日分數變化（{日期: {代號: [短線, 長線]}}）
    # 評分公式改版時，舊分數不能拿來比（5 日變化會失真）→ 清掉重新累積
    if scores.get("_v") != SCORE_VER:
        scores.clear()
        scores["_v"] = SCORE_VER
    scores[last] = {x["c"]: x["s"]["score"] for x in stocks if x["s"].get("score") is not None}
    for k in sorted(k for k in scores if k.isdigit())[:-SCORE_KEEP]:
        del scores[k]
    sdates = sorted(k for k in scores if k.isdigit())
    ref = scores[sdates[-6]] if len(sdates) >= 6 else {}
    for x in stocks:
        old = ref.get(x["c"])
        if old is None:
            continue
        if x["s"].get("score") is not None:
            x["s"]["d5"] = round(x["s"]["score"] - old, 1)

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
        if x["ind"] and x["s"].get("score") is not None:
            inds.setdefault(x["ind"], []).append(x)
    industries = []
    for name, lst in inds.items():
        r20 = [x["m"]["r20"] for x in lst if x["m"].get("r20") is not None]
        chg = [x["m"]["chg"] for x in lst if x["m"].get("chg") is not None]
        industries.append({
            "ind": name, "n": len(lst),
            "score": round(sum(x["s"]["score"] for x in lst) / len(lst), 1),
            "cand": sum(1 for x in lst if "long_core" in (x["m"].get("rules") or [])),
            "r20": round(sum(r20) / len(r20), 2) if r20 else None,
            "chg": round(sum(chg) / len(chg), 2) if chg else None,
            "top": [[x["c"], x["n"], x["s"]["score"]] for x in sorted(lst, key=lambda x: -x["s"]["score"])[:3]],
        })
    industries.sort(key=lambda i: -i["score"])

    # 個股明細檔（點開個股才載入）
    sdir = OUT / "s"
    sdir.mkdir(parents=True, exist_ok=True)
    rev_hist = fund.get("rev_hist", {})
    pmonths = sorted(pe_monthly)[-PE_POS_MONTHS:]
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
            if m.get("feps"):
                rm.append("F")
                rp.append(m["p"])
                re_.append(m["feps"])
            det["river"] = {"m": rm, "p": rp, "e": re_, "q": qs}
            if code in riv_cache:
                import math as _m
                mu, sd = riv_cache[code]
                det["river"]["q"] = [round(_m.exp(mu + k * sd), 2) for k in (-1, -0.5, 0, 0.5, 1)]
                det["river"]["z"] = [-1, -0.5, 0, 0.5, 1]
        det["est"] = est_cache.get(code)
        det["news"] = (news or {}).get(code)
        det["confs"] = (confs or {}).get(code)
        det["cons"] = ((consensus or {}).get("data") or {}).get(code)
        sd = [d for d in sdates if code in scores[d]]
        det["sc"] = {"d": sd, "score": [scores[d][code] for d in sd]}
        h = rev_hist.get(code, {})
        det["rev"] = [[k, *h[k]] for k in sorted(h)]
        (sdir / f"{code}.json").write_text(json.dumps(det, ensure_ascii=False, separators=(",", ":")),
                                            encoding="utf-8")

    add_cagr3(stocks, fund)
    add_fwd_z(stocks, pe_monthly, fund)      # 研究版前瞻本益比位階：持股監控「回到 0σ」與大跌名單用
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
        "etfs": etfs,
        "rules": RULES,
        "themes": (th_ := theme_stats(themes or {}, stocks, qf_latest)),
        "groups": (gr_ := theme_stats({"產業": {g: [x["c"] for x in stocks if x["ind"] == g and x["s"].get("score") is not None]
                                                for g in {x["ind"] for x in stocks if x["ind"]}}}, stocks, qf_latest).get("產業", [])),
        "step2": quarter_plan(stocks, prices, last, fund),
        "crash": crash_plan(stocks, prices, last, pe_monthly, fund),
        "mkt_pe": _safe(lambda: market_pe(stocks, pe_monthly, fund), "大盤本益比"),
        "picks": {rid: top_picks([(x["c"], x["m"]) for x in stocks], rid) for rid in RULES},
    }


def sync_hist(prices, t86):
    """每日新資料也寫進 data/hist（回測用的長期歷史）"""
    hist = DATA / "hist"
    if not hist.exists():
        return
    for prefix, store in (("px", prices), ("t86", t86)):
        by_month = {}
        for d, rows in store.items():
            by_month.setdefault(d[:6], {})[d] = rows
        for ym, days in by_month.items():
            cur = hist_read(prefix, ym)
            new = {d: r for d, r in days.items() if (d not in cur or len(r) > len(cur[d]))
                   and (prefix != "px" or len(next(iter(r.values()))) >= 5)}
            if new:
                cur.update(new)
                hist_write(prefix, ym, cur)


# ---------------------------------------------------------------- 第二步：本季名單（研究 3 驗證的做法）
# 條件（Q_RULE 2）：市值 ≥ 100 億、營收預估上修中、外資持股 ≥ 20%、近 3 年營收年複合成長 ≥ 10%；依基本面分數排序，每產業最多 2 檔、最多 20 檔
# 研究 3：三種換股起始月年化 26.9~28.6%（0050 22.1%），2018~23、2023~26 都贏；舊條件（上修 ≥ 3＋創新高）有一種起始月輸
# 每季（3、6、9、12 月 11 日後）換一次；每檔 5%，其餘放 0050；名單與當時價格記在 data/quarter.json，之後可對帳
Q_MONTHS = tuple(range(1, 13))      # 每月 11 日後（新營收公布）檢查一次
Q_MAX, Q_PER_IND, Q_PER_W = 10, 10, 0.10
Q_RULE, Q_QF, Q_CAGR = 4, 30.0, 10.0
# Q_RULE 4（研究 3 網格 42 組：外資門檻 20/30/40 × 每季/每月檢查 × 回落停損 不設~50%）：
# 外資 ≥ 30% 在各種停損設定下都穩定（年化 30~37%、最大回撤約 −40%，比 20% 的 −46~−54% 小）；40% 股票太少反而差。
# 每月檢查「不符合就換」在 30% 門檻下最好（35.6~36.9%）；停損幾 % 差異很小（用前段挑的最佳參數在後段只排 24/42 → 不要細調）。
Q_STOP = 0.30     # 從買進後最高點回落 30% 就提前下車
# Q_RULE 3（研究 3 下車訊號）：換股日只換掉不再符合條件的股票，還符合的續抱；期間內從高點回落 30% 提前下車。
# 同基準回測（10 檔全押個股）：每季重挑 34.0%、不符合才換 34.4%、不符合才換＋回落 30% 停損 36.7%（0050 22.1%），
# 賺 50% 就走 23.2%、永遠不賣 18.8%（有一半的下車是虧超過 30%）


# ---------------------------------------------------------------- 除權息、減資（報酬含股利用）
# data/hist/div.json：{"f": {代號: {yyyymmdd: 還原係數}}, "done": [已補完的年份區段]}
# 還原係數 = 除權息（或減資）前收盤價 ÷ 參考價；當天報酬 = 收盤 ÷ 前一天收盤 × 係數
DIV_FROM = 2007


def _roc_ymd(s):
    """'113年01月04日'、'113/01/04'、'1130104' -> '20240104'"""
    g = re.findall(r"\d+", str(s))
    if len(g) == 1 and len(g[0]) == 7:
        g = [g[0][:3], g[0][3:5], g[0][5:]]
    if len(g) < 3:
        return None
    return f"{int(g[0]) + 1911}{int(g[1]):02d}{int(g[2]):02d}"


def fetch_divs(d0: date, d1: date):
    """區間內的除權息＋減資：{代號: {日期: 係數}}；任何一個來源失敗就回傳 None（下次重試）"""
    out = {}

    def add(rows, i_d, i_c, i_prev, i_ref):
        for r in rows:
            try:
                c, t = str(r[i_c]).strip(), _roc_ymd(r[i_d])
                a, b = num(r[i_prev]), num(r[i_ref])
            except (IndexError, TypeError):
                continue
            if t and a and b and a > 0 and b > 0 and 0.2 < a / b < 20:
                out.setdefault(c, {})
                out[c][t] = round(out[c].get(t, 1.0) * a / b, 6)

    s0, s1 = d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")
    for path in ("exRight/TWT49U", "reducation/TWTAUU"):
        js = get_json(f"https://www.twse.com.tw/rwd/zh/{path}", {"startDate": s0, "endDate": s1, "response": "json"}, rwd=True)
        if not js or str(js.get("stat", "")).upper() != "OK":
            if js and "沒有符合" in str(js.get("stat", "")):
                continue
            return None
        add(js.get("data") or [], 0, 1, 3, 4)
    for path in ("bulletin/exDailyQ", "bulletin/revivt"):
        time.sleep(TPEX_SLEEP)
        js = get_json(f"{TPEX}/{path}", {"startDate": d0.strftime("%Y/%m/%d"), "endDate": d1.strftime("%Y/%m/%d"), "response": "json"})
        if not js or str(js.get("stat", "")).lower() != "ok":
            return None
        for t in js.get("tables") or []:
            add(t.get("data") or [], 0, 1, 3, 4)
    return out


def update_divs():
    p = DATA / "hist" / "div.json"
    st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"f": {}, "done": []}
    today = datetime.now(TW).date()
    jobs = []
    for y in range(DIV_FROM, today.year + 1, 2):        # 兩年一段；今年所在的區段每天重抓（含已公告、即將除權息）
        y1 = min(y + 1, today.year)
        tag = f"{y}-{y1}" if y1 < today.year else None
        if tag not in st["done"]:
            jobs.append((date(y, 1, 1), min(date(y1, 12, 31), today + timedelta(days=10)), tag))
    n = 0
    for a, b, tag in jobs:
        res = fetch_divs(a, b)
        if res is None:
            log(f"  除權息 {a}~{b} 失敗")
            continue
        for c, v in res.items():
            st["f"].setdefault(c, {}).update(v)
            n += len(v)
        if tag:
            st["done"].append(tag)
    if n:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    log(f"  除權息／減資 {n} 筆（累計 {sum(len(v) for v in st['f'].values())} 筆、{len(st['f'])} 檔）")


def load_divs():
    p = DATA / "hist" / "div.json"
    return json.loads(p.read_text(encoding="utf-8")).get("f", {}) if p.exists() else {}


def tr_growth(arr, dates, fac=None):
    """含股利的累積報酬指數：當天報酬 × 還原係數；還原後單日仍超過 ±15%（沒有資料的分割、減資）當天視為 0；
    沒有成交就維持最後值。fac＝{日期: 係數}（load_divs()[代號]）"""
    out, last, cur, pend = [], None, None, 1.0
    fac = fac or {}
    for v, t in zip(arr, dates):
        pend *= fac.get(t, 1.0)                        # 除權息當天沒成交：係數留到下一個有成交的日子
        if v:
            if last is None:
                cur = 1.0
            else:
                r = v / last * pend
                if 0.85 <= r <= 1.15:
                    cur *= r
            last, pend = v, 1.0
        elif last is None:
            pend = 1.0
        out.append(cur)
    return out


_REVH = None


def load_revh(fund):
    """月營收歷史（data/hist/rev.json＋最新），整次執行只讀一次"""
    global _REVH
    if _REVH is None:
        revh = {}
        p = DATA / "hist" / "rev.json"
        if p.exists():
            revh = json.loads(p.read_text(encoding="utf-8"))
        for c, h in fund.get("rev_hist", {}).items():
            revh.setdefault(c, {}).update(h)
        _REVH = revh
    return _REVH


def add_fwd_z(stocks, pe_monthly, fund):
    """前瞻本益比位階 σ（研究版，fpe_zf）：目前本益比 ÷（1 + 營收預估成長），
    和自己過去 5 年每個月「當時的前瞻本益比」比（取對數算平均、標準差）。回測：回到 0σ 就賣，勝率較高"""
    import math as _m
    revh = load_revh(fund)
    rev = fund.get("revenue", {})
    pkeys = sorted(pe_monthly)[-60:]
    fc = {}

    def factor(c, h, rk):
        if (c, rk) not in fc:
            cur = [h.get(_shift(rk, i), [None])[0] for i in range(12)]
            e = est12(h, rk)
            fc[(c, rk)] = e / sum(cur) if e and None not in cur and sum(cur) > 0 else None
        return fc[(c, rk)]

    for x in stocks:
        m, c = x["m"], x["c"]
        h = revh.get(c)
        if not h or not m.get("pe") or m["pe"] <= 0 or industry(c, rev) in FWD_SKIP_IND:
            continue
        fn = factor(c, h, max(h))
        if not fn or fn <= 0:
            continue
        fh = []
        for k in pkeys:
            v = (pe_monthly[k].get(c) or [None])[0]
            f = factor(c, h, _roc_prev(k)) if v and v > 0 else None
            if f and f > 0:
                fh.append(_m.log(v / f))
        if len(fh) < 24:
            continue
        mu = sum(fh) / len(fh)
        sd = (sum((a - mu) ** 2 for a in fh) / len(fh)) ** 0.5
        if sd > 0:
            m["fpe_zf"] = round((_m.log(m["pe"] / fn) - mu) / sd, 2)


def _safe(fn, label):
    """新功能出錯時只記錄、不中斷整體更新"""
    try:
        return fn()
    except Exception:
        import traceback
        log(f"{label} 失敗：" + traceback.format_exc()[-400:])
        return None


def market_pe(stocks, pe_monthly, fund):
    """大盤本益比位階：市值加權（總市值 ÷ 總獲利，排除虧損公司）；前瞻版＝每家獲利 ×（1 + 營收預估成長，限 0.5~2.5 倍）。
    位階＝和過去 60 個月比（取對數）。月資料用 pe_monthly（每月取樣的本益比、股價）× 當月發行股數（外資持股檔）。
    回測 2018~2025：位階高低對 0050 之後 12 個月報酬「沒有」便宜比較好的關係（便宜時 +2%、偏貴時 +31%），只當估值參考"""
    import math as _m
    revh = load_revh(fund)
    rev = fund.get("revenue", {})
    sh_by_m, last_sh = {}, {}
    for ym in hist_months("qf"):
        d = hist_read("qf", ym)
        for t in sorted(d):
            for c, v in d[t].items():
                if v and len(v) > 1 and v[1]:
                    last_sh[c] = v[1]
        sh_by_m[ym] = dict(last_sh)
    if not sh_by_m:
        return None
    sh_keys = sorted(sh_by_m)
    fc, ind = {}, {}

    def factor(c, rk):
        if (c, rk) not in fc:
            h = revh.get(c) or {}
            cur = [h.get(_shift(rk, i), [None])[0] for i in range(12)]
            e = est12(h, rk) if h else None
            f = e / sum(cur) if e and None not in cur and sum(cur) > 0 else None
            fc[(c, rk)] = max(0.5, min(2.5, f)) if f else None
        return fc[(c, rk)]

    def fwd(c, rk):
        if c not in ind:
            ind[c] = industry(c, rev)
        return 1.0 if ind[c] in FWD_SKIP_IND else factor(c, rk)

    def agg(items):
        """items: [(代號, 市值, 本益比, 營收月份)] → (本益比, 前瞻本益比, 檔數)"""
        cap = earn = fcap = fearn = 0.0
        n = 0
        for c, cp, pe, rk in items:
            if not cp or not pe or pe <= 0:
                continue
            e = cp / pe
            cap += cp
            earn += e
            n += 1
            f = fwd(c, rk)
            if f:
                fcap += cp
                fearn += e * f
        return (cap / earn if earn else None, fcap / fearn if fearn else None, n)

    hist = []
    for ym in sorted(pe_monthly):
        k = [x for x in sh_keys if x <= ym]
        sh = sh_by_m[k[-1]] if k else sh_by_m[sh_keys[0]]
        rk = _roc_prev(ym)
        pe, fpe, n = agg([(c, v[1] * sh[c], v[0], rk) for c, v in pe_monthly[ym].items()
                          if v and v[0] and v[1] and c in sh])
        if pe:
            hist.append([ym, round(pe, 2), round(fpe, 2) if fpe else None, n])
    revk = {c: max(h) for c, h in revh.items() if h}
    now = agg([(x["c"], x["m"].get("cap"), x["m"].get("pe"), revk.get(x["c"])) for x in stocks
               if x["m"].get("cap") and x["m"].get("pe")])

    def pos(v, xs):
        xs = [x for x in xs if x]
        if v is None or len(xs) < 24:
            return None, None, None, None
        lx = [_m.log(x) for x in xs]
        mu = sum(lx) / len(lx)
        sd = (sum((a - mu) ** 2 for a in lx) / len(lx)) ** 0.5
        if sd <= 0:
            return None, None, None, None
        return (round((_m.log(v) - mu) / sd, 2), round(sum(1 for x in xs if x < v) / len(xs) * 100),
                round(_m.exp(mu), 2), round(sd, 4))
    cur_ym = datetime.now(TW).strftime("%Y%m")
    past = [h for h in hist if h[0] < cur_ym][-60:]
    z, pct, mean, sd = pos(now[0], [h[1] for h in past])
    fz, fpct, fmean, fsd = pos(now[1], [h[2] for h in past])
    return {"pe": round(now[0], 2) if now[0] else None, "fpe": round(now[1], 2) if now[1] else None, "n": now[2],
            "z": z, "pct": pct, "mean": mean, "sd": sd, "fz": fz, "fpct": fpct, "fmean": fmean, "fsd": fsd,
            "hist": hist[-96:]}


def add_cagr3(stocks, fund):
    """近 3 年營收年複合成長（%）＝近 12 個月營收 vs 36 個月前的近 12 個月營收"""
    revh = load_revh(fund)
    for x in stocks:
        h = revh.get(x["c"])
        if not h:
            continue
        rk = max(h)
        cur = [h.get(_shift(rk, i), [None])[0] for i in range(12)]
        old = [h.get(_shift(rk, i + 36), [None])[0] for i in range(12)]
        if None not in cur + old and sum(old) > 0 and sum(cur) > 0:
            x["m"]["cagr3"] = round(((sum(cur) / sum(old)) ** (1 / 3) - 1) * 100, 1)


def quarter_plan(stocks, prices, last, fund):
    add_cagr3(stocks, fund)
    st = load("quarter.json", {"cur": None, "hist": []})
    if st["cur"] and st["cur"].get("rule") != Q_RULE:      # 規則改版：本期名單重選（已結束的期間保留）
        st["cur"] = None
    pdates = sorted(prices)

    def px(d, c):
        v = (prices.get(d) or {}).get(c)
        return v[0] if v and v[0] else None

    def ret(a, b):
        if not a or not b:
            return None
        r = b / a - 1
        return None if r < -0.6 else r            # 分割（如 0050 1 拆 4）造成的假跌幅不算

    m60 = ret(px(pdates[-61], "0050"), px(last, "0050")) if len(pdates) > 60 else None
    cand = [x for x in stocks if x["s"].get("score") is not None and (x["m"].get("cap") or 0) >= MIN_CAP
            and (x["m"].get("rev_up_n") or 0) >= 1 and (x["m"].get("qf") or 0) >= Q_QF
            and x["m"].get("cagr3") is not None and x["m"]["cagr3"] >= Q_CAGR]
    cand.sort(key=lambda x: -x["s"]["score"])
    qual = [x["c"] for x in cand]
    ind_of = {x["c"]: x["ind"] or "?" for x in stocks}

    def select(keep):
        """keep：上一期還符合條件、沒被停損的股票（續抱）；空位依分數補，每產業最多 Q_PER_IND 檔"""
        pick = list(keep)[:Q_MAX]
        cnt = {}
        for c in pick:
            cnt[ind_of.get(c, "?")] = cnt.get(ind_of.get(c, "?"), 0) + 1
        for c in qual:
            if len(pick) >= Q_MAX:
                break
            g = ind_of.get(c, "?")
            if c in pick or cnt.get(g, 0) >= Q_PER_IND:
                continue
            cnt[g] = cnt.get(g, 0) + 1
            pick.append(c)
        return pick

    d = datetime.strptime(last, "%Y%m%d")
    mo = d.month
    fav, unfav = [], []
    if m60 is not None and m60 > 0.10:
        fav.append(f"0050 近 3 個月漲 {m60 * 100:.0f}%（> 10%）")
    if m60 is not None and 0 <= m60 <= 0.10:
        unfav.append(f"0050 近 3 個月只小漲 {m60 * 100:.0f}%（0~10%）")
    if mo in (12, 1, 2, 3):
        fav.append("12～3 月（歷史上較有利）")
    if mo in (5, 6, 7, 8, 9):
        unfav.append("5～9 月（歷史上較不利）")
    level = "有利" if fav and not unfav else "不利" if unfav and not fav else "普通"
    share = {"有利": 0.8, "普通": 0.5, "不利": 0.25}[level]

    key = f"{d.year}-{mo:02d}"
    due = mo in Q_MONTHS and d.day >= 11 and (not st["cur"] or st["cur"]["key"] != key)
    changed = False
    if not st["cur"] or due:
        prev = st["cur"]
        keep = []
        if prev:
            stopped = prev.get("stopped", {})
            prev["end"] = last
            prev["end_px"] = {c: (stopped[c]["px"] if c in stopped else px(last, c)) for c in prev["codes"]}
            prev["end_0050"] = px(last, "0050")
            st["hist"].append(prev)
            keep = [c for c in prev["codes"] if c in qual and c not in stopped]
        pick = select(keep)
        peak = {c: (prev or {}).get("peak", {}).get(c) or px(last, c) for c in pick}
        entry = {c: ((prev or {}).get("entry", {}).get(c) if c in keep else None) or last for c in pick}
        st["cur"] = {"key": key, "rule": Q_RULE, "start": last, "codes": pick, "px": {c: px(last, c) for c in pick},
                     "p0050": px(last, "0050"), "level": level, "share": share, "peak": peak, "entry": entry, "stopped": {},
                     "added": [c for c in pick if not prev or c not in prev["codes"]],
                     "removed": [c for c in prev["codes"] if c not in pick] if prev else []}
        changed = True
    # 每天更新買進後最高價；回落 30% 就標記提前下車（之後不再算進本期報酬，下次換股不再續抱）
    cur = st["cur"]
    cur.setdefault("peak", {})
    cur.setdefault("stopped", {})
    for c in cur["codes"]:
        p = px(last, c)
        if not p or c in cur["stopped"]:
            continue
        old_pk = cur["peak"].get(c)
        if old_pk and p / old_pk < 0.4:          # 分割等異常：重設
            cur["peak"][c] = p
            continue
        if not old_pk or p > old_pk:
            cur["peak"][c] = p
            changed = True
        elif p <= old_pk * (1 - Q_STOP):
            cur["stopped"][c] = {"d": last, "px": p, "peak": old_pk}
            changed = True
    if changed:
        save("quarter.json", st)

    def perf(q, end_px, end50):
        rs = [ret(q["px"].get(c), end_px.get(c)) for c in q["codes"]]
        rs = [r for r in rs if r is not None]
        r50 = ret(q["p0050"], end50)
        return (round(sum(rs) / len(rs) * 100, 1) if rs else None), (round(r50 * 100, 1) if r50 is not None else None)

    r_basket, r_50 = perf(cur, {c: (cur["stopped"][c]["px"] if c in cur["stopped"] else px(last, c)) for c in cur["codes"]},
                          px(last, "0050"))
    ny, nm = d.year, next((m for m in Q_MONTHS if m > mo or (m == mo and d.day < 11)), None)
    if nm is None:
        ny, nm = ny + 1, Q_MONTHS[0]
    n = len(cur["codes"])
    stock_w = min(share, Q_PER_W * n)
    return {"codes": cur["codes"], "key": cur["key"], "start": cur["start"], "next": f"{ny}/{nm}/11",
            "added": cur.get("added", []), "removed": cur.get("removed", []),
            "level": cur.get("level"), "level_now": level, "fav": fav, "unfav": unfav,
            "m60": round(m60 * 100, 1) if m60 is not None else None,
            "stock_w": round(stock_w * 100), "per_w": round(stock_w / n * 100, 1) if n else 0,
            "ret": r_basket, "ret0050": r_50, "live": qual,
            "stopped": {c: v for c, v in cur["stopped"].items()},
            "dd": {c: round((px(last, c) / cur["peak"][c] - 1) * 100, 1) for c in cur["codes"]
                   if px(last, c) and cur["peak"].get(c)},
            "entry": cur.get("entry", {}),
            "hist": [dict(zip(("key", "start", "end", "ret", "ret0050", "n"),
                              (h["key"], h["start"], h["end"], *perf(h, h["end_px"], h["end_0050"]), len(h["codes"]))))
                     for h in st["hist"]]}


# ---------------------------------------------------------------- 大跌加碼名單（scripts/research_crash.py：2018~2026 五次大跌）
# 0050 距一年高點跌超過 15% 時啟用；候選：營收預估上修中、市值 ≥ 100 億；
# 依外資持股 30%＋近 3 年營收成長 30%＋大跌前一年 beta 40% 排序取前 10。
# 回測持有 12 個月：能算 beta 的 4 次大跌都贏 0050（最差一次 +12.5%）；舊版（前瞻本益比 < −0.5σ 篩選）只有 3/5。
# 大跌期間抗跌的、本益比便宜的、低 beta 的，之後反而落後 → 前瞻本益比位階只顯示、不篩選。
CRASH_DD = -0.15
CRASH_W = {"qf": 0.3, "cagr3": 0.3, "beta": 0.4}
TRIO_QF, TRIO_CAGR = 30.0, 10.0      # 三檔組合候選：外資持股 ≥ 30%、近 3 年營收成長 ≥ 10%（%）


def _roc_prev(ym: str) -> str:
    """西元年月 → 當月月底已公布的最新月營收（上個月，民國年月）"""
    y, m = int(ym[:4]), int(ym[4:]) - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y - 1911}{m:02d}"


def crash_plan(stocks, prices, last, pe_monthly, fund):
    import math as _m
    # 近兩年日線：一年高點用來算跌幅；高點之前一年用來算 beta
    d0 = datetime.strptime(last, "%Y%m%d")
    yms = sorted({(d0 - timedelta(days=30 * i)).strftime("%Y%m") for i in range(26)})
    seen = {}
    if (DATA / "hist").exists():
        for ym in yms:
            seen.update(hist_read("px", ym))
    seen.update(prices)
    ds = sorted(seen)
    e50 = [(seen[d].get("0050") or [None])[0] for d in ds]
    for i in range(1, len(e50)):                 # 缺值往前補
        if not e50[i]:
            e50[i] = e50[i - 1]
    for i in range(1, len(e50)):                 # 分割（1 拆 N）時把之前的價格換算
        if e50[i - 1] and e50[i] and e50[i] / e50[i - 1] < 0.5:
            k = e50[i - 1] / e50[i]
            e50[:i] = [x / k if x else x for x in e50[:i]]
    cut = (d0 - timedelta(days=365)).strftime("%Y%m%d")
    win = [i for i, d in enumerate(ds) if d >= cut and e50[i]]
    dd, pk = None, None
    if win:
        pk = max(win, key=lambda i: e50[i])
        dd = e50[win[-1]] / e50[pk] - 1

    def beta_of(c):
        """高點之前 250 個交易日，個股對 0050 的 beta（單日 ±15% 視為分割／減資，略過）"""
        if pk is None:
            return None
        ra, rm = [], []
        for i in range(max(1, pk - 249), pk + 1):
            a0, a1 = (seen[ds[i - 1]].get(c) or [None])[0], (seen[ds[i]].get(c) or [None])[0]
            if a0 and a1 and e50[i - 1] and e50[i] and 0.85 <= a1 / a0 <= 1.15:
                ra.append(a1 / a0 - 1)
                rm.append(e50[i] / e50[i - 1] - 1)
        if len(ra) < 120:
            return None
        ma, mm = sum(ra) / len(ra), sum(rm) / len(rm)
        vm = sum((y - mm) ** 2 for y in rm)
        return sum((x - ma) * (y - mm) for x, y in zip(ra, rm)) / vm if vm else None

    # 3 年成長（cagr3）與前瞻本益比位階（fpe_zf）已由 add_cagr3 / add_fwd_z 算好
    rows = []
    for x in stocks:
        m, c = x["m"], x["c"]
        if (m.get("cap") or 0) < MIN_CAP or (m.get("rev_up_n") or 0) < 1:
            continue
        bt = beta_of(c)
        m["crash_beta"] = round(bt, 2) if bt is not None else None
        rows.append(x)
    # 排序：外資持股 30%＋近 3 年營收成長 30%＋beta 40%（候選內百分位；缺資料的項目不計，至少要有一半權重）
    keymap = {"qf": "qf", "cagr3": "cagr3", "beta": "crash_beta"}

    def score(pool):
        def pr(k):
            v = sorted(x["m"][k] for x in pool if x["m"].get(k) is not None)
            return {x["c"]: (sum(1 for a in v if a < x["m"][k]) + sum(1 for a in v if a <= x["m"][k]) - 1) / 2 / max(1, len(v) - 1)
                    for x in pool if x["m"].get(k) is not None}
        rk_ = {w: pr(keymap[w]) for w in CRASH_W}
        out = {}
        for x in pool:
            parts = [(rk_[w][x["c"]], wt) for w, wt in CRASH_W.items() if x["c"] in rk_[w]]
            tw = sum(wt for _, wt in parts)
            if tw >= 0.5:
                out[x["c"]] = round(sum(v * wt for v, wt in parts) / tw * 100)
        return out

    sc = score(rows)
    for x in rows:
        x["m"]["crash_sc"] = sc.get(x["c"])
    rows = [x for x in rows if x["m"]["crash_sc"] is not None]
    rows.sort(key=lambda x: -x["m"]["crash_sc"])
    # 三檔組合（research_winrate.py）：0050 跌破半年線時，從「選股名單條件＋營收創新高」依同樣排序（在這個候選池內重排）取前 3，
    # 賺 20% 就轉 0050、沒到就抱 12 個月。回測 15 個月份（2019~2025、跨 5 年）組合贏 0050 93%、平均超額 +10%、最差 −5.6%；
    # 0050 在半年線上時同規則只有 62% 且前後段不穩，所以只在半年線下啟用
    ma120 = sum(e50[-120:]) / 120 if len(e50) >= 120 and all(e50[-120:]) else None
    below = ma120 is not None and e50[-1] < ma120
    tpool = [x for x in rows if (x["m"].get("qf") or 0) >= TRIO_QF and x["m"].get("cagr3") is not None
             and x["m"]["cagr3"] >= TRIO_CAGR and x["m"].get("rev_hi") is not None and x["m"]["rev_hi"] >= 0]
    tsc = score(tpool)
    for x in tpool:
        x["m"]["trio_sc"] = tsc.get(x["c"])
    tpool = sorted([x for x in tpool if x["m"].get("trio_sc") is not None], key=lambda x: -x["m"]["trio_sc"])
    return {"dd": round(dd * 100, 1) if dd is not None else None, "active": dd is not None and dd <= CRASH_DD,
            "peak": ds[pk] if pk is not None else None,
            "n": len(rows), "codes": [x["c"] for x in rows[:10]], "all": [x["c"] for x in rows],
            "rows": [{"c": x["c"], "sc": x["m"]["crash_sc"], "zf": x["m"].get("fpe_zf"), "qf": x["m"].get("qf"),
                      "cagr3": x["m"].get("cagr3"), "beta": x["m"]["crash_beta"], "cap": round((x["m"].get("cap") or 0) / 1e8),
                      "up": x["m"].get("rev_up_n")}
                     for x in rows[:10]],
            "trio": (trio := {"active": below, "p50": round(e50[-1], 2), "ma120": round(ma120, 2) if ma120 else None,
                              "gap": round((e50[-1] / ma120 - 1) * 100, 1) if ma120 else None,
                              "n": len(tpool), "codes": [x["c"] for x in tpool[:3]], "all": [x["c"] for x in tpool],
                              "log": _safe(lambda: trio_track(below, [x["c"] for x in tpool[:3]], seen, ds), "三檔組合記錄")})}


# ---------------------------------------------------------------- 三檔組合實際記錄（驗證回測的 93%）
# 每個月第一次啟用時，把名單與收盤價存進 data/trio_log.json；之後每天用含股利的報酬追蹤：
# 個股賺 20% 就當天換成 0050（超額報酬凍結、扣 0.3% 換股成本），沒到就抱滿 250 個交易日（約 12 個月）。
TRIO_TP, TRIO_DAYS, TRIO_COST = 0.20, 250, 0.003


def trio_track(active, codes, seen, ds):
    st = load("trio_log.json", {"rounds": []})
    last = ds[-1]
    px = lambda d, c: ((seen.get(d) or {}).get(c) or [None])[0]
    ym = last[:6]
    # 當天上櫃收盤還沒抓到（下午提早執行）就先不記，避免漏掉上櫃股；同一天的記錄以最後一次執行為準
    full = has_otc(seen.get(last) or {})
    if full:
        st["rounds"] = [r for r in st["rounds"] if r["start"] != last]
    if active and codes and full and px(last, "0050") and not any(r["start"][:6] == ym for r in st["rounds"]):
        # 回測用每月 11 日後第一個交易日；不是那天開始的（月中才跌破半年線）另外標記
        sched = int(last[6:]) >= 11 and not any(d[:6] == ym and int(d[6:]) >= 11 for d in ds[:-1])
        st["rounds"].append({"start": last, "codes": codes, "px": {c: px(last, c) for c in codes},
                             "p0050": px(last, "0050"), "sched": sched, "done": False, "res": {}})
        log(f"  三檔組合新記錄 {last}：{codes}")
    divs = load_divs()
    for r in st["rounds"]:
        if r["done"] or r["start"] not in ds:
            continue
        i0 = ds.index(r["start"])
        dts = ds[i0:]
        n = len(dts) - 1
        g50 = tr_growth([px(d, "0050") for d in dts], dts, divs.get("0050"))
        res = {}
        for c in r["codes"]:
            g = tr_growth([px(d, c) for d in dts], dts, divs.get(c))
            if not g or g[0] is None:
                continue
            hit = next((k for k in range(1, min(n, TRIO_DAYS) + 1) if g[k] and g[k] >= 1 + TRIO_TP), None)
            k = hit if hit is not None else min(n, TRIO_DAYS)
            rel = g[k] / g50[k] * (1 - (TRIO_COST if hit is not None else 0))
            res[c] = {"d": dts[k], "r": round((g[k] - 1) * 100, 1), "r50": round((g50[k] - 1) * 100, 1),
                      "ex": round((rel - 1) * 100, 1),
                      "why": "賺 20% 換 0050" if hit is not None else ("滿 12 個月" if n >= TRIO_DAYS else None)}
        r["res"] = res
        r["days"] = n
        if res:
            r["ex"] = round(sum(v["ex"] for v in res.values()) / len(res), 1)
        r["done"] = n >= TRIO_DAYS or (len(res) == len(r["codes"]) and all(v["why"] for v in res.values()))
    save("trio_log.json", st)
    fin = [r for r in st["rounds"] if r["done"] and r.get("ex") is not None]
    return {"rounds": st["rounds"][-24:], "n_done": len(fin), "n_win": sum(r["ex"] > 0 for r in fin)}


def _shift(k, n):
    y, m = int(k[:-2]), int(k[-2:]) - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


# ---------------------------------------------------------------- main
def main():
    prices = load("prices.json", {})
    names = load("names.json", {})
    t86 = load("t86.json", {})
    pe_monthly = load("pe_monthly.json", {})
    bw_latest = load("bwibbu_latest.json", {})
    qf_latest = load("qfii_latest.json", {})
    meta = load("meta.json", {"nodata_prices": [], "nodata_t86": []})
    OTC_CODES.update(meta.get("otc_codes", []))
    OTC_LATEST.update(meta.get("otc_latest") or {})
    otc_nd = {k: set(meta.get("nodata_otc_" + k, [])) for k in ("px", "t86", "pe")}
    fund = load("fundamentals.json", {})
    index = load("index.json", {})
    scores = load("scores.json", {})
    tdcc = load("tdcc.json", {})

    log("== 收盤行情")
    meta["nodata_prices"], n1 = update_daily(prices, meta["nodata_prices"], fetch_prices,
                                             PRICE_KEEP, 200, "price", names)
    log("== 三大法人")
    meta["nodata_t86"], n2 = update_daily(t86, meta["nodata_t86"], fetch_t86, T86_KEEP, 40, "t86")
    log("== 上櫃收盤行情、三大法人")
    merge_otc(prices, fetch_prices_otc, "price", otc_nd["px"], names)
    merge_otc(t86, fetch_t86_otc, "t86", otc_nd["t86"])
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
    if bw_latest.get("date") and not has_otc(bw_latest.get("data", {})):
        res, st = fetch_bwibbu_otc(datetime.strptime(bw_latest["date"], "%Y%m%d").date())
        if st == "ok":
            bw_latest["data"].update(res)
            log(f"  上櫃本益比 {bw_latest['date']} ok ({len(res)})")
    log("== 外資持股比例")
    if prices:
        _TWSE_CODES[:] = [c for c in prices[max(prices)] if c not in OTC_CODES]
    tw_n = sum(1 for c in (qf_latest.get("data") or {}) if c in set(_TWSE_CODES))
    if prices and (qf_latest.get("date") != max(prices) or tw_n < 500):
        for k in sorted(prices, reverse=True)[:3]:
            if qf_latest.get("date") and qf_latest["date"] > k and tw_n >= 500:
                break
            res = fetch_qfii(datetime.strptime(k, "%Y%m%d").date())
            if res:
                qf_latest = {"date": k, "data": res}
                cur = hist_read("qf", k[:6]) if (DATA / "hist").exists() else None
                if cur is not None:
                    cur[k] = res
                    hist_write("qf", k[:6], cur)
                log(f"  外資持股 {k} ok ({len(res)})")
                break
    save("qfii_latest.json", qf_latest)
    log("== 本益比月取樣（位階用）")
    update_pe_monthly(pe_monthly, otc_nd["pe"])
    log("== 加權指數")
    update_index(index)
    log("== 法說會行事曆")
    confs = load("confs.json", {})
    update_confs(confs)
    save("confs.json", confs)
    log("== 新聞（法人調升目標價、上修財測）")
    news = load("news.json", {})
    update_news(news, names)
    save("news.json", news)
    log("== 法人共識 EPS（FactSet 調查新聞標題）")
    consensus = load("consensus.json", {})
    try:                                  # 新功能出錯不能讓整個更新失敗
        update_consensus(consensus, names)
        save("consensus.json", consensus)
    except Exception as e:
        import traceback
        probe["errors"].append("consensus: " + traceback.format_exc()[-400:])
        log(f"  法人共識 EPS 失敗：{e}")
    log("== 集保大戶持股（每週）")
    update_tdcc(tdcc)
    save("tdcc.json", tdcc)
    log("== 除權息／減資")
    _safe(update_divs, "除權息")
    log("== 月營收 / 財報")
    fund = fetch_fundamentals(fund)
    log("== 月營收歷史（公開資訊觀測站）")
    backfill_revenue(fund)
    for h in fund.get("rev_hist", {}).values():
        for k in sorted(h)[:-REV_MONTHS]:
            del h[k]

    meta["otc_codes"] = sorted(OTC_CODES)
    meta["otc_latest"] = OTC_LATEST
    cut = ymd(datetime.now(TW).date() - timedelta(days=400))
    for k, v in otc_nd.items():   # 日資料只留近期；月取樣（pe）全留，避免每天重試很舊的月份
        meta["nodata_otc_" + k] = sorted(x for x in v if k == "pe" or x >= cut)
    sync_hist(prices, t86)
    save("prices.json", prices)
    save("names.json", names)
    save("t86.json", t86)
    save("pe_monthly.json", pe_monthly)
    save("bwibbu_latest.json", bw_latest)
    save("meta.json", meta)
    save("fundamentals.json", fund)

    out = build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores, tdcc, qf_latest,
                       news, confs, load("themes.json", {}), consensus)
    save("index.json", index)
    save("scores.json", scores)
    save("fundamentals.json", fund)
    probe["rwd_calls"] = rwd_calls
    probe["tpex_calls"] = tpex_calls
    probe["finished"] = datetime.now(TW).isoformat()
    probe["counts"] = {"price_days": len(prices), "t86_days": len(t86), "pe_months": len(pe_monthly),
                       "revenue": len(fund.get("revenue", {})), "income": len(fund.get("income", {})),
                       "equity": len(fund.get("equity", {})), "index_days": len(index),
                       "rev_hist_months": len({k for h in fund.get("rev_hist", {}).values() for k in h})}
    save("probe.json", probe)
    if out:
        save("latest.json", out, OUT)
        bt = load("backtest.json", None)
        if bt:
            save("backtest.json", bt, OUT)
        pool = load("pool.json", None)
        if pool:
            save("pool.json", pool, OUT)
        n = sum(1 for s in out["stocks"] if s["s"].get("score") is not None)
        log(f"輸出完成：{out['date']}，共 {len(out['stocks'])} 檔，有總分 {n} 檔")
    else:
        log("沒有收盤資料，無法輸出")
        if not (OUT / "latest.json").exists():
            sys.exit(1)


if __name__ == "__main__":
    main()
