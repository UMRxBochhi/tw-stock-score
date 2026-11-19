"""（2026-11-19：探測 2008 年前後）

探測櫃買中心（上櫃）各資料來源的實際格式，結果寫到 data/probe_tpex.json。
公司網路連不到 tpex.org.tw，所以由 GitHub Actions 執行。
"""
import json
import time
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent.parent / "data" / "probe_tpex.json"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                  "Accept": "application/json,text/html,*/*"})

D_NEW, D_OLD = "2026/11/13", "2024/04/01"
R_NEW, R_OLD = "115/11/13", "113/04/01"
T = "https://www.tpex.org.tw"
URLS = {
    # 2008~2017 回補可行性
    "twse_px_2008": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20081015&type=ALLBUT0999&response=json",
    "twse_px_2005": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20050315&type=ALLBUT0999&response=json",
    "twse_qf_2008": "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20081015&selectType=ALLBUT0999&response=json",
    "twse_qf_2012": "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date=20120315&selectType=ALLBUT0999&response=json",
    "twse_pe_2008": "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20081015&selectType=ALL&response=json",
    "twse_pe_2005": "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20050315&selectType=ALL&response=json",
    "tpex_px_2008": f"{T}/www/zh-tw/afterTrading/dailyQuotes?date=2008/10/15&type=EW&response=json",
    "tpex_qf_2008": f"{T}/www/zh-tw/insti/qfii?date=2008/10/15&response=json",
    "tpex_qf_2012": f"{T}/www/zh-tw/insti/qfii?date=2012/03/15&response=json",
    "tpex_pe_2008": f"{T}/www/zh-tw/afterTrading/peQryDate?date=2008/10/15&response=json",
    "mops_sii_2004": "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_93_1_0.html",
    "mops_sii_2008": "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_97_10_0.html",
    "mops_sii_2008b": "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_97_10.html",
    "mops_otc_2008": "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_97_10_0.html",
    "mops_otc_2008b": "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_97_10.html",
}


def summarize(js):
    info = {"type": type(js).__name__}
    if isinstance(js, dict):
        info["keys"] = list(js.keys())[:30]
        for k in ("stat", "date", "reportDate", "iTotalRecords", "totalCount"):
            if k in js:
                info[k] = js[k]
        if isinstance(js.get("tables"), list):
            info["tables"] = [{"title": t.get("title"), "fields": t.get("fields"), "n": len(t.get("data") or []),
                               "sample": (t.get("data") or [])[:3] + (t.get("data") or [])[-1:], "keys": list(t.keys())} for t in js["tables"][:4]]
        for k, v in js.items():
            if isinstance(v, list) and v and k != "tables":
                info["list_" + k] = {"n": len(v), "sample": v[:3]}
    elif isinstance(js, list):
        info["n"] = len(js)
        info["sample"] = js[:3]
    return info


res = {}
for name, url in URLS.items():
    for method in ("GET", "POST"):
        if method == "POST" and "/www/" not in url:
            continue
        key = name + ("" if method == "GET" else "_post")
        try:
            time.sleep(2)
            if method == "GET":
                r = S.get(url, timeout=40)
            else:
                base, _, qs = url.partition("?")
                data = dict(x.split("=", 1) for x in qs.split("&") if x)
                r = S.post(base, data=data, timeout=40)
            info = {"status": r.status_code, "ctype": r.headers.get("content-type"), "len": len(r.content), "url": r.url}
            try:
                info.update(summarize(r.json()))
            except Exception:
                txt = r.content.decode("cp950" if "mops" in name else "utf-8", errors="ignore")
                info["text"] = txt[:600]
                rows = __import__("re").findall(r"<tr[^>]*>(.*?)</tr>", txt, flags=__import__("re").S | __import__("re").I)
                info["n_tr"] = len(rows)
                info["tr_sample"] = [__import__("re").sub(r"<[^>]+>|&nbsp;", "|", x)[:300] for x in rows[3:8]]
            res[key] = info
        except Exception as e:
            res[key] = {"error": str(e)[:300]}
        print(key, res[key].get("status"), res[key].get("len"), res[key].get("error", ""), flush=True)

OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
