"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const LS = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};
const state = {
  data: null, tab: "win", sort: null, asc: false,
  f: Object.fromEntries(Object.entries(LS.get("filters", {})).map(([k, v]) => [k === "short" || k === "long" ? "score" : k, v])),
  watch: new Set(LS.get("watch", [])), // 自選股
};
const STRAT_NAME = { short: "短線", long: "長線" };

// ---------- 格式化
const NA = '<span class="na">—</span>';
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2, suf = "") => v == null || Number.isNaN(+v) ? NA : `${(+v).toFixed(d)}${suf}`;
const signed = (v, d = 2, suf = "%") => {
  if (v == null) return NA;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${(+v).toFixed(d)}${suf}</span>`;
};
const bar = (v) => v == null ? NA :
  `<span class="bar"><span class="track"><span class="fill" style="width:${Math.max(2, v)}%"></span></span><span class="v">${v.toFixed(0)}</span></span>`;
const ymdTxt = (d) => `${d.slice(0, 4)}/${d.slice(4, 6)}/${d.slice(6, 8)}`;
const md = (d) => `${+d.slice(4, 6)}/${+d.slice(6, 8)}`;
const rocYm = (k) => `${+k.slice(0, -2) + 1911}/${k.slice(-2)}`;

// ---------- 欄位定義（表格、篩選共用）
const F = {
  score: { t: "基本面分數（營收預估上修、創新高、動能；技術面與籌碼面不計分）", short: "分數", g: (s) => s.s.score, kind: "total", grp: "分數" },
  f: { t: "基本面分數（營收預估上修、成長）", short: "基本面", g: (s) => s.s.f, kind: "bar", grp: "分數" },
  t: { t: "技術面（參考，不計分）", short: "技術*", g: (s) => s.s.t, kind: "bar", grp: "分數" },
  c: { t: "籌碼面（參考，不計分）", short: "籌碼*", g: (s) => s.s.c, kind: "bar", grp: "分數" },
  d5: { t: "5 日分數變化", short: "5日Δ", g: (s) => s.s.d5, kind: "signed1", suf: "", grp: "分數" },
  cap: { t: "市值（億）", short: "市值(億)", g: (s) => s.m.cap != null ? s.m.cap / 1e8 : null, kind: "num0", grp: "價格" },
  qd4: { t: "外資持股 4 週變化（百分點）", short: "外資Δ", g: (s) => s.m.qd4, kind: "signed", suf: "", grp: "籌碼" },
  cagr3: { t: "近 3 年營收年複合成長（%）", short: "3年成長", g: (s) => s.m.cagr3, kind: "signed", suf: "%", grp: "營收" },
  cons_chg: { t: "法人共識 EPS 近 90 天變化（FactSet 調查新聞）", short: "法人EPS", g: (s) => s.m.cons_chg, kind: "signed", suf: "%", grp: "營收" },
  rev_up_n: { t: "營收預估連續上修月數", short: "連續上修", g: (s) => s.m.rev_up_n, kind: "num0", grp: "營收" },
  rev_rev3: { t: "營收預估近 3 個月上修 %", short: "3月上修", g: (s) => s.m.rev_rev3, kind: "signed1", grp: "營收" },
  pe_z: { t: "本益比位階 σ（< −0.5 便宜、> +0.5 偏貴）", short: "位階σ", g: (s) => s.m.pe_z, kind: "signed", suf: "", grp: "價值" },
  opm: { t: "營業利益率 %（今年累計）", short: "營益率", g: (s) => s.m.opm, kind: "num1", suf: "%", grp: "獲利" },
  rev_hi: { t: "最新月營收距前 12 月最高 %（≥ 0 = 創新高）", short: "營收距高", g: (s) => s.m.rev_hi, kind: "signed1", grp: "營收" },
  qf: { t: "外資持股比例 %", short: "外資持股%", g: (s) => s.m.qf, kind: "num1", suf: "%", grp: "籌碼" },
  sup_dist: { t: "距前波支撐 %（負 = 已跌破）", short: "距支撐", g: (s) => s.m.sup_dist, kind: "signed1", grp: "支撐" },
  sup_broke: { t: "跌破前波低點（1 = 是、0 = 否）", short: "破底", g: (s) => s.m.sup_broke == null ? null : (s.m.sup_broke ? 1 : 0), kind: "num0", grp: "支撐" },
  vsup_dist: { t: "距大量支撐 %", short: "距大量", g: (s) => s.m.vsup_dist, kind: "signed1", grp: "支撐" },
  p: { t: "收盤價", short: "收盤", g: (s) => s.m.p, kind: "num", grp: "價格" },
  chg: { t: "今日漲跌 %", short: "今日", g: (s) => s.m.chg, kind: "signed", grp: "價格" },
  r20: { t: "20 日漲跌 %", short: "20日", g: (s) => s.m.r20, kind: "signed1", grp: "價格" },
  b20: { t: "20 日乖離 %", short: "月乖離", g: (s) => s.m.b20, kind: "signed1", grp: "價格" },
  b60: { t: "股價相對 60 日均線 %", short: "距季線", g: (s) => s.m.b60, kind: "signed1", grp: "價格" },
  avgval: { t: "20 日均成交金額（億）", short: "均量(億)", g: (s) => s.m.avgval != null ? s.m.avgval / 1e8 : null, kind: "num", grp: "價格" },
  yld: { t: "殖利率 %", short: "殖利率", g: (s) => s.m.yld, kind: "num", suf: "%", grp: "價值" },
  pe: { t: "本益比", g: (s) => s.m.pe, kind: "num1", grp: "價值" },
  pe_pos: { t: "本益比位階 %", short: "PE位階", g: (s) => s.m.pe_pos, kind: "num0", suf: "%", grp: "價值" },
  fpe: { t: "預估本益比（Forward PE）", short: "預估PE", g: (s) => s.m.fpe, kind: "num1", grp: "價值" },
  vpos: { t: "河流圖位階 %（有預估用預估）", short: "河流位階", g: (s) => s.m.fpe_pos ?? s.m.pe_pos, kind: "num0", suf: "%", grp: "價值" },
  pb: { t: "股價淨值比", short: "PB", g: (s) => s.m.pb, kind: "num", grp: "價值" },
  rev_yoy: { t: "月營收年增 %", short: "營收YoY", g: (s) => s.m.rev_yoy, kind: "signed1", grp: "營收" },
  rev3: { t: "近 3 月營收年增 %", short: "近3月YoY", g: (s) => s.m.rev3, kind: "signed1", grp: "營收" },
  accel: { t: "營收動能（近3月 − 近12月）", short: "營收動能", g: (s) => s.m.accel, kind: "signed1", suf: "", grp: "營收" },
  streak: { t: "連續年增月數", short: "連增月", g: (s) => s.m.streak, kind: "num0", grp: "營收" },
  cum_yoy: { t: "累計營收年增 %", short: "累計YoY", g: (s) => s.m.cum_yoy, kind: "signed1", grp: "營收" },
  est_growth: { t: "全年營收推估成長 %", short: "全年推估", g: (s) => s.m.est_growth, kind: "signed1", grp: "營收" },
  roe: { t: "ROE（年化）%", short: "ROE", g: (s) => s.m.roe, kind: "num1", suf: "%", grp: "獲利" },
  gm: { t: "毛利率 %", short: "毛利率", g: (s) => s.m.gm, kind: "num1", suf: "%", grp: "獲利" },
  f20: { t: "外資 20 日買超 / 成交量 %", short: "外資20日", g: (s) => s.m.f20, kind: "signed", grp: "籌碼" },
  t20: { t: "投信 20 日買超 / 成交量 %", short: "投信20日", g: (s) => s.m.t20, kind: "signed", grp: "籌碼" },
  big400: { t: "大戶持股 %（400 張以上，集保）", short: "大戶%", g: (s) => s.m.big400, kind: "num1", suf: "%", grp: "籌碼" },
  big400_chg: { t: "大戶持股 4 週變化（百分點）", short: "大戶Δ", g: (s) => s.m.big400_chg, kind: "signed1", suf: "", grp: "籌碼" },
  i5: { t: "法人 5 日買超 / 成交量 %", short: "法人5日", g: (s) => s.m.i5, kind: "signed", grp: "籌碼" },
};
const LABEL = { rev_yoy: "月營收年增率", accel: "營收動能", cum_yoy: "累計營收年增率", roe: "ROE（年化）", gm: "毛利率",
  qf: "外資持股比例", f20: "外資 20 日買超 / 成交量", t20: "投信 20 日買超 / 成交量", i5: "三大法人 5 日買超 / 成交量",
  r20: "20 日漲跌幅", b60: "股價相對 60 日均線（季線）", streak: "營收連續年增月數", rev3: "近 3 月營收年增", rev_hi: "營收距 12 月新高", pe_cheap: "本益比位階（越低越好）", yld: "殖利率", pb_cheap: "股價淨值比（越低越好）", val_cheap: "河流圖位階（預估本益比優先；位階 30% = 70 分）" };

function cell(key, s) {
  const f = F[key], v = f.g(s), suf = f.suf ?? "";
  switch (f.kind) {
    case "total": return v == null ? NA : `<span class="total">${v.toFixed(0)}</span>`;
    case "bar": return bar(v);
    case "signed": return signed(v, 2, suf || (f.suf === "" ? "" : "%"));
    case "signed1": return signed(v, 1, f.suf === "" ? "" : "%");
    case "num0": return fmt(v, 0, suf);
    case "num1": return fmt(v, 1, suf);
    default: return fmt(v, 2, suf);
  }
}

const TABCOLS = {
  step2: ["p", "chg", "score", "rev_up_n", "cons_chg", "qf", "cagr3", "pe_z", "cap"],
  watch: ["p", "chg", "score", "rev_up_n", "rev_rev3", "pe_z", "b20", "b60", "qd4"],
  screen: ["p", "chg", "score", "f", "t", "c", "rev_up_n", "rev_rev3", "rev_hi", "pe_z", "opm", "qf", "cap"],
};
const DEFAULT_SORT = { step2: [null, false], watch: ["score", false], screen: ["score", false] };
const HIDE_SM = new Set(["chg", "f", "t", "c", "rev_rev3", "rev_hi", "b20", "b60", "cap", "opm", "qf", "qd4", "d5"]);

const FILTER_TABS = new Set(["screen"]);

// ---------- 快速篩選
const PRESETS = {
  upgrade: { t: "營收預估連續上修 ≥ 3 個月", f: { rev_up_n: [3, null] } },
  upgrade_cheap: { t: "連續上修 ≥ 2 個月＋本益比便宜（< −0.5σ）", f: { rev_up_n: [2, null], pe_z: [null, -0.5] } },
  upgrade_fair: { t: "連續上修 ≥ 2 個月＋本益比不貴（≤ +0.5σ）", f: { rev_up_n: [2, null], pe_z: [null, 0.5] } },
  rev_high: { t: "營收創 12 個月新高", f: { rev_hi: [0, null] } },
  big_cap: { t: "市值 ≥ 100 億＋分數 ≥ 70", f: { cap: [100, null], score: [70, null] } },
  downgrade: { t: "營收預估本月下修（留意）", f: { rev_up_n: [0, 0] } },
  rich: { t: "本益比偏貴（> +0.5σ）", f: { pe_z: [0.5, null] } },
};

// ---------- 清單
function baseList() {
  const all = state.data.stocks;
  switch (state.tab) {
    case "step2": {
      const codes = state.data.step2?.codes || [];
      return codes.map((c) => all.find((s) => s.c === c)).filter(Boolean);
    }
    case "watch": return all.filter((s) => state.watch.has(s.c));
    default: return all;
  }
}

function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  const ind = $("#ind").value;
  let list = baseList();
  if (q) list = list.filter((s) => s.c.includes(q) || (s.n || "").toLowerCase().includes(q));
  if (ind) list = list.filter((s) => s.ind === ind);
  for (const [k, [lo, hi]] of FILTER_TABS.has(state.tab) ? Object.entries(state.f) : []) {
    if (!F[k] || (lo == null && hi == null)) continue;
    list = list.filter((s) => {
      const v = F[k].g(s);
      if (v == null) return false;
      return (lo == null || v >= lo) && (hi == null || v <= hi);
    });
  }
  const key = state.sort;
  if (key) {
    const get = key === "name" ? (s) => s.c : key === "ind" ? (s) => s.ind || "" : F[key]?.g;
    const dir = state.asc ? 1 : -1;
    if (get) list = [...list].sort((a, b) => {
      const va = get(a), vb = get(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
  }
  return list;
}

function renderTable() {
  const cols = TABCOLS[state.tab];
  const list = filtered();
  const shown = list.slice(0, 500);
  $("#count").textContent = `共 ${list.length} 檔${list.length > 500 ? "（顯示前 500）" : ""}`;
  const thc = (k, t, cls = "") => `<th data-k="${k}" class="${cls} ${state.sort === k ? "sorted" + (state.asc ? " asc" : "") : ""}">${t}</th>`;
  const th = `<th class="starc"></th><th class="rank">#</th>${thc("name", "股票", "l")}${thc("ind", "產業", "l hide-sm")}` +
    cols.map((k) => thc(k, F[k].short || F[k].t, HIDE_SM.has(k) ? "hide-sm" : "")).join("");
  const tb = shown.map((s, i) => `<tr data-c="${s.c}">
      <td class="starc"><button class="star ${state.watch.has(s.c) ? "on" : ""}" data-star="${s.c}" title="加入/移除自選">${state.watch.has(s.c) ? "★" : "☆"}</button></td>
      <td class="rank">${i + 1}</td>
      <td class="l"><span class="code">${s.c}</span>${esc(s.n)}${s.mk === "o" ? '<span class="mk" title="上櫃">櫃</span>' : ""}${flagHtml(s)}</td>
      <td class="l hide-sm"><span class="ind">${esc(s.ind || "")}</span></td>
      ${cols.map((k) => `<td class="${HIDE_SM.has(k) ? "hide-sm" : ""}">${cell(k, s)}</td>`).join("")}</tr>`).join("");
  let empty = "沒有符合條件的股票。";
  if (state.tab === "watch" && !state.watch.size) empty = "還沒有持股。點 ☆ 加入。";
  $("#tableWrap").innerHTML = list.length ? `<table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`
    : `<p class="empty">${empty}</p>`;
}

function flagHtml(s) {
  const m = s.m;
  if (state.tab === "step2") {
    const q = state.data.step2 || {};
    if (q.stopped?.[s.c]) return ` <span class="flag warn" title="從買進後最高 ${q.stopped[s.c].peak} 回落 30%（${md(q.stopped[s.c].d)}）">回落 30%：下車</span>`;
    if ((q.added || []).includes(s.c) && (q.hist || []).length) return ' <span class="flag good" title="本季新進">新進</span>';
    if (!(q.live || []).includes(s.c)) return ' <span class="flag warn" title="現在已不符合條件，下次換股會剔除（期間內不用賣）">已不符合</span>';
    return s.ind ? ` <span class="flag">${esc(s.ind)}</span>` : "";
  }
  if (m.over) return ' <span class="flag warn" title="20 日乖離 > 20% 或季線乖離 > 50%，分數已扣 15">過熱</span>';
  if (m.rev_up_n === 0) return ' <span class="flag warn" title="營收預估本月下修">預估下修</span>';
  if (m.rev_up_n >= 3) return ` <span class="flag good" title="營收預估連續上修">上修 ${m.rev_up_n} 月</span>`;
  return "";
}

// ---------- 進階篩選面板
function renderAdv() {
  const groups = {};
  Object.entries(F).forEach(([k, f]) => (groups[f.grp] ||= []).push(k));
  $("#adv").innerHTML = Object.entries(groups).map(([g, keys]) => `<fieldset><legend>${g}</legend>${keys.map((k) => {
    const [lo, hi] = state.f[k] || [];
    return `<label><span>${F[k].t}</span><span class="rng"><input type="number" step="any" data-f="${k}" data-i="0" placeholder="最小" value="${lo ?? ""}">
      ~ <input type="number" step="any" data-f="${k}" data-i="1" placeholder="最大" value="${hi ?? ""}"></span></label>`;
  }).join("")}</fieldset>`).join("");
  updateAdvCount();
}
function updateAdvCount() {
  const n = Object.values(state.f).filter(([a, b]) => a != null || b != null).length;
  $("#advN").textContent = n ? `(${n})` : "";
}
function setFilters(f) {
  state.f = JSON.parse(JSON.stringify(f));
  LS.set("filters", state.f);
  renderAdv();
  render();
}

// ---------- 市場概況
function renderMarket() {
  const m = state.data.market || {};
  const cards = [];
  if (m.taiex) {
    const pct = m.taiex_chg != null ? m.taiex_chg / (m.taiex - m.taiex_chg) * 100 : null;
    cards.push(`<div class="card"><div class="lbl">加權指數 <span class="note">${md(m.taiex_date)}</span></div>
      <div class="val">${m.taiex.toLocaleString("zh-TW", { maximumFractionDigits: 2 })}</div>
      <div>${signed(m.taiex_chg, 2, "")}　${signed(pct)}</div>${Charts.spark(m.index.map((x) => x[1]))}</div>`);
  }
  const tot = (m.up || 0) + (m.down || 0) + (m.flat || 0);
  if (tot) {
    cards.push(`<div class="card"><div class="lbl">今日漲跌家數（上市櫃股票）</div>
      <div class="val"><span class="up">${m.up}</span> / <span class="down">${m.down}</span></div>
      <div class="breadth" title="上漲 ${m.up}、平盤 ${m.flat}、下跌 ${m.down}"><span class="b-up" style="width:${m.up / tot * 100}%"></span><span class="b-flat" style="width:${m.flat / tot * 100}%"></span><span class="b-down" style="width:${m.down / tot * 100}%"></span></div>
      <div class="note">上漲 ${m.up}・平盤 ${m.flat}・下跌 ${m.down}</div></div>`);
  }
  const g = (state.data.groups || []).filter((x) => x.n >= 5).slice(0, 3);
  if (g.length) cards.push(`<div class="card"><div class="lbl">① 最強族群</div>${g.map((x) =>
    `<div class="mini"><a href="#" data-gotab="trend">${esc(x.seg)}</a><b>${x.seg_score?.toFixed(0) ?? "—"}</b></div>`).join("")}</div>`);
  const q = state.data.step2 || {};
  if (q.codes) cards.push(`<div class="card"><div class="lbl">② 選股名單</div><div class="val">${q.codes.length} 檔・${esc(q.level_now || "")}</div>
    <div class="note">建議個股 ${q.stock_w}%、其餘 0050；下次換股 ${q.next}　<a href="#" data-gotab="step2">看名單 →</a></div></div>`);
  const k = state.data.crash;
  if (k?.active) cards.push(`<div class="card"><div class="lbl">大跌加碼名單</div><div class="val">已啟用・${k.n} 檔</div>
    <div class="note">0050 距一年高點 ${k.dd}%　<a href="#" data-gotab="step2">看名單 →</a></div></div>`);
  $("#market").innerHTML = cards.join("");
}

// ---------- 第二步：選股名單
function renderStep2Info() {
  const q = state.data.step2 || {};
  const nm = (c) => { const x = state.data.stocks.find((s) => s.c === c); return `<a href="#${c}" data-open="${c}">${c} ${esc(x?.n || "")}</a>`; };
  const pct = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`;
  const lvCls = { "有利": "good", "普通": "neutral", "不利": "warn" };
  const hist = (q.hist || []).slice().reverse();
  $("#ruleInfo").innerHTML = `<div class="rulebox">
    <b>② 選股名單</b>　<span class="note">${q.key || ""} 起（${q.start ? md(q.start) : ""} 收盤價），下次換股 ${q.next || "—"} 之後</span>
    <ol class="sop">
      <li><b>條件</b>：市值 ≥ 100 億、營收預估上修中、外資持股 ≥ 30%、近 3 年營收年成長 ≥ 10%（長期成長＋法人認可）；依基本面分數排序，最多 10 檔。</li>
      <li><b>比例</b>：<span class="chip ${lvCls[q.level_now] || "neutral"}">現在：${esc(q.level_now || "—")}</span>
        建議個股合計 <b>${q.stock_w ?? "—"}%</b>（每檔約 ${q.per_w ?? "—"}%），其餘放 0050。
        ${(q.fav || []).map((t) => `<span class="chip good">${esc(t)}</span>`).join(" ")} ${(q.unfav || []).map((t) => `<span class="chip warn">${esc(t)}</span>`).join(" ")}</li>
      <li><b>持有與下車</b>：每月 11 日後（新營收公布）檢查一次，只換掉<b>不再符合條件</b>的股票，還符合的續抱（好股票可以一直抱）；另外<b>從買進後最高點回落 30%</b> 就提前下車。
        不設「賺多少就走」（回測：賺 50% 就走年化只剩 23%，永遠不賣只有 19%、一半的下車是虧超過 30%）。</li>
      <li><b>本益比位階、技術面、籌碼面</b>只當參考：同樣要買時，可優先買位階不貴、沒有漲多的。</li>
    </ol>
    <p class="note">本期至今：名單平均 <b>${pct(q.ret)}</b>，0050 <b>${pct(q.ret0050)}</b>（不含股利）。
      ${q.added?.length && hist.length ? `新進：${q.added.map(nm).join("、")}。` : ""}${q.removed?.length ? `剔除：${q.removed.map(nm).join("、")}。` : ""}</p>
    ${hist.length ? `<table class="kv"><tr><td>期間</td><td>檔數</td><td>名單</td><td>0050</td></tr>${hist.map((h) =>
      `<tr><td>${md(h.start)}~${md(h.end)}</td><td>${h.n}</td><td>${pct(h.ret)}</td><td>${pct(h.ret0050)}</td></tr>`).join("")}</table>` : ""}
    ${crashHtml()}
    <p class="note">回測（2018~2026，已扣交易成本，10 檔全押個股）：年化約 36%（2018~23 約 18%、2023~26 約 69%），0050 22.1%；最大回撤約 −40%。
      42 種參數組合（外資門檻、檢查頻率、停損幅度）裡 41 種贏 0050，所以不是靠剛好挑到某組參數；但每個月贏 0050 的比例只有約 5 成，要整組、拉長看。
      有利／不利的判斷樣本少，可能是巧合，從這一季開始實際記錄驗證。不是投資建議。</p></div>`;
}

function crashHtml() {
  const k = state.data.crash;
  if (!k) return "";
  const nm = (c) => { const x = state.data.stocks.find((s) => s.c === c); return `<a href="#${c}" data-open="${c}">${c} ${esc(x?.n || "")}</a>`; };
  const head = k.active
    ? `<span class="chip warn">已啟用</span> 0050 距一年高點 ${k.dd}%（跌超過 15%）：可以分 2~3 次加碼 0050，並買進下面這組股票（持有 12 個月）。`
    : `<span class="chip neutral">未啟用</span> 0050 距一年高點 ${k.dd ?? "—"}%；跌超過 15% 才啟用。下面是目前符合條件的股票，先當觀察。`;
  return `<div style="margin-top:10px"><b>大跌加碼名單</b>　<span class="note">${head}</span>
    <p class="note">候選：營收預估上修中、前瞻本益比 &lt; −0.5σ（和自己過去 5 年的前瞻本益比比）、市值 ≥ 100 億，目前 ${k.n ?? 0} 檔；
      依「外資持股 40%＋近 3 年營收成長 40%＋市值 20%」排序取前 10（便不便宜在候選內沒有分辨力，不加分）。
      回測 2020~2025 共 6 次大跌，持有 12 個月組合都贏 0050、單檔約 6 成贏；但樣本少，而且下跌途中買進可能先套一段，所以要分批。</p>
    ${k.rows?.length ? `<table class="kv"><tr><td>股票</td><td>排序分數</td><td>外資持股</td><td>3 年營收年成長</td><td>市值（億）</td><td>前瞻位階</td><td>連續上修</td></tr>${k.rows.map((r) =>
      `<tr><td>${nm(r.c)}</td><td>${r.sc}</td><td>${r.qf ?? "—"}%</td><td>${r.cagr3}%</td><td>${r.cap.toLocaleString()}</td><td>${r.zf}σ</td><td>${r.up} 月</td></tr>`).join("")}</table>` : '<p class="note">目前沒有符合的股票。</p>'}</div>`;
}

// ---------- 第三步：持股監控
function holdAdvice(s) {
  const m = s.m, out = [];
  const q = state.data.step2 || {}, k = state.data.crash || {};
  // 兩份名單分開寫，不互相覆蓋
  const acts = [];
  const ki = (k.codes || []).indexOf(s.c);
  if (ki >= 0) acts.push([k.active ? "good" : "neutral",
    `大跌加碼名單第 ${ki + 1} 名${k.active ? "（已啟用）：分批買、持有 12 個月" : "（未啟用）"}`]);
  const qi = (q.live || []).indexOf(s.c), qn = (q.live || []).length;
  if ((q.codes || []).includes(s.c)) {
    if (q.stopped?.[s.c]) acts.push(["warn", "選股名單：從買進後高點回落 30%，下車"]);
    else if (qi >= 0) acts.push(["good", `選股名單：在名單內，續抱（距買進後高點 ${q.dd?.[s.c] ?? "—"}%）`]);
    else acts.push(["warn", `選股名單：已不符合條件，${q.next || "下次檢查"} 換掉`]);
  } else if (qi >= 0) {
    acts.push(["neutral", `選股名單：符合條件，排第 ${qi + 1} / ${qn} 名（只取前 10）；已持有可續抱`]);
  } else if (m.cap >= 1e10) {
    acts.push(["neutral", "選股名單：目前不符合條件"]);
  }
  const act = acts.map((a) => a[1]).join("；") || "照自己的計畫";
  const cls = acts.some((a) => a[0] === "warn") ? "warn" : acts.some((a) => a[0] === "good") ? "good" : "neutral";
  if (m.rev_up_n === 0) out.push(["warn", "營收預估本月下修"]);
  if (m.rev_up_n >= 1) out.push(["good", `營收預估上修中（${m.rev_up_n} 個月）`]);
  if (m.cons_up) out.push(["good", `法人 EPS 上修 ${m.cons_up} 次（90 天）`]);
  if (m.cons_dn) out.push(["warn", `法人 EPS 下修 ${m.cons_dn} 次（90 天）`]);
  if (m.qf != null) out.push([m.qf >= 20 ? "good" : "neutral", `外資持股 ${m.qf}%`]);
  if (m.cagr3 != null) out.push([m.cagr3 >= 10 ? "good" : "neutral", `3 年營收年成長 ${m.cagr3}%`]);
  if (m.news_up) out.push(["good", `法人上調 ${m.news_up} 則`]);
  if (m.news_dn) out.push(["warn", `法人下調 ${m.news_dn} 則`]);
  return { act, cls, out };
}
function renderStep3() {
  const list = state.data.stocks.filter((s) => state.watch.has(s.c));
  if (!list.length) { $("#watchInfo").innerHTML = '<div class="rulebox"><b>③ 持股監控</b><p class="note">還沒有持股。在任何清單點 ☆ 加入，這裡會每天檢查它還在不在選股名單、大跌名單。</p></div>'; return; }
  $("#watchInfo").innerHTML = `<div class="rulebox"><b>③ 持股監控</b>　<span class="note">每天收盤後更新；選股名單符合條件就續抱、回落 30% 下車；大跌名單持有 12 個月</span>
    <table class="kv" style="margin-top:8px"><tr><td>股票</td><td>建議</td><td>訊號</td></tr>
    ${list.map((s) => { const a = holdAdvice(s); return `<tr><td><a href="#${s.c}" data-open="${s.c}">${s.c} ${esc(s.n)}</a></td>
      <td><span class="chip ${a.cls}">${a.act}</span></td><td>${a.out.map(([c, t]) => `<span class="chip ${c}">${t}</span>`).join(" ") || NA}</td></tr>`; }).join("")}
    </table>
    <p class="note"><b>下車規則怎麼來的</b>：回測比較過各種出場方式。最好的是「換股日不符合條件才換＋從買進後高點回落 30% 提前下車」；
      回落 20% 就跑太容易被洗掉，賺 50% 就走、過熱停利、本益比太貴就賣都會提早賣掉大漲的股票，永遠不賣則會遇到宏達電式的大虧。</p></div>`;
}

let etfState = { kind: "", sort: "avgval", asc: false };
function renderETF() {
  const all = state.data.etfs || [];
  const kinds = ["", ...new Set(all.map((e) => e.kind))];
  const q = (state.etfQ || "").toLowerCase();
  let list = all.filter((e) => (!etfState.kind || e.kind === etfState.kind) && (!q || e.c.toLowerCase().includes(q) || (e.n || "").toLowerCase().includes(q)));
  const k = etfState.sort, dir = etfState.asc ? 1 : -1;
  list = [...list].sort((a, b) => (a[k] == null) - (b[k] == null) || ((a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir));
  const th = (key, t, cls = "") => `<th data-ek="${key}" class="${cls} ${etfState.sort === key ? "sorted" + (etfState.asc ? " asc" : "") : ""}">${t}</th>`;
  $("#page").innerHTML = `
    <div class="filters"><input id="etfQ" type="search" placeholder="代號或名稱，例如 0050、高股息" value="${esc(state.etfQ || "")}">
      <span class="seg" id="etfKind">${kinds.map((x) => `<button data-k="${x}" class="${etfState.kind === x ? "on" : ""}">${x || "全部"}</button>`).join("")}</span>
      <span class="count">共 ${list.length} 檔</span></div>
    <div class="tablebox"><table><thead><tr>${th("c", "ETF", "l")}${th("kind", "類型", "l hide-sm")}${th("p", "收盤")}${th("chg", "今日")}${th("r20", "20日")}${th("r60", "60日", "hide-sm")}${th("r120", "120日", "hide-sm")}${th("b60", "距季線", "hide-sm")}${th("avgval", "均量(億)")}${th("inst20", "法人20日", "hide-sm")}${th("yld", "殖利率", "hide-sm")}</tr></thead>
    <tbody>${list.slice(0, 300).map((e) => `<tr><td class="l"><span class="code">${e.c}</span>${esc(e.n)}${e.mk === "o" ? '<span class="mk" title="上櫃">櫃</span>' : ""}</td><td class="l hide-sm"><span class="ind">${e.kind}</span></td>
      <td>${fmt(e.p)}</td><td>${signed(e.chg)}</td><td>${signed(e.r20, 1)}</td><td class="hide-sm">${signed(e.r60, 1)}</td><td class="hide-sm">${signed(e.r120, 1)}</td>
      <td class="hide-sm">${signed(e.b60, 1)}</td><td>${fmt(e.avgval / 1e8)}</td><td class="hide-sm">${signed(e.inst20)}</td><td class="hide-sm">${fmt(e.yld, 2, "%")}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">ETF 沒有營收與本益比，不評分；依成交金額排序（流動性越好，買賣價差越小）。法人 20 日 = 三大法人 20 日買超 ÷ 成交量。殖利率有資料才顯示。</p>`;
  $("#etfQ").oninput = (e) => { state.etfQ = e.target.value; renderETF(); const el = $("#etfQ"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); };
  $("#etfKind").onclick = (e) => { const b = e.target.closest("button"); if (b) { etfState.kind = b.dataset.k; renderETF(); } };
  $$("th[data-ek]").forEach((h) => h.onclick = () => {
    const key = h.dataset.ek;
    if (etfState.sort === key) etfState.asc = !etfState.asc; else { etfState.sort = key; etfState.asc = key === "c" || key === "kind"; }
    renderETF();
  });
}

// ---------- 第一步：趨勢地圖（趨勢 → 環節 → 龍頭 → 是否上修 → 本益比位階）
const ZONE = { cheap: ["good", "便宜"], fair: ["neutral", "合理"], rich: ["warn", "偏貴"] };
const zoneTag = (m) => m.pe_zone ? `<span class="chip ${ZONE[m.pe_zone][0]}" title="本益比在自己 5 年歷史的 ${m.pe_z > 0 ? "+" : ""}${m.pe_z}σ，過去 ${m.pe_pct}% 的時間比現在便宜">${ZONE[m.pe_zone][1]} ${m.pe_z > 0 ? "+" : ""}${m.pe_z}σ</span>` : NA;
const upTag = (m) => m.rev_up_n == null ? NA : m.rev_up_n >= 3 ? `<span class="chip good">連續上修 ${m.rev_up_n} 月</span>` :
  m.rev_up_n >= 1 ? `<span class="chip neutral">上修 ${m.rev_up_n} 月</span>` : '<span class="chip warn">本月下修</span>';
function renderTrend() {
  const all = { "產業族群": (state.data.groups || []).filter((g) => g.n >= 5), ...(state.data.themes || {}) };
  const names = Object.keys(all).filter((k) => all[k]?.length);
  if (!names.length) { $("#page").innerHTML = '<div class="how"><p>趨勢資料還沒產生（下次每日更新後出現）。</p></div>'; return; }
  const th = state.theme && all[state.theme] ? state.theme : names[0];
  const rows = all[th];
  const S = Object.fromEntries(state.data.stocks.map((s) => [s.c, s]));
  const open = state.segOpen ?? rows[0]?.seg;
  const seg = rows.find((r) => r.seg === open);
  const arrow = (r) => r.accel == null ? "" : r.accel > 5 ? '<span class="up">▲ 加速</span>' : r.accel < -5 ? '<span class="down">▼ 放緩</span>' : "持平";
  const mem = seg ? seg.codes.map((c) => S[c]).filter(Boolean).sort((a, b) => (b.s.score ?? 0) - (a.s.score ?? 0)) : [];
  $("#page").innerHTML = `
    <div class="filters"><span class="seg" id="themeSeg">${names.map((n) => `<button data-t="${esc(n)}" class="${n === th ? "on" : ""}">${esc(n)}</button>`).join("")}</span>
      <span class="note">① 先看族群分數，前段族群才往下看：族群內領先的公司 → 營收預估是否持續上修 → ② 本益比位階</span></div>
    <div class="tablebox"><table><thead><tr><th class="l">族群 / 環節（點選看成員）</th><th>族群分數</th><th>近3月營收年增</th><th>營收創新高</th><th>預估連續上修≥2月</th><th>營收動能</th><th>距季線</th><th class="l">龍頭</th></tr></thead>
      <tbody>${rows.map((r, i) => { const L = S[r.lead]; return `<tr data-seg="${esc(r.seg)}" class="${r.seg === open ? "sel" : ""}">
        <td class="l"><b>${i + 1}. ${esc(r.seg)}</b> <span class="na">${r.n} 家</span></td><td>${bar(r.seg_score)}</td>
        <td>${signed(r.rev3, 0)}</td><td>${r.hi_pct ?? "—"}%</td><td>${r.up_pct ?? "—"}%</td><td>${signed(r.accel, 1, "")} ${arrow(r)}</td><td>${signed(r.b60, 1)}</td>
        <td class="l">${L ? `<a href="#${L.c}" data-open="${L.c}">${esc(L.n)}</a> ${upTag(L.m)} ${zoneTag(L.m)}` : "—"}</td></tr>`; }).join("")}</tbody></table></div>
    ${seg ? `<h3 style="margin:16px 0 6px">${esc(seg.seg)}：成員（依分數排序）</h3>
    <div class="tablebox"><table><thead><tr><th class="starc"></th><th class="l">股票</th><th>分數</th><th class="l">營收預估</th><th>近3月上修</th><th>近3月營收年增</th><th>營收距高點</th><th>營業利益率</th><th class="l">本益比位階（第二步）</th><th class="l">消息</th></tr></thead>
      <tbody>${mem.map((s) => { const m = s.m; return `<tr data-c="${s.c}">
        <td class="starc"><button class="star ${state.watch.has(s.c) ? "on" : ""}" data-star="${s.c}">${state.watch.has(s.c) ? "★" : "☆"}</button></td>
        <td class="l"><span class="code">${s.c}</span>${esc(s.n)}${s.c === seg.lead ? ' <span class="flag good">龍頭</span>' : ""}${m.over ? ' <span class="flag warn">過熱</span>' : ""}</td>
        <td>${s.s.score != null ? `<span class="total">${s.s.score.toFixed(0)}</span>` : NA}</td><td class="l">${upTag(m)}</td><td>${signed(m.rev_rev3, 1)}</td>
        <td>${signed(m.rev3, 0)}</td><td>${signed(m.rev_hi, 1)}</td><td>${m.opm != null ? fmt(m.opm, 1, "%") + (m.opm_chg != null ? ` <span class="na">(${m.opm_chg > 0 ? "+" : ""}${m.opm_chg})</span>` : "") : NA}</td>
        <td class="l">${zoneTag(m)}</td>
        <td class="l">${m.news_up ? `<span class="chip good">法人上調 ${m.news_up}</span>` : ""}${m.news_dn ? ` <span class="chip warn">下調 ${m.news_dn}</span>` : ""}${m.conf_next ? ` <span class="chip neutral">法說 ${md(m.conf_next)}</span>` : ""}</td></tr>`; }).join("")}</tbody></table></div>` : ""}
    <p class="note"><b>環節分數</b> = 各環節間比較：近 3 月營收年增中位數 30%、營收創 12 個月新高的比例 25%、營收動能 20%、營收預估連續上修 ≥ 2 個月的比例 25%。<br>
      <b>營收預估</b> = 用最新月營收推算「未來 12 個月營收」，每月重算；比上個月高就是上修。這是從營收外推，不是法人預估，會比法人慢 1~2 個月，但不受業外一次性收益影響。<br>
      <b>本益比位階</b> = 目前本益比在自己 5 年本益比歷史（取對數）的標準差位置：&lt; −0.5σ 便宜、−0.5σ ~ +0.5σ 合理、&gt; +0.5σ 偏貴。
      用「近四季 EPS」的本益比和歷史比才公平；預估本益比對成長股一定偏低，只在個股頁列為參考。<br>
      <b>營業利益率</b> = 最新季報（今年累計），括號為與上一份季報相比的變化；季報歷史從現在開始累積。<b>消息</b> = 近 30 天新聞標題提到「調升目標價、上修財測」等（Google 新聞），以及即將召開的法說會。<br>
      成員名單是人工整理的初版（data/themes.json），可能有遺漏；不是經過回測驗證的選股策略。</p>`;
  $("#themeSeg").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.theme = b.dataset.t; state.segOpen = null; renderTrend(); } };
  $$("tr[data-seg]").forEach((tr) => tr.onclick = (e) => { if (e.target.closest("a")) return; state.segOpen = tr.dataset.seg; renderTrend(); });
  $$("#page tr[data-c]").forEach((tr) => tr.onclick = (e) => { if (e.target.closest("a,button")) return; showDetail(tr.dataset.c); });
}

// ---------- 回測
function renderBacktest() {
  const bt = state.bt;
  if (!bt) { $("#page").innerHTML = '<div class="how"><p>回測結果還沒產生（需要先補完歷史資料）。</p></div>'; return; }
  const NAMES = { fund: "營收成長分數（計分）", liq: "流動性分數（計分）", peg: "成長性價比 PEG（計分）", chip: "外資持股分數（計分）", qf: "外資持股比例", rev_hi: "營收距 12 月新高", tech: "趨勢（參考）", value: "估值（參考）", sup: "支撐位置（參考）",
    streak: "營收連續年增月數", rev3: "近 3 月營收年增", cum_yoy: "累計營收年增", accel: "營收動能", lv: "成交金額規模", f20: "外資 20 日",
    t20: "投信 20 日", b20: "月乖離", b60: "距季線", r60: "60 日漲幅（動能）", lowvol: "低波動", vr: "量能放大（5 日 / 60 日）", sup_score: "支撐分數", val_cheap: "河流圖位階（低）" };
  const bandTable = (b) => `<table><tr><th>分數區間</th><th>樣本數</th><th>勝率</th><th>平均超額報酬</th></tr>${Object.entries(b).map(([k, v]) =>
    `<tr><td>${k}</td><td>${v.n.toLocaleString()}</td><td>${bar(v.win)}</td><td>${signed(v.avg)}</td></tr>`).join("")}</table>`;
  const sig = (x) => x?.p != null && x.p < 0.05 && x.ic > 0;
  const icTable = (ic) => `<table><tr><th>指標</th><th>全期 IC</th><th>t 值</th><th>p 值</th><th>訓練期</th><th>驗證期</th><th>可信？</th></tr>${Object.keys(NAMES).filter((k) => ic.all[k]).map((k) =>
    `<tr><td>${NAMES[k]}</td><td>${signed(ic.all[k].ic * 100, 1, "")}</td><td>${fmt(ic.all[k].t, 1)}</td><td>${ic.all[k].p != null ? ic.all[k].p.toFixed(3) : "—"}</td>
      <td>${ic.train[k] ? signed(ic.train[k].ic * 100, 1, "") : NA}</td><td>${ic.test[k] ? signed(ic.test[k].ic * 100, 1, "") : NA}</td>
      <td>${sig(ic.train[k]) && sig(ic.test[k]) ? '<span class="flag good">兩期都顯著</span>' : sig(ic.all[k]) ? '<span class="flag">只有全期</span>' : '<span class="na">否</span>'}</td></tr>`).join("")}</table>`;
  const RN = { long_core: "長線（5 檔、60 日）", short_core: "短線（3 檔、20 日）" };
  const st = (x) => x ? `<td>${x.n}</td><td>${x.pos}%</td><td>${signed(x.avg, 1)}</td><td>${signed(x.mkt, 1)}</td><td>${x.beat_mkt ?? "—"}%</td><td>${signed(x.worst, 1)}</td>` : '<td colspan="6" class="na">無樣本</td>';
  const head = '<th>期數</th><th>賺錢</th><th>平均報酬</th><th>同期 0050</th><th>贏 0050</th><th>最差</th>';
  const yearsHtml = bt.years ? Object.entries(bt.years).map(([rid, ys]) => `<h3>${RN[rid] || rid}：各年度</h3>
    <table><tr><th>進場年份</th>${head}</tr>${Object.entries(ys).map(([y, x]) => `<tr><td>${y}</td>${st(x)}</tr>`).join("")}</table>`).join("") : "";
  const icr = bt.ic_regime?.long;
  const regIcHtml = icr && Object.keys(icr.bear || {}).length ? `<h3>多頭 vs 空頭：各指標 60 日預測力（進場時 0050 在半年線上 / 下）</h3>
    <table><tr><th>指標</th><th>多頭 IC</th><th>t 值</th><th>空頭 IC</th><th>t 值</th><th>判讀</th></tr>${Object.keys(NAMES).filter((k) => icr.bull?.[k] && icr.bear?.[k]).map((k) => {
      const a = icr.bull[k], z = icr.bear[k];
      const sigA = Math.abs(a.t ?? 0) > 1.96, sigZ = Math.abs(z.t ?? 0) > 1.96;
      const note = sigA && sigZ && a.ic * z.ic > 0 ? "兩種行情都有效" : sigA && !sigZ ? "只在多頭有效" : !sigA && sigZ ? "只在空頭有效" : sigA && sigZ ? "多空方向相反" : "都不顯著";
      return `<tr><td>${NAMES[k]}</td><td>${signed(a.ic * 100, 1, "")}</td><td>${fmt(a.t, 1)}</td><td>${signed(z.ic * 100, 1, "")}</td><td>${fmt(z.t, 1)}</td><td>${note}</td></tr>`;
    }).join("")}</table><p class="note">空頭期間樣本較少（${icr.bear?.qf?.n ?? icr.bear?.fund?.n ?? "—"} 期），解讀要保守。</p>` : "";
  const regHtml = bt.regime ? Object.entries(bt.regime).map(([rid, g]) => `<h3>${RN[rid] || rid}：進場時大盤位置</h3>
    <table><tr><th>進場時 0050</th>${head}</tr>
      <tr><td>在季線上</td>${st(g.up60?.on)}</tr><tr><td>在季線下</td>${st(g.up60?.off)}</tr>
      <tr><td>在半年線上</td>${st(g.up120?.on)}</tr><tr><td>在半年線下</td>${st(g.up120?.off)}</tr></table>`).join("") : "";
  const sec = (k) => {
    const s = bt.strats[k]; if (!s) return "";
    const c = s.current;
    return `<h3>綜合分數 → ${s.horizon} 個交易日後（${STRAT_NAME[k]}持有期）</h3>
      <p>分數前 20% 的股票平均超額報酬 ${signed(c.all.top20_avg)}、後 20% ${signed(c.all.bot20_avg)}；前 20% 跑贏的期數比例 ${fmt(c.all.top20_win, 1, "%")}。綜合 IC ${signed(c.all.ic * 100, 1, "")}。</p>
      ${bandTable(c.all.bands)}
      <p class="note">驗證期（${bt.train_until} 之後）綜合 IC：目前權重 ${signed(c.test.ic * 100, 1, "")}，資料校正權重 ${signed(s.calibrated.test.ic * 100, 1, "")}
        ${s.calibrated_better_on_test ? "（校正版較好）" : "（校正版沒有比較好）"}。</p>
      <h3>${s.horizon} 日後：各指標預測力（IC ×100，越大越好；|t| &gt; 2 才算可信）</h3>${icTable(bt.ic[k])}`;
  };
  $("#page").innerHTML = `<div class="how">
    <p>回測期間 ${ymdTxt(bt.period[0])} ~ ${ymdTxt(bt.period[1])}，每週一次、共 ${bt.n_dates} 個時間點，只用當時已公布的資料計分。
       <b>勝率</b> = 贏過大盤的比例；<b>大盤</b> = 同期所有評分股票報酬的中位數（隨便亂挑勝率約 50%）；<b>超額報酬</b> = 個股報酬 − 中位數。</p>
    ${yearsHtml}${regHtml}${regIcHtml}
    <p class="note">「期數」是每週一個起點；同一年度裡的起點持有期會重疊，不是互相獨立的樣本。p 值已用 Newey-West 修正重疊。</p>
    ${sec("short")}${sec("long")}
    <p class="note">${bt.notes.join("；")}。回測反映的是過去，市場環境改變時不保證有效。</p></div>`;
}

function renderHow() {
  $("#page").innerHTML = `<div class="how">
    <h3>這個網站的用法：三步驟</h3>
    <p><b>① 趨勢族群</b>：先看族群分數，只往下看前段族群。族群分數 = 族群內公司的近 3 月營收年增中位數 30%、營收創 12 個月新高的比例 25%、營收動能 20%、營收預估連續上修 ≥ 2 個月的比例 25%（族群之間排名）。
       「產業族群」用證交所產業分類；AI 等趨勢用人工整理的產業鏈環節（data/themes.json，可自行修改）。</p>
    <p><b>② 選股名單</b>：市值 ≥ 100 億、營收預估上修中、外資持股 ≥ 30%、近 3 年營收年成長 ≥ 10%，最多 10 檔；每月 11 日後檢查，不符合才換、從買進後高點回落 30% 提前下車，其餘資金放 0050。族群分數只當趨勢參考，不用來篩選（回測篩族群會錯過台積電等大型股）。</p>
    <p><b>大跌加碼名單</b>：0050 距一年高點跌超過 15% 時啟用。候選是營收上修中、前瞻本益比 &lt; −0.5σ 的大型股，依外資持股 40%＋3 年營收成長 40%＋市值 20% 排序取前 10，分批買、持有 12 個月。</p>
    <p><b>③ 持股監控</b>：☆ 加入的股票每天檢查還在不在名單、有沒有從高點回落 30%。不設「賺多少就走」（回測較差）。</p>
    <h3>分數</h3>
    <table><tr><th>面向</th><th>權重</th><th>內容</th></tr>
      <tr><td>基本面</td><td>100%</td><td>營收預估連續上修月數 30%、近 3 月上修幅度 20%、營收距 12 月新高 20%、營收動能 15%、營收連續年增 15%</td></tr>
      <tr><td>技術面</td><td>參考</td><td>站上季線但 ≤ 15%、接近支撐（回測加進分數會變差，只顯示）</td></tr>
      <tr><td>籌碼面</td><td>參考</td><td>外資持股 4 週變化、集保大戶持股 4 週變化、負乖離（同上，只顯示）</td></tr></table>
    <p class="note">各項先在全部股票中排名（0~100），再依權重加總；乖離過大扣 15 分。分數是相對排名，不是上漲機率。
      研究 3 用 2018~2026 資料測過：營收預估上修有效；技術面、籌碼面加進分數反而變差；停利、過熱出場也比較差。</p>
    <h3>營收預估上修</h3>
    <p>每月新營收公布後，用「近 12 個月營收 ×（1 + 近 3 個月年增率）」推估未來 12 個月營收；比上個月的推估高就是上修。從營收出發，不受業外一次性收益影響；但這是外推，不是法人預估，會比法人慢 1~2 個月。</p>
    <h3>本益比位階</h3>
    <p>取自己 5 年的月本益比（取對數）算平均與標準差：&lt; −0.5σ 便宜、−0.5σ ~ +0.5σ 合理、&gt; +0.5σ 偏貴。
      前瞻版＝近四季 EPS ×（1 + 營收預估成長），和自己過去每個月當時的前瞻本益比比較。</p>
    <p class="note">回測：平常時期位階便宜<b>不是</b>好買點（持有 3~6 個月反而較差，營收持續上修的公司常常偏貴還繼續漲）；只有大跌之後，便宜＋上修＋法人持股高＋長期成長的組合才明顯贏 0050。</p>
    <h3>研究結論（2018~2026）</h3>
    <ul>
      <li>有效：營收預估上修、營收創新高、外資持股比例高、近 3 年營收長期成長、分散 10~20 檔、每季換股。</li>
      <li>無效或更差：技術面（站上季線、支撐、乖離）、籌碼變化（外資 4 週變化、法人買賣超）、先篩族群、停利／停損／過熱出場、判斷多空再進場、逢低買、平常時期用本益比位階挑買點。</li>
      <li>單一個股贏 0050 的機率幾乎都 &lt; 50%；組合能贏是因為少數大贏家把平均拉高，所以要買一組、不要押單檔。</li>
      <li>仍不保證：大約一半的月份會輸 0050，0050 仍建議當核心。</li>
    </ul>
    <h3>消息</h3>
    <p class="note">法說會行事曆來自公開資訊觀測站；「法人調升目標價、上修財測」來自 Google 新聞標題自動比對，可能誤判，只當參考、不計分。集保大戶持股每週記錄，累積後才有變化值。</p>
    <h3>使用小技巧</h3>
    <p>☆ 加入持股監控（存在這台裝置的瀏覽器裡）。網址加上 <code>#2330</code> 可直接打開個股。</p>
    <h3>資料狀態</h3>
    <p class="note">收盤價 ${state.data.price_days} 個交易日、法人買賣超 ${state.data.t86_days} 個交易日、本益比月取樣 ${state.data.pe_months} 個月。每個交易日台灣時間約 18:30 自動更新。本頁不是投資建議。</p></div>`;
}

function render() {
  const t = state.tab;
  const isList = !!TABCOLS[t];
  $("#tableWrap").hidden = !isList;
  $("#filters").hidden = !isList;
  const canF = FILTER_TABS.has(t);
  $("#adv").hidden = !canF || !state.advOpen;
  ["#preset", "#advBtn", "#resetBtn"].forEach((x) => ($(x).hidden = !canF));
  $("#page").hidden = isList;
  $("#ruleInfo").hidden = t !== "step2";
  $("#watchInfo").hidden = t !== "watch";
  if (t === "watch") renderStep3();
  if (t === "step2") renderStep2Info();
  if (isList) renderTable();
  else if (t === "etf") renderETF();
  else if (t === "trend") renderTrend();
  else if (t === "bt") renderBacktest();
  else renderHow();
  $("#watchN").textContent = state.watch.size ? `(${state.watch.size})` : "";
}

function setTab(t) {
  state.tab = t;
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  if (DEFAULT_SORT[t]) [state.sort, state.asc] = DEFAULT_SORT[t];
  render();
}

function toggleWatch(code) {
  state.watch.has(code) ? state.watch.delete(code) : state.watch.add(code);
  LS.set("watch", [...state.watch]);
}

// ---------- 個股詳細
async function showDetail(code) {
  const s = state.data.stocks.find((x) => x.c === code);
  if (!s) return;
  if (location.hash !== "#" + code) history.replaceState(null, "", "#" + code);
  const m = s.m, p = m.pct || {};
  const kv = (label, val, key) => `<tr><td>${label}</td><td>${val}</td><td class="pct">${p[key] != null ? `贏過 ${p[key]}%` : ""}</td></tr>`;
  const sub = (label, v) => `<div class="row"><span>${label}</span><span class="track"><span class="fill" style="width:${v ?? 0}%"></span></span><span>${v == null ? "—" : v.toFixed(0)}</span></div>`;
  const chips = signals(s).map(([cls, t]) => `<span class="chip ${cls}">${t}</span>`).join("");
  $("#detail").innerHTML = `
    <div class="dhead">
      <div>
        <h2><span class="code">${s.c}</span>${esc(s.n)} <button class="star big ${state.watch.has(s.c) ? "on" : ""}" data-star="${s.c}">${state.watch.has(s.c) ? "★" : "☆"}</button></h2>
        <div class="ind">${esc(s.ind || "")}${m.liquid ? "" : "　・成交量不足，不列入評分"}　${s.mk === "o" ? "上櫃・" : "上市・"}<a href="https://tw.stock.yahoo.com/quote/${s.c}.${s.mk === "o" ? "TWO" : "TW"}" target="_blank" rel="noopener">Yahoo 股市 →</a></div>
      </div>
      <button class="ghost" id="closeDetail" title="關閉（Esc）">✕</button>
    </div>
    <div class="dtop">
      <div>
        <div class="bigwrap">
          <div class="cur"><span class="big">${s.s.score == null ? "—" : s.s.score.toFixed(0)}</span><span class="sub">基本面分數 ${s.s.d5 != null ? "5日 " + signed(s.s.d5, 1, "") : ""}</span></div>
        </div>
        <div class="subscores">${sub("基本面", s.s.f)}${sub("技術面（參考）", s.s.t)}${sub("籌碼面（參考）", s.s.c)}</div>
      </div>
      <div class="pricebox"><div class="val">${fmt(m.p)}</div><div>${signed(m.chg)} 今日</div><div>${signed(m.r20, 1)} 20 日</div></div>
    </div>
    <table class="kv steps">
      <tr><td>① 族群</td><td>${grpLine(s)}</td></tr>
      <tr><td>① 營收預估</td><td>${upTag(m)} ${m.rev_rev3 != null ? `近 3 個月 ${signed(m.rev_rev3, 1)}` : ""}${m.rev_hi != null && m.rev_hi >= 0 ? ' <span class="chip good">月營收創 12 月新高</span>' : ""}</td></tr>
      <tr><td>② 名單</td><td>${(state.data.step2?.live || []).includes(s.c) ? '<span class="chip good">符合選股名單條件</span>' : '<span class="chip neutral">不符合選股名單條件</span>'}
        ${(state.data.crash?.codes || []).includes(s.c) ? ` <span class="chip ${state.data.crash.active ? "good" : "neutral"}">大跌名單${state.data.crash.active ? "（已啟用）" : ""}</span>` : ""}
        <span class="note">外資持股 ${m.qf ?? "—"}%・3 年營收年成長 ${m.cagr3 ?? "—"}%</span></td></tr>
      <tr><td>位階</td><td>${zoneTag(m)} ${m.fpe_zf != null ? `<span class="note">前瞻 ${m.fpe_zf}σ</span>` : ""} <span class="note">（參考；平常時期不是好買點訊號）</span></td></tr>
      <tr><td>③ 監控</td><td>${(() => { const a = holdAdvice(s); return `<span class="chip ${a.cls}">${a.act}</span>`; })()}</td></tr>
    </table>
    <div class="chips">${chips}</div>

    <h3>營收預估修正（①）</h3><div id="chEst" class="chartbox"></div>
    <div id="newsBox"></div>
    <h3>月營收</h3><div id="chRev" class="chartbox"></div>
    <h3>本益比河流圖（②：標準差位階）</h3><div id="chRiver" class="chartbox"></div>
    <h3>股價與支撐（近    <h3>股價與支撐（近 ${"130"} 個交易日）</h3><div id="chPrice" class="chartbox"></div>
    <div class="supbox">${supportHtml(m)}</div>
    <h3>分價量表（哪個價位成交最多）</h3><div id="chVP" class="chartbox"></div>
    <h3>法人買賣超（張）
      <span class="seg" id="segT86"><button data-k="f" class="on">外資</button><button data-k="i">投信</button><button data-k="s">自營商</button><button data-k="all">合計</button></span></h3>
    <div id="chT86" class="chartbox"></div>
    <h3>分數走勢</h3><div id="chScore" class="chartbox"></div>

    <div class="cols2">
      <div>
        <h3>基本面 ${m.rev_ym ? `<span class="note">（營收 ${rocYm(String(m.rev_ym))}${m.eps_y ? `、財報 ${esc(m.eps_y)} Q${m.eps_q}` : ""}）</span>` : ""}</h3>
        <table class="kv">
          ${kv(LABEL.rev_yoy, signed(m.rev_yoy, 1), "rev_yoy")}
          ${kv("月營收月增率", signed(m.rev_mom, 1))}
          ${kv("近 3 月營收年增", signed(m.rev3, 1))}
          ${kv("近 12 月營收年增", signed(m.rev12, 1))}
          ${kv("營收動能（3 月 − 12 月）", signed(m.accel, 1, ""), "accel")}
          ${kv("連續年增月數", m.streak != null ? m.streak + " 個月" : NA)}
          ${kv(LABEL.cum_yoy, signed(m.cum_yoy, 1), "cum_yoy")}
          ${kv(`${m.est_y ? m.est_y + 1911 : ""} 全年營收推估`, m.est_year ? (m.est_year / 1e5).toFixed(1) + " 億（" + signed(m.est_growth, 1) + "）" : NA)}
          ${kv(LABEL.roe, fmt(m.roe, 1, "%"), "roe")}
          ${kv(LABEL.gm, fmt(m.gm, 1, "%"), "gm")}
          ${kv("今年累計 EPS", fmt(m.eps, 2, " 元"))}
        </table>
      </div>
      <div>
        <h3>籌碼與技術</h3>
        <table class="kv">
          ${kv("外資持股比例", fmt(m.qf, 1, "%"), "qf")}
          ${kv(LABEL.f20, signed(m.f20), "f20")}
          ${kv(LABEL.t20, signed(m.t20), "t20")}
          ${kv(LABEL.i5, signed(m.i5), "i5")}
          ${kv("大戶持股（400 張以上）", m.big400 != null ? `${m.big400.toFixed(1)}%${m.big400_chg != null ? `（4 週 ${signed(m.big400_chg, 2, " 百分點")}）` : "（累積 4 週後顯示變化）"}<br><span class="note">千張大戶 ${fmt(m.big1000, 1, "%")}・參考，尚未驗證</span>` : NA)}
          ${kv(LABEL.r20, signed(m.r20, 1), "r20")}
          ${kv(LABEL.b60, signed(m.b60, 1), "b60")}
          ${kv("20 日均成交金額", m.avgval ? (m.avgval / 1e8).toFixed(2) + " 億" : NA)}
        </table>
        <h3>價值</h3>
        <table class="kv">
          ${kv("本益比", fmt(m.pe, 1, " 倍"))}
          ${kv("預估本益比（Forward）", m.fpe != null ? `${fmt(m.fpe, 1, " 倍")}<br><span class="note">預估 EPS ${m.feps}（成長 ${m.fg > 0 ? "+" : ""}${m.fg}%）</span>` : '<span class="na">不適用</span>')}
          ${kv("本益比位階（σ）", m.pe_z != null ? `${zoneTag(m)}<br><span class="note">目前 ${fmt(m.pe, 1)} 倍、5 年平均 ${m.pe_mean} 倍；過去 ${m.pe_pct}% 的時間比現在便宜；3 年 ${m.pe_z3 != null ? (m.pe_z3 > 0 ? "+" : "") + m.pe_z3 + "σ" : "—"}${m.fpe_z != null ? `；用預估 EPS 算 ${m.fpe_z > 0 ? "+" : ""}${m.fpe_z}σ（成長股一定偏低，僅參考）` : ""}</span>` : NA)}
          ${kv("營業利益率（今年累計）", m.opm != null ? `${fmt(m.opm, 1, "%")}${m.opm_chg != null ? `（比上一份季報 ${signed(m.opm_chg, 2, " 個百分點")}）` : ""}<br><span class="note">${esc(m.opm_q || "")}</span>` : NA)}
          ${kv("預估本益比位階（百分位）", m.fpe_pos != null ? fmt(m.fpe_pos, 0, "%") : NA)}
          ${kv("本益比位階", m.pe_pos != null ? `${m.pe_pos.toFixed(0)}%（5 年 ${m.pe_lo.toFixed(1)}~${m.pe_hi.toFixed(1)} 倍）` : NA)}
          ${kv("殖利率", fmt(m.yld, 2, "%"))}
          ${kv("股價淨值比", fmt(m.pb))}
        </table>
      </div>
    </div>
    <p class="note">「贏過 X%」= 在所有列入評分的股票中，這項數字贏過多少比例的股票。</p>`;
  $("#detail").hidden = false;
  $("#backdrop").hidden = false;
  $("#detail").scrollTop = 0;
  $("#closeDetail").onclick = closeDetail;
  let det;
  try {
    det = await (await fetch(`data/s/${code}.json`, { cache: "no-cache" })).json();
  } catch {
    $$(".chartbox").forEach((el) => (el.innerHTML = '<p class="note">明細資料載入失敗</p>'));
    return;
  }
  drawPrice(det, m);
  drawVP(det, m);
  drawT86(det, "f");
  $("#segT86").onclick = (e) => {
    const k = e.target.dataset.k; if (!k) return;
    $$("#segT86 button").forEach((b) => b.classList.toggle("on", b === e.target));
    drawT86(det, k);
  };
  drawEst(det, m);
  drawNews(det);
  drawRiver(det, m);
  drawRev(det);
  drawScore(det);
}

function grpLine(s) {
  const g = (state.data.groups || []).filter((x) => x.n >= 5);
  const i = g.findIndex((x) => x.seg === s.ind);
  const parts = [];
  if (i >= 0) parts.push(`${esc(s.ind)}：第 ${i + 1} / ${g.length} 名（族群分數 ${g[i].seg_score?.toFixed(0)}）`);
  for (const [th, rows] of Object.entries(state.data.themes || {})) {
    const j = rows.findIndex((r) => r.codes.includes(s.c));
    if (j >= 0) parts.push(`${esc(th)}・${esc(rows[j].seg)}：第 ${j + 1} / ${rows.length} 名${rows[j].lead === s.c ? '　<span class="flag good">龍頭</span>' : ""}`);
  }
  return parts.join("<br>") || NA;
}

function supportHtml(m) {
  const rows = [];
  if (m.sup_last != null) rows.push(`<div><span class="lbl">前波低點</span><b>${m.sup_last}</b>（${m.sup_last_d} 個交易日前）${m.sup_broke ? '　<span class="flag warn">已跌破</span>' : '　<span class="flag good">未跌破</span>'}</div>`);
  if (m.sup != null) rows.push(`<div><span class="lbl">下方最近支撐</span><b>${m.sup}</b>（距離 ${m.sup_dist.toFixed(1)}%）</div>`);
  if (m.vsup != null) rows.push(`<div><span class="lbl">大量支撐</span><b>${m.vsup}</b>（距離 ${m.vsup_dist.toFixed(1)}%）</div>`);
  if (m.vres != null) rows.push(`<div><span class="lbl">上方大量壓力</span><b>${m.vres}</b>（還有 ${((m.vres / m.p - 1) * 100).toFixed(1)}%）</div>`);
  if (m.poc != null) rows.push(`<div><span class="lbl">成交最密集價位</span><b>${m.poc}</b>（股價${m.p >= m.poc ? "在其上方" : "在其下方，上方賣壓較重"}）</div>`);
  return rows.join("") || '<p class="note">價格資料不足，無法判斷支撐。</p>';
}

function signals(s) {
  const m = s.m, out = [];
  if (m.over) out.push(["warn", `乖離過大（月乖離 ${m.b20 > 0 ? "+" : ""}${m.b20?.toFixed(1)}%、季線乖離 ${m.b60 > 0 ? "+" : ""}${m.b60?.toFixed(1)}%）：回測勝率約 50%，分數已扣 15`]);
  else if (m.b20 != null && m.b20 < 0) out.push(["neutral", `回檔中（月乖離 ${m.b20.toFixed(1)}%）`]);
  if (m.sup_broke) out.push(["warn", `跌破前波低點 ${m.sup_last}`]);
  else if (m.sup_dist != null && m.sup_dist <= 4) out.push(["good", `貼近支撐 ${m.sup}（${m.sup_dist.toFixed(1)}%）`]);
  if (m.vsup_dist != null && m.vsup_dist <= 5) out.push(["good", `站在大量支撐 ${m.vsup} 上`]);
  if (m.poc != null && m.p < m.poc) out.push(["warn", `股價在大量區 ${m.poc} 下方`]);
  if (m.accel != null) {
    if (m.accel <= -10) out.push(["warn", `營收動能轉弱（${m.accel.toFixed(1)}）`]);
    else if (m.accel >= 10) out.push(["good", `營收動能轉強（+${m.accel.toFixed(1)}）`]);
  }
  if (m.streak >= 6) out.push(["good", `營收連續 ${m.streak} 個月年增`]);
  if (m.est_growth != null) out.push([m.est_growth >= 0 ? "neutral" : "warn", `全年營收推估 ${m.est_growth > 0 ? "+" : ""}${m.est_growth.toFixed(1)}%`]);
  if (m.qd4 != null && Math.abs(m.qd4) >= 1) out.push([m.qd4 > 0 ? "good" : "warn", `外資持股 4 週 ${m.qd4 > 0 ? "+" : ""}${m.qd4} 個百分點`]);
  if (m.big400_chg != null && Math.abs(m.big400_chg) >= 1) out.push([m.big400_chg > 0 ? "good" : "warn", `大戶持股 4 週 ${m.big400_chg > 0 ? "+" : ""}${m.big400_chg} 個百分點`]);
  if (m.b60 != null && m.b60 < 0) out.push(["neutral", "股價在季線下"]);
  const d5 = s.s.d5;
  if (d5 >= 10) out.push(["good", "近 5 日分數大幅進步"]);
  if (d5 <= -10) out.push(["warn", "近 5 日分數大幅退步"]);
  if (m.rev_hi != null && m.rev_hi >= 0) out.push(["good", "月營收創 12 個月新高"]);
  if (m.rev_up_n >= 3) out.push(["good", `營收預估連續上修 ${m.rev_up_n} 個月`]);
  else if (m.rev_up_n === 0) out.push(["warn", "營收預估本月下修"]);
  if (m.pe_zone) out.push([ZONE[m.pe_zone][0], `本益比位階${ZONE[m.pe_zone][1]}（${m.pe_z > 0 ? "+" : ""}${m.pe_z}σ）`]);
  if (m.news_up) out.push(["good", `近 30 天法人上調 ${m.news_up} 則`]);
  if (m.news_dn) out.push(["warn", `近 30 天法人下調 ${m.news_dn} 則`]);
  return out;
}

const ma = (arr, n) => arr.map((_, i) => i < n - 1 ? null : arr.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n);

function drawPrice(det, m) {
  const ma20 = ma(det.c, 20), ma60 = ma(det.c, 60);
  const hl = [];
  if (m.sup_last != null) hl.push({ v: m.sup_last, label: "前低", cls: m.sup_broke ? "h-warn" : "h-sup" });
  if (m.vsup != null) hl.push({ v: m.vsup, label: "大量撐", cls: "h-vol" });
  if (m.vres != null) hl.push({ v: m.vres, label: "大量壓", cls: "h-res" });
  Charts.line($("#chPrice"), {
    labels: det.d.map(md), hlines: hl, height: 240,
    series: [
      { name: "收盤價", values: det.c, cls: "s-price" },
      { name: "20 日均線", values: ma20, cls: "s-1" },
      { name: "60 日均線", values: ma60, cls: "s-2" },
    ],
    tip: (i) => `<b>${ymdTxt(det.d[i])}</b><br>收盤 ${det.c[i]}<br>20MA ${ma20[i]?.toFixed(2) ?? "—"}<br>60MA ${ma60[i]?.toFixed(2) ?? "—"}<br>成交 ${det.v[i]} 億`,
  });
}

function drawVP(det, m) {
  const vp = det.vp;
  if (!vp) { $("#chVP").innerHTML = '<p class="note">資料不足</p>'; return; }
  Charts.bars($("#chVP"), {
    labels: vp.map((x) => x[0] >= 100 ? x[0].toFixed(0) : x[0].toFixed(1)), values: vp.map((x) => x[1]), height: 150,
    mark: vp.findIndex((x, i) => i === vp.length - 1 || (m.p < (x[0] + vp[Math.min(i + 1, vp.length - 1)][0]) / 2)),
    tip: (i) => `<b>約 ${vp[i][0]} 元</b><br>成交金額 ${vp[i][1]} 億${m.p != null && Math.abs(vp[i][0] - m.p) <= Math.abs(vp[1][0] - vp[0][0]) / 2 ? "<br>（目前股價在這格）" : ""}`,
  });
  $("#chVP").insertAdjacentHTML("beforeend", `<p class="note">近 130 日依價位統計的成交金額，深色長條 = 目前股價所在價位。左低右高。</p>`);
}

function drawT86(det, k) {
  const t = det.t, vals = k === "all" ? t.f.map((_, i) => t.f[i] + t.i[i] + t.s[i]) : t[k];
  const name = { f: "外資", i: "投信", s: "自營商", all: "三大法人合計" }[k];
  Charts.bars($("#chT86"), {
    labels: t.d.map(md), values: vals, signed: true, height: 170,
    tip: (i) => `<b>${ymdTxt(t.d[i])}</b><br>${name} ${vals[i] > 0 ? "買超" : vals[i] < 0 ? "賣超" : ""} ${Math.abs(vals[i]).toLocaleString()} 張`,
  });
  const sum = vals.reduce((a, b) => a + b, 0);
  $("#chT86").insertAdjacentHTML("beforeend", `<p class="note">${name}近 ${vals.length} 日合計 ${signed(sum, 0, " 張")}　（紅 = 買超、綠 = 賣超）</p>`);
}

function drawRiver(det, m) {
  const r = det.river;
  if (!r) { $("#chRiver").innerHTML = '<p class="note">本益比歷史不足 24 個月，或公司虧損沒有本益比，無法畫河流圖。</p>'; return; }
  const n = r.m.length;
  const lines = r.q.map((q) => r.e.map((e) => +(q * e).toFixed(2)));
  const zl = (i) => r.z ? `${r.z[i] > 0 ? "+" : ""}${r.z[i]}σ ${r.q[i]}倍` : `${r.q[i]}倍`;
  const lbl = r.m.map((k) => k === "F" ? "預估" : `${k.slice(0, 4)}/${k.slice(4, 6)}`);
  Charts.line($("#chRiver"), {
    labels: lbl, height: 260,
    xlabels: lbl.map((l, i) => r.m[i] === "F" ? "預估" : (r.m[i].slice(4, 6) === "01" ? l.slice(0, 4) : "")),
    bands: [0, 1, 2, 3].map((i) => ({ lo: lines[i], hi: lines[i + 1], cls: "b" + (i + 1),
      name: r.z ? ["−1σ~−0.5σ（便宜）", "−0.5σ~平均", "平均~+0.5σ", "+0.5σ~+1σ（偏貴）"][i] : `${r.q[i]}~${r.q[i + 1]} 倍` })),
    series: [
      ...lines.map((l, i) => ({ values: l, cls: "qline", endLabel: zl(i) })),
      { name: "股價", values: r.p, cls: "s-price" },
    ],
    tip: (i) => `<b>${r.m[i] === "F" ? "預估（未來 12 個月 EPS）" : lbl[i]}</b><br>股價 ${r.p[i]}<br>${r.m[i] === "F" ? "預估本益比" : "本益比"} ${(r.p[i] / r.e[i]).toFixed(1)} 倍<br>${r.m[i] === "F" ? "預估 EPS" : "近四季 EPS"} ≈ ${r.e[i].toFixed(2)}<br>` +
      r.q.map((q, k) => `${r.z ? zl(k) : q + " 倍"} → ${lines[k][i]}`).reverse().join("<br>"),
  });
  const li = r.z && r.m[n - 1] === "F" ? n - 2 : n - 1;      // 位階用近四季 EPS 那一點（和歷史同基準）
  const last = r.p[li], e = r.e[li], isF = r.m[li] === "F";
  const zone = r.z ? (last < r.q[1] * e ? "便宜區（< −0.5σ）" : last <= r.q[3] * e ? "合理區（±0.5σ）" : "偏貴區（> +0.5σ）")
    : last < r.q[0] * e ? "最下緣（歷史 10% 以下）" : last < r.q[1] * e ? "偏低區" : last < r.q[3] * e ? "中間區" : last < r.q[4] * e ? "偏高區" : "最上緣（歷史 90% 以上）";
  $("#chRiver").insertAdjacentHTML("beforeend", `<p class="note">${isF ? "以預估 EPS 計算，" : ""}目前股價落在河流的<b>${zone}</b>。${r.z ? "各條線 = 5 年本益比（取對數）的平均 ±0.5σ、±1σ。" : ""}歷史 EPS 由「收盤價 ÷ 本益比」反推（近四季合計）；最右邊「預估」點 = 近四季 EPS ×（1 + 營收成長率），假設利潤率不變。</p>`);
}

function drawEst(det, m) {
  const e = det.est;
  if (!e || e.length < 2) { $("#chEst").innerHTML = '<p class="note">營收歷史不足，無法推估。</p>'; return; }
  Charts.line($("#chEst"), {
    labels: e.map((x) => rocYm(x[0]).slice(2)), height: 170,
    series: [{ name: "未來 12 個月營收預估（億）", values: e.map((x) => +(x[1] / 1e5).toFixed(1)), cls: "s-1" }],
    tip: (i) => `<b>${rocYm(e[i][0])} 營收公布後</b><br>未來 12 個月營收預估 ${(e[i][1] / 1e5).toFixed(1)} 億${i ? `<br>比上個月 ${signed((e[i][1] / e[i - 1][1] - 1) * 100, 1)}` : ""}`,
  });
  $("#chEst").insertAdjacentHTML("beforeend", `<p class="note">${m.rev_up_n != null ? `已連續上修 <b>${m.rev_up_n}</b> 個月；近 3 個月上修 ${signed(m.rev_rev3, 1)}。` : ""}每月新營收公布後，用「近 12 個月營收 ×（1 + 近 3 月年增率）」重算一次；線往上 = 營收預估上修。</p>`);
}

function drawNews(det) {
  const n = det.news || [], c = det.confs || [], k = det.cons || [];
  if (!n.length && !c.length && !k.length) { $("#newsBox").innerHTML = ""; return; }
  const dirTxt = { up: ["good", "上修"], down: ["warn", "下修"], flat: ["neutral", "持平"] };
  $("#newsBox").innerHTML = `<h3>法說會與法人消息</h3>
    ${k.length ? `<p class="note"><b>法人共識 EPS（FactSet 調查）</b></p><table class="kv"><tr><td>日期</td><td>EPS 預估</td><td>目標價</td></tr>${[...k].reverse().slice(0, 12).map((x) =>
      `<tr><td>${ymdTxt(x[0])}</td><td>${x[2] ? `<span class="chip ${dirTxt[x[2]][0]}">${dirTxt[x[2]][1]}</span> ` : ""}${x[1] ?? "—"}${x[1] != null ? " 元" : ""}</td><td>${x[3] ?? "—"}${x[3] ? " 元" : ""}</td></tr>`).join("")}</table>` : ""}
    <table class="kv">
    ${c.map((x) => `<tr><td>${ymdTxt(x[0])} 法說會</td><td>${esc(x[1])} ${x[2] ? `<a href="${esc(x[2])}" target="_blank" rel="noopener">簡報 →</a>` : ""}</td></tr>`).join("")}
    ${[...n].reverse().map((x) => `<tr><td>${ymdTxt(x[0])} <span class="chip ${x[1] === "up" ? "good" : "warn"}">${x[1] === "up" ? "上調" : "下調"}</span></td><td><a href="${esc(x[3])}" target="_blank" rel="noopener">${esc(x[2])}</a></td></tr>`).join("")}
  </table><p class="note">法人共識 EPS 來自「FactSet 最新調查」新聞標題（鉅亨、券商發布）；其他新聞來自 Google 新聞標題比對（可能誤判）；法說會來自公開資訊觀測站。
    共識 EPS 從現在開始累積，累積夠了會回測它是否比「營收推估上修」更準，驗證前只當參考、不計分。</p>`;
}

function drawRev(det) {
  const rv = det.rev.filter((x) => x[1] != null);
  if (rv.length < 2) { $("#chRev").innerHTML = '<p class="note">月營收歷史累積中。</p>'; return; }
  Charts.bars($("#chRev"), {
    labels: rv.map((x) => rocYm(x[0]).slice(2)), values: rv.map((x) => +(x[1] / 1e5).toFixed(2)), height: 170,
    tip: (i) => `<b>${rocYm(rv[i][0])}</b><br>營收 ${(rv[i][1] / 1e5).toFixed(2)} 億<br>年增 ${rv[i][2] != null ? signed(rv[i][2], 1) : "—"}`,
  });
  $("#chRev").insertAdjacentHTML("beforeend", '<p class="note">單位：億元。滑過長條可看年增率。</p>');
}

function drawScore(det) {
  if (det.sc.d.length < 2) { $("#chScore").innerHTML = '<p class="note">分數歷史從開始每日更新後累積，過幾天就會出現。</p>'; return; }
  Charts.line($("#chScore"), {
    labels: det.sc.d.map(md), height: 160,
    series: [{ name: "綜合分數", values: det.sc.score, cls: "s-1" }],
    tip: (i) => `<b>${ymdTxt(det.sc.d[i])}</b><br>分數 ${det.sc.score[i] ?? "—"}`,
  });
}

function closeDetail() {
  $("#detail").hidden = true;
  $("#backdrop").hidden = true;
  $("#tip").hidden = true;
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
}

// ---------- 事件
$("#tabs").onclick = (e) => { const b = e.target.closest("button"); if (b) setTab(b.dataset.tab); };
document.addEventListener("click", (e) => {
  const st = e.target.closest("[data-star]");
  if (st) {
    e.stopPropagation(); e.preventDefault();
    toggleWatch(st.dataset.star);
    $$(`[data-star="${st.dataset.star}"]`).forEach((b) => { const on = state.watch.has(st.dataset.star); b.classList.toggle("on", on); b.textContent = on ? "★" : "☆"; });
    $("#watchN").textContent = state.watch.size ? `(${state.watch.size})` : "";
    if (state.tab === "watch") { renderTable(); renderStep3(); }
    return;
  }
  const op = e.target.closest("[data-open]");
  if (op) { e.preventDefault(); showDetail(op.dataset.open); return; }
  const gt = e.target.closest("[data-gotab]");
  if (gt) { e.preventDefault(); setTab(gt.dataset.gotab); return; }
  const gi = e.target.closest("[data-goind]");
  if (gi) { e.preventDefault(); $("#ind").value = gi.dataset.goind; setTab("screen"); return; }
});
$("#tableWrap").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-k]");
  if (th) {
    const k = th.dataset.k;
    if (state.sort === k) state.asc = !state.asc;
    else { state.sort = k; state.asc = k === "name" || k === "ind"; }
    return renderTable();
  }
  const tr = e.target.closest("tr[data-c]");
  if (tr) showDetail(tr.dataset.c);
});
["#q", "#ind"].forEach((s) => $(s).addEventListener("input", render));
$("#preset").addEventListener("change", (e) => {
  const p = PRESETS[e.target.value];
  if (p) { setFilters(p.f); if (!FILTER_TABS.has(state.tab)) setTab("screen"); }
  e.target.value = "";
});
$("#advBtn").onclick = () => { state.advOpen = !state.advOpen; render(); };
$("#resetBtn").onclick = () => { $("#q").value = ""; $("#ind").value = ""; setFilters({}); };
$("#adv").addEventListener("input", (e) => {
  const k = e.target.dataset.f; if (!k) return;
  const cur = state.f[k] || [null, null];
  cur[+e.target.dataset.i] = e.target.value === "" ? null : +e.target.value;
  state.f[k] = cur;
  if (cur[0] == null && cur[1] == null) delete state.f[k];
  LS.set("filters", state.f);
  updateAdvCount();
  renderTable();
});
$("#backdrop").onclick = closeDetail;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });
window.addEventListener("hashchange", () => { const c = location.hash.slice(1); if (c) showDetail(c); });
$("#themeBtn").onclick = () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  LS.set("theme", root.dataset.theme);
};
if (LS.get("theme")) document.documentElement.dataset.theme = LS.get("theme");

fetch("data/latest.json", { cache: "no-cache" })
  .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then((d) => {
    state.data = d;
    const scored = d.stocks.filter((s) => s.s.score != null).length;
    $("#meta").textContent = `資料日期 ${ymdTxt(d.date)}・評分 ${scored} 檔・更新於 ${d.generated_at.replace("T", " ").slice(0, 16)}`;
    const inds = [...new Set(d.stocks.map((s) => s.ind).filter(Boolean))].sort();
    $("#ind").insertAdjacentHTML("beforeend", inds.map((i) => `<option>${esc(i)}</option>`).join(""));
    $("#preset").insertAdjacentHTML("beforeend", Object.entries(PRESETS).map(([k, p]) => `<option value="${k}">${p.t}</option>`).join(""));
    fetch("data/backtest.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : null).then((b) => { state.bt = b; render(); }).catch(() => {});
    renderMarket();
    renderAdv();
    const qt = new URLSearchParams(location.search).get("tab");
    setTab(qt && (TABCOLS[qt] || ["trend", "etf", "bt", "how"].includes(qt)) ? qt : "trend");
    const c = location.hash.slice(1);
    if (c) showDetail(c);
  })
  .catch((e) => { $("#meta").textContent = "資料載入失敗：" + e.message + "（第一次部署需要等 Actions 跑完）"; });
