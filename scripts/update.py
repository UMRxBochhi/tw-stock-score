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


SCORE_VER = 5          # 評分公式版本（改公式就 +1，分數歷史會重新累積）
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
    return out if len(out) >= 500 else None


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
            inc[code] = {
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


def build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores, tdcc=None, qf_latest=None):
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
        sd = [d for d in sdates if code in scores[d]]
        det["sc"] = {"d": sd, "score": [scores[d][code] for d in sd]}
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
        "etfs": etfs,
        "rules": RULES,
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
    if prices and qf_latest.get("date") != max(prices):
        for k in sorted(prices, reverse=True)[:3]:
            if qf_latest.get("date") and qf_latest["date"] >= k:
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
    log("== 集保大戶持股（每週）")
    update_tdcc(tdcc)
    save("tdcc.json", tdcc)
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

    out = build_output(prices, names, t86, bw_latest, pe_monthly, fund, index, scores, tdcc, qf_latest)
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
