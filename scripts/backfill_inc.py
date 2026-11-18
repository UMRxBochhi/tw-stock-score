"""
補 2017 起每季損益表（公開資訊觀測站 t163sb04 綜合損益表彙總，上市＋上櫃）
輸出 data/hist/inc.json.gz：{code: {"107Q1": [營收, 毛利, 營業利益]}}，都是「當年累計」數字
已補過的季度不重抓；最近兩季每次重抓（晚公布的公司）
"""
import gzip
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "hist" / "inc.json.gz"
PROBE = ROOT / "data" / "probe_inc.json"
URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
probe = {"errors": [], "periods": {}, "headers": []}


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def cells(tr, tag):
    return [re.sub(r"<[^>]+>|&nbsp;", "", x).strip()
            for x in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", tr, flags=re.S | re.I)]


def find(hd, *keys, exclude=()):
    for k in keys:
        for i, h in enumerate(hd):
            if k in h and not any(e in h for e in exclude):
                return i
    return None


def fetch(roc_y, season, typek):
    data = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y",
            "TYPEK": typek, "year": roc_y, "season": f"{season:02d}"}
    for k in range(4):
        try:
            time.sleep(4 + k * 8)
            r = S.post(URL, data=data, timeout=60)
            html = r.content.decode("utf-8", errors="ignore")
            if "頻繁" in html or "Overrun" in html:
                raise RuntimeError("查詢過於頻繁")
            break
        except Exception as e:
            probe["errors"].append(f"{roc_y}Q{season} {typek}: {e}"[:200])
            html = None
    if not html:
        return None
    out = {}
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S | re.I):
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, flags=re.S | re.I)
        hd = None
        for tr in trs:
            th = cells(tr, "th")
            if th and any("公司代號" in h for h in th):
                hd = th
                continue
            if not hd:
                continue
            td = cells(tr, "td")
            if len(td) != len(hd) or not re.fullmatch(r"\d{4}", td[0]):
                continue
            ir = find(hd, "營業收入", "收入合計", "收益合計", exclude=("以外", "成本"))
            ig = find(hd, "營業毛利（毛損）淨額", "營業毛利（毛損）", "營業毛利")
            io = find(hd, "營業利益", exclude=("率",))
            if len(probe["headers"]) < 8 and hd not in probe["headers"]:
                probe["headers"].append(hd)
            rv = num(td[ir]) if ir is not None else None
            if rv:
                out[td[0]] = [rv, num(td[ig]) if ig is not None else None, num(td[io]) if io is not None else None]
    return out


def main():
    db = json.loads(gzip.decompress(OUT.read_bytes())) if OUT.exists() else {}
    cnt = {}
    for c, h in db.items():
        for k in h:
            cnt[k] = cnt.get(k, 0) + 1
    now = datetime.now()
    periods = []
    for y in range(2016, now.year + 1):
        for q in (1, 2, 3, 4):
            # 財報公布期限：Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31
            due = {1: (y, 5, 15), 2: (y, 8, 14), 3: (y, 11, 14), 4: (y + 1, 3, 31)}[q]
            if datetime(*due) > now and not (datetime(*due) - now).days < 45:
                continue
            periods.append((y, q))
    recent = set(periods[-2:])
    t0 = time.time()
    for y, q in periods:
        key = f"{y - 1911}Q{q}"
        if cnt.get(key, 0) >= 1200 and (y, q) not in recent:
            continue
        if time.time() - t0 > 60 * 60:
            break
        n = 0
        for typek in ("sii", "otc"):
            res = fetch(y - 1911, q, typek)
            for c, v in (res or {}).items():
                db.setdefault(c, {})[key] = v
                n += 1
        probe["periods"][key] = n
        print(key, n, flush=True)
    OUT.write_bytes(gzip.compress(json.dumps(db, separators=(",", ":")).encode()))
    probe["companies"] = len(db)
    PROBE.write_text(json.dumps(probe, ensure_ascii=False, indent=1), encoding="utf-8")
    print("done", len(db), "errors", len(probe["errors"]))


if __name__ == "__main__":
    sys.exit(main())
