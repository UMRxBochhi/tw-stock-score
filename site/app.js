"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const LS = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};
const state = {
  data: null, tab: "win", sort: null, asc: false,
  f: LS.get("filters", {}),            // 進階篩選 {key: [min, max]}
  watch: new Set(LS.get("watch", [])), // 自選股
  strat: LS.get("strat", "short"),      // 目前策略：short / long
};
const STRAT_NAME = { short: "短線", long: "長線" };
const VALUE_YIELD = 4.5, VALUE_PEPOS = 40;

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
  short: { t: "短線分數", short: "短線", g: (s) => s.s.short, kind: "total", grp: "分數" },
  long: { t: "長線分數", short: "長線", g: (s) => s.s.long, kind: "total", grp: "分數" },
  d5: { t: "5 日分數變化（目前策略）", short: "5日Δ", g: (s) => s.s["d5_" + state.strat], kind: "signed1", suf: "", grp: "分數" },
  liq: { t: "流動性分數（成交金額規模）", short: "流動性", g: (s) => s.s.liq, kind: "bar", grp: "分數" },
  value: { t: "估值（參考，不計分）", short: "估值*", g: (s) => s.s.value, kind: "bar", grp: "分數" },
  sup: { t: "支撐位置（參考，不計分）", short: "支撐*", g: (s) => s.s.sup, kind: "bar", grp: "分數" },
  fund: { t: "營收成長分數", short: "營收", g: (s) => s.s.fund, kind: "bar", grp: "分數" },
  chip: { t: "外資分數", short: "外資", g: (s) => s.s.chip, kind: "bar", grp: "分數" },
  tech: { t: "趨勢（參考，不計分）", short: "趨勢*", g: (s) => s.s.tech, kind: "bar", grp: "分數" },
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
  i5: { t: "法人 5 日買超 / 成交量 %", short: "法人5日", g: (s) => s.m.i5, kind: "signed", grp: "籌碼" },
};
const LABEL = { rev_yoy: "月營收年增率", accel: "營收動能", cum_yoy: "累計營收年增率", roe: "ROE（年化）", gm: "毛利率",
  f20: "外資 20 日買超 / 成交量", t20: "投信 20 日買超 / 成交量", i5: "三大法人 5 日買超 / 成交量",
  r20: "20 日漲跌幅", b60: "股價相對 60 日均線（季線）", streak: "營收連續年增月數", rev3: "近 3 月營收年增", pe_cheap: "本益比位階（越低越好）", yld: "殖利率", pb_cheap: "股價淨值比（越低越好）", val_cheap: "河流圖位階（預估本益比優先；位階 30% = 70 分）" };

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
  pick: ["p", "chg", "b20", "b60", "long", "short", "streak", "rev3", "accel", "f20", "avgval", "vpos"],
  win: ["p", "chg", "b20", "b60", "short", "long", "d5", "fund", "liq", "chip", "value"],
  value: ["p", "yld", "pe", "fpe", "vpos", "sup_dist", "accel", "long"],
  watch: ["p", "chg", "r20", "short", "long", "d5", "fpe", "vpos", "sup_dist", "accel"],
  screen: ["p", "chg", "short", "long", "d5", "fpe", "vpos", "sup_dist", "vsup_dist", "accel", "roe", "f20", "t20"],
};
const DEFAULT_SORT = { pick: ["$strat", false], win: ["$strat", false], value: ["long", false], watch: ["$strat", false], screen: ["$strat", false] };
const HIDE_SM = new Set(["b60", "chg", "value", "sup", "fund", "chip", "tech", "liq", "streak", "rev3", "avgval", "vpos", "sup_dist", "accel", "pb", "pe", "d5", "roe", "f20", "t20", "rev_yoy", "vsup_dist", "r20"]);

// ---------- 快速篩選
const PRESETS = {
  grow_trust: { t: "營收成長＋投信買超", f: { rev_yoy: [20, null], accel: [0, null], t20: [1, null] } },
  accel_up: { t: "營收動能轉強", f: { accel: [10, null], streak: [3, null] } },
  accel_down: { t: "營收動能轉弱（留意下修）", f: { accel: [null, -10] } },
  yield_low: { t: "高殖利率＋河流圖低位階", f: { yld: [5, null], vpos: [null, 30] } },
  strong: { t: "強勢股（趨勢≥80、站上季線）", f: { tech: [80, null], b60: [0, null], short: [60, null] } },
  pullback: { t: "回測支撐不破（離前波支撐 4% 內）", f: { sup_broke: [0, 0], sup_dist: [0, 4] } },
  vsupport: { t: "站在大量支撐上（5% 內）", f: { vsup_dist: [0, 5] } },
  broke: { t: "跌破前波低點（避開）", f: { sup_broke: [1, 1] } },
  river_cheap: { t: "河流圖便宜區（預估位階 ≤ 20%）", f: { vpos: [null, 20] } },
  long80: { t: "長線分數 ≥ 80（回測勝率最高的區間）", f: { long: [80, null] } },
  foreign: { t: "外資大買", f: { f20: [5, null] } },
  quality: { t: "高 ROE＋高毛利", f: { roe: [15, null], gm: [30, null] } },
  score_up: { t: "5 日分數大進步", f: { d5: [10, null] } },
};

// ---------- 清單
function baseList() {
  const all = state.data.stocks;
  switch (state.tab) {
    case "pick": {
      if (state.pickAll) return all.filter((s) => (s.m.rules || []).includes(ruleId()));
      const codes = state.data.picks?.[ruleId()] || [];
      return codes.map((c) => all.find((s) => s.c === c)).filter(Boolean);
    }
    case "win": return all.filter((s) => s.s[state.strat] != null);
    case "value": return all.filter((s) => s.m.liquid && s.m.pe > 0 && (s.m.fpe_pos ?? s.m.pe_pos) != null &&
      (s.m.fpe_pos ?? s.m.pe_pos) <= VALUE_PEPOS && (s.m.accel == null || s.m.accel > -10));
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
  for (const [k, [lo, hi]] of Object.entries(state.f)) {
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
      <td class="l"><span class="code">${s.c}</span>${esc(s.n)}${flagHtml(s)}</td>
      <td class="l hide-sm"><span class="ind">${esc(s.ind || "")}</span></td>
      ${cols.map((k) => `<td class="${HIDE_SM.has(k) ? "hide-sm" : ""}">${cell(k, s)}</td>`).join("")}</tr>`).join("");
  let empty = "沒有符合條件的股票。";
  if (state.tab === "watch" && !state.watch.size) empty = "還沒有自選股。在任何清單點 ☆ 就能加入。";
  $("#tableWrap").innerHTML = list.length ? `<table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`
    : `<p class="empty">${empty}</p>`;
}

const ruleId = () => state.strat === "long" ? "long_core" : "short_core";

function flagHtml(s) {
  const m = s.m;
  if ((m.rules || []).includes("long_core") && state.tab !== "pick") return ' <span class="flag good" title="符合長線核心選股規則">長線核心</span>';
  if (m.over) return ' <span class="flag warn" title="20 日乖離 > 20% 或季線乖離 > 50%，回測勝率約 50%，已扣 15 分">乖離過大</span>';
  if (m.sup_broke) return ' <span class="flag warn" title="收盤價跌破最近一個波段低點">破前低</span>';
  if (m.accel != null && m.accel <= -10) return ' <span class="flag warn" title="近 3 月營收年增明顯低於近 12 月">動能轉弱</span>';
  if (m.accel != null && m.accel >= 10 && (m.rev3 ?? 0) > 0) return ' <span class="flag good" title="近 3 月營收年增明顯高於近 12 月">動能轉強</span>';
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
    cards.push(`<div class="card"><div class="lbl">今日漲跌家數（上市股票）</div>
      <div class="val"><span class="up">${m.up}</span> / <span class="down">${m.down}</span></div>
      <div class="breadth" title="上漲 ${m.up}、平盤 ${m.flat}、下跌 ${m.down}"><span class="b-up" style="width:${m.up / tot * 100}%"></span><span class="b-flat" style="width:${m.flat / tot * 100}%"></span><span class="b-down" style="width:${m.down / tot * 100}%"></span></div>
      <div class="note">上漲 ${m.up}・平盤 ${m.flat}・下跌 ${m.down}</div></div>`);
  }
  const inds = state.data.industries || [];
  if (inds.length) {
    const top = [...inds].sort((a, b) => b[state.strat] - a[state.strat]).slice(0, 3);
    cards.push(`<div class="card"><div class="lbl">產業平均${STRAT_NAME[state.strat]}分數 前三</div>${top.map((i) =>
      `<div class="mini"><a href="#" data-goind="${esc(i.ind)}">${esc(i.ind)}</a><b>${i[state.strat].toFixed(0)}</b></div>`).join("")}</div>`);
  }
  const scored = state.data.stocks.filter((s) => s.s.long != null).length;
  cards.push(`<div class="card"><div class="lbl">評分股票</div><div class="val">${scored}</div>
    <div class="note">20 日均成交金額 ≥ ${(state.data.min_avg_value / 1e8).toFixed(1)} 億</div></div>`);
  $("#market").innerHTML = cards.join("");
}

// ---------- 產業頁
function renderIndustries() {
  const k = state.strat;
  const inds = [...(state.data.industries || [])].sort((a, b) => b[k] - a[k]);
  $("#page").innerHTML = `<div class="tablebox"><table>
    <thead><tr><th class="l">產業</th><th>評分家數</th><th>平均短線</th><th>平均長線</th><th>平均今日</th><th>平均 20 日</th><th class="l">${STRAT_NAME[k]}分數前三</th></tr></thead>
    <tbody>${inds.map((i) => `<tr data-goind="${esc(i.ind)}">
      <td class="l"><a href="#">${esc(i.ind)}</a></td><td>${i.n}</td><td>${bar(i.short)}</td><td>${bar(i.long)}</td><td>${signed(i.chg)}</td><td>${signed(i.r20, 1)}</td>
      <td class="l">${i["top_" + k].map(([c, n, t]) => `<a href="#${c}" data-open="${c}">${esc(n)}</a> <span class="na">${t.toFixed(0)}</span>`).join("、")}</td></tr>`).join("")}
    </tbody></table></div><p class="note">依目前策略（${STRAT_NAME[k]}）排序。點產業會切到選股器並只顯示該產業。</p>`;
}

// ---------- 策略選股
function renderRuleInfo() {
  const id = ruleId(), r = state.data.rules?.[id], bt = state.bt?.rules?.[id];
  if (!r) { $("#ruleInfo").innerHTML = ""; return; }
  const isL = r.h === "long";
  const P = (x) => x != null ? x + "%" : "—";
  const row = (lab, tr, te, k) => `<tr><td>${lab}</td><td>${P(tr?.[k])}</td><td>${P(te?.[k])}</td></tr>`;
  const steps = isL ? [
    "<b>篩選</b>：營收連續年增 ≥ 12 個月、20 日均成交金額 ≥ 10 億、不過熱（20 日乖離 ≤ +20% 且季線乖離 ≤ +50%）。三個條件都要符合。",
    "<b>挑選</b>：從候選中挑 5 檔。優先挑「外資 20 日買超比例」較高的；盡量分散在不同產業，不要 5 檔都同一族群。",
    "<b>買進</b>：5 檔等金額買進。過熱的不追，等它回到條件內再說。",
    "<b>持有</b>：約 3 個月（60 個交易日）。期間不用每天看。",
    "<b>檢查與換股</b>：到期時重新篩選。仍符合條件的續抱，不符合的賣出、換成新的候選。",
    "<b>資金控管</b>：回測最差一期 5 檔組合約 −25%，投入金額要是你能承受短期跌 25% 的錢。",
  ] : [
    "<b>篩選</b>：營收連續年增 ≥ 12 個月、營收動能 > 0（近 3 月年增高於近 12 月）、20 日均成交金額 ≥ 10 億、股價在 20 日線下（回檔中）、不過熱。",
    "<b>挑選</b>：從候選中挑 3 檔，優先外資 20 日買超比例高的。",
    "<b>買進</b>：3 檔等金額。",
    "<b>持有</b>：約 1 個月（20 個交易日），到期賣出、重新篩選。",
    "<b>資金控管</b>：回測最差一期 3 檔組合約 −30%，短線波動比長線大，金額要更保守。",
  ];
  $("#ruleInfo").innerHTML = `<div class="rulebox">
    <div><b>${esc(r.t)}</b>　<span class="note">上方切換「短線 / 長線」可換策略</span></div>
    <h3 style="margin:12px 0 4px">操作 SOP（自己用任何看盤軟體也能照做）</h3>
    <ol class="sop">${steps.map((x) => `<li>${x}</li>`).join("")}</ol>
    <div class="cols2">
      <div><h3 style="margin:12px 0 4px">回測（${isL ? "持有 60 日" : "持有 20 日"}）</h3>
        <table class="kv"><tr><td></td><td>訓練期</td><td>驗證期</td></tr>
          ${row("候選股個股勝率（贏過大盤中位數）", bt?.train, bt?.test, "win")}
          ${row(`自己從候選隨便挑 ${r.n} 檔：組合贏過大盤`, bt?.rand_train, bt?.rand_test, "basket_win")}
          ${row(`自己從候選隨便挑 ${r.n} 檔：組合賺錢`, bt?.rand_train, bt?.rand_test, "basket_pos")}
          ${row(`照「外資優先」挑 ${r.n} 檔：個股勝率`, bt?.top_train, bt?.top_test, "win")}
          ${row(`照「外資優先」挑 ${r.n} 檔：組合賺錢`, bt?.top_train, bt?.top_test, "basket_pos")}
          <tr><td>最差一期（外資優先 ${r.n} 檔）</td><td>${bt?.top_train ? signed(bt.top_train.worst, 1) : "—"}</td><td>${bt?.top_test ? signed(bt.top_test.worst, 1) : "—"}</td></tr>
        </table></div>
      <div><h3 style="margin:12px 0 4px">今天的候選</h3>
        <p>符合條件 <b>${state.data.stocks.filter((s) => (s.m.rules || []).includes(id)).length}</b> 檔。下表${state.pickAll ? "是全部候選" : `是外資優先的前 ${r.n} 檔`}。
        <button class="ghost sm" id="pickAllBtn">${state.pickAll ? "只看前 " + r.n + " 檔" : "顯示全部候選"}</button></p>
        <p class="note">回測期間 2024/10~2026/10、每週一次，只用當時已公布的資料。這段期間是 AI 多頭，風格轉換時勝率可能下降。止損規則沒有回測過，未納入。</p></div>
    </div></div>`;
  $("#pickAllBtn").onclick = () => { state.pickAll = !state.pickAll; render(); };
}

// ---------- 自選股勝率推估
async function loadPool() {
  if (state.pool !== undefined) return state.pool;
  try { state.pool = await (await fetch("data/pool.json", { cache: "no-cache" })).json(); } catch { state.pool = null; }
  return state.pool;
}
const bandIdx = (bands, v) => bands.reduce((b, lo, i) => (v >= lo ? i : b), 0);

function estimate(pool, strat, stocks) {
  const P = pool[strat], bands = pool.bands;
  // 單股：同分數區間全期統計
  const agg = bands.map((_, i) => P.flatMap((per) => per[i]));
  const rid = strat === "long" ? "long_core" : "short_core";
  const one = stocks.map((s) => {
    const b = bandIdx(bands, s.s[strat]);
    const hit = (s.m.rules || []).includes(rid) ? 1 : 0;
    const same = agg[b].filter((x) => x[2] === hit);
    const useHit = same.length >= 300;            // 樣本夠才細分
    const xs = useHit ? same : agg[b];
    return { s, b, hit, useHit, n: xs.length, win: xs.filter((x) => x[0] > 0).length / xs.length * 100,
      pos: xs.filter((x) => x[1] > 0).length / xs.length * 100, avg: xs.reduce((a, x) => a + x[1], 0) / xs.length / 10 };
  });
  // 組合：隨機抽歷史時間點，每檔從該期同區間抽一檔（保留同期股票一起漲跌的相關性）
  const pick = (per, o) => {
    for (let d = 0; d < bands.length; d++) for (const k of [o.b - d, o.b + d]) {
      if (!per[k]?.length) continue;
      const c = o.useHit ? per[k].filter((x) => x[2] === o.hit) : per[k];
      const arr = c.length ? c : per[k];
      return arr[Math.floor(Math.random() * arr.length)];
    }
    return null;
  };
  const ex = [], ret = [];
  for (let it = 0; it < 4000; it++) {
    const per = P[Math.floor(Math.random() * P.length)];
    let se = 0, sr = 0, n = 0;
    for (const o of one) { const x = pick(per, o); if (x) { se += x[0]; sr += x[1]; n++; } }
    if (n) { ex.push(se / n / 10); ret.push(sr / n / 10); }
  }
  ret.sort((a, b) => a - b);
  const q = (p) => ret[Math.floor(p * (ret.length - 1))];
  return { one, win: ex.filter((x) => x > 0).length / ex.length * 100, pos: ret.filter((x) => x > 0).length / ret.length * 100,
    avg: ret.reduce((a, x) => a + x, 0) / ret.length, p10: q(0.1), p50: q(0.5), p90: q(0.9) };
}

async function renderWatchInfo() {
  const box = $("#watchInfo");
  const strat = state.strat, list = state.data.stocks.filter((s) => state.watch.has(s.c));
  const scored = list.filter((s) => s.s[strat] != null), unscored = list.filter((s) => s.s[strat] == null);
  if (!list.length) { box.innerHTML = ""; return; }
  box.innerHTML = '<div class="rulebox"><p class="note">推估中…</p></div>';
  const pool = await loadPool();
  if (!pool || !pool[strat]) { box.innerHTML = '<div class="rulebox"><p class="note">推估資料還沒產生（需要先跑回測）。</p></div>'; return; }
  if (!scored.length) { box.innerHTML = '<div class="rulebox"><p class="note">自選股都沒有分數（成交量不足或資料不足），無法推估。</p></div>'; return; }
  const r = estimate(pool, strat, scored), H = pool.h[strat];
  const pc = (v) => `${v.toFixed(0)}%`;
  box.innerHTML = `<div class="rulebox">
    <b>自選股推估（${STRAT_NAME[strat]}：持有 ${H} 個交易日，約 ${strat === "long" ? "3 個月" : "1 個月"}）</b>
    <span class="note">　上方切換短線 / 長線可換持有期間</span>
    <div class="market">
      <div class="card"><div class="lbl">組合贏過大盤（中位數）</div><div class="val">${pc(r.win)}</div><div class="note">${scored.length} 檔等金額</div></div>
      <div class="card"><div class="lbl">組合賺錢機率</div><div class="val">${pc(r.pos)}</div><div class="note">平均報酬 ${signed(r.avg, 1)}</div></div>
      <div class="card"><div class="lbl">報酬範圍（10% / 50% / 90%）</div><div class="val" style="font-size:18px">${signed(r.p10, 1)}　${signed(r.p50, 1)}　${signed(r.p90, 1)}</div>
        <div class="note">10 次裡約有 1 次比左邊更差</div></div>
    </div>
    <table class="kv" style="margin-top:10px"><tr><td>股票</td><td>${STRAT_NAME[strat]}分數</td><td>贏過大盤</td><td>賺錢機率</td><td>歷史平均報酬</td><td>樣本</td></tr>
      ${r.one.map((o) => `<tr><td><a href="#${o.s.c}" data-open="${o.s.c}">${o.s.c} ${esc(o.s.n)}</a>${(o.s.m.rules || []).length ? ' <span class="flag good">符合策略</span>' : ""}${o.s.m.over ? ' <span class="flag warn">乖離過大</span>' : ""}</td>
        <td>${o.s.s[strat].toFixed(0)}（${pool.bands[o.b]}~${pool.bands[o.b + 1] ?? 100}${o.useHit ? (o.hit ? "、符合策略" : "、不符合策略") : ""}）</td><td>${pc(o.win)}</td><td>${pc(o.pos)}</td><td>${signed(o.avg, 1)}</td><td>${o.n.toLocaleString()}</td></tr>`).join("")}
    </table>
    ${unscored.length ? `<p class="note">沒有分數、未納入推估：${unscored.map((s) => esc(s.n)).join("、")}（成交量不足或資料不足）</p>` : ""}
    <p class="note">推估方式：用 2024/10 起的回測，找「同分數區間、且同樣符合／不符合策略條件」的股票後來的實際表現（樣本不足 300 時只看分數區間）。組合是隨機抽歷史時間點、每檔從同期同區間各抽一檔，模擬 4,000 次，
      保留同期股票一起漲跌的特性。這是歷史統計，個股本身的消息（財報、事件）不在模型內；回測期間為 AI 多頭，風格轉換時可能失準。</p>
  </div>`;
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
    <tbody>${list.slice(0, 300).map((e) => `<tr><td class="l"><span class="code">${e.c}</span>${esc(e.n)}</td><td class="l hide-sm"><span class="ind">${e.kind}</span></td>
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

// ---------- 回測
function btBand(strat, score) {
  const r = state.bt?.strats?.[strat]?.current?.all?.bands;
  if (!r || score == null) return null;
  const k = Object.keys(r).find((b) => { const [lo, hi] = b.split("-").map(Number); return score >= lo && (score < hi || hi === 100); });
  return k ? { band: k, ...r[k] } : null;
}

function renderBacktest() {
  const bt = state.bt;
  if (!bt) { $("#page").innerHTML = '<div class="how"><p>回測結果還沒產生（需要先補完歷史資料）。</p></div>'; return; }
  const NAMES = { fund: "基本面", chip: "籌碼面", tech: "趨勢", value: "估值", sup: "支撐位置",
    cum_yoy: "累計營收年增", accel: "營收動能", f20: "外資 20 日", t20: "投信 20 日", b60: "距季線", sup_score: "支撐分數", val_cheap: "河流圖位階（低）" };
  const bandTable = (b) => `<table><tr><th>分數區間</th><th>樣本數</th><th>勝率</th><th>平均超額報酬</th></tr>${Object.entries(b).map(([k, v]) =>
    `<tr><td>${k}</td><td>${v.n.toLocaleString()}</td><td>${bar(v.win)}</td><td>${signed(v.avg)}</td></tr>`).join("")}</table>`;
  const icTable = (ic) => `<table><tr><th>指標</th><th>全期 IC</th><th>t 值</th><th>訓練期</th><th>驗證期</th></tr>${Object.keys(NAMES).filter((k) => ic.all[k]).map((k) =>
    `<tr><td>${NAMES[k]}</td><td>${signed(ic.all[k].ic * 100, 1, "")}</td><td>${fmt(ic.all[k].t, 1)}</td><td>${ic.train[k] ? signed(ic.train[k].ic * 100, 1, "") : NA}</td><td>${ic.test[k] ? signed(ic.test[k].ic * 100, 1, "") : NA}</td></tr>`).join("")}</table>`;
  const sec = (k) => {
    const s = bt.strats[k]; if (!s) return "";
    const c = s.current;
    return `<h3>${STRAT_NAME[k]}分數 → ${s.horizon} 個交易日後</h3>
      <p>分數前 20% 的股票平均超額報酬 ${signed(c.all.top20_avg)}、後 20% ${signed(c.all.bot20_avg)}；前 20% 跑贏的期數比例 ${fmt(c.all.top20_win, 1, "%")}。綜合 IC ${signed(c.all.ic * 100, 1, "")}。</p>
      ${bandTable(c.all.bands)}
      <p class="note">驗證期（${bt.train_until} 之後）綜合 IC：目前權重 ${signed(c.test.ic * 100, 1, "")}，資料校正權重 ${signed(s.calibrated.test.ic * 100, 1, "")}
        ${s.calibrated_better_on_test ? "（校正版較好）" : "（校正版沒有比較好）"}。</p>
      <h3>${STRAT_NAME[k]}：各指標預測力（IC ×100，越大越好；|t| &gt; 2 才算可信）</h3>${icTable(bt.ic[k])}`;
  };
  $("#page").innerHTML = `<div class="how">
    <p>回測期間 ${ymdTxt(bt.period[0])} ~ ${ymdTxt(bt.period[1])}，每週一次、共 ${bt.n_dates} 個時間點，只用當時已公布的資料計分。
       <b>勝率</b> = 超額報酬 &gt; 0 的比例；<b>超額報酬</b> = 個股報酬 − 同期所有評分股票平均。</p>
    ${sec("short")}${sec("long")}
    <p class="note">${bt.notes.join("；")}。回測反映的是過去，市場環境改變時不保證有效。</p></div>`;
}

function renderHow() {
  const w = state.data.weights;
  const pct = (x) => Math.round(x * 100) + "%";
  const list = (o) => Object.entries(o).map(([k, v]) => `<tr><td>${LABEL[k] || k}</td><td>${pct(v)}</td></tr>`).join("");
  $("#page").innerHTML = `<div class="how">
    <h3>分數怎麼來的：依回測結果設計</h3>
    <p>2024/10~2026/10 的回測（每週一次、只用當時已公布的資料）中，<b>真正有預測力的只有三類</b>：營收成長（尤其是連續年增月數）、流動性（成交金額規模）、外資買超。
       估值（河流圖）、支撐位置、趨勢在回測中沒有預測力，因此<b>不計分</b>，但仍顯示在個股頁供參考。</p>
    <table><tr><th>面向</th><th>短線</th><th>長線</th></tr>
      ${["fund", "liq", "chip"].map((k) => `<tr><td>${{ fund: "營收成長（連續年增、近3月、動能、累計）", liq: "流動性（20 日均成交金額排名）", chip: "外資 20 日買超" }[k]}</td>
        <td>${w.strats.short[k] ? pct(w.strats.short[k]) : "—"}</td><td>${w.strats.long[k] ? pct(w.strats.long[k]) : "—"}</td></tr>`).join("")}</table>
    <p><b>乖離過大扣分</b>：20 日乖離 &gt; +20% 或季線乖離 &gt; +50% 的股票，回測中勝率掉到 50% 上下（雖然有些會繼續噴，但等於丟銅板），分數扣 15 分、也不列入策略選股。
       短線選股另外要求股價在 20 日線下（回檔才買），回測勝率較穩定。</p>
    <p class="note">回測期間是 AI 多頭行情，大型成長股表現特別好；市場風格轉換時，這些規律可能減弱。分數與勝率請當參考，不是保證。</p>
    <h3>支撐位置（參考）</h3>
    <p><b>前波支撐</b>：近 130 個交易日內的波段低點（前後各 10 日內最低）。收盤價跌破最近一個波段低點 = <b>破前低</b>，分數直接給很低。<br>
       <b>大量支撐</b>：把近 130 日的成交金額依價位分 24 格（分價量表），成交特別多的價位代表很多人在那裡買，常成為支撐或壓力。<br>
       分數依「離支撐多近」給分：沒跌破且在支撐上方 3% 內 100 分、6% 內 85、10% 內 65、15% 內 45，更遠 30。兩種支撐各算一次取平均。</p>
    <h3>估值（參考）</h3><table>${list(w.value)}</table>
    <p class="note">本益比來自證交所，用的是「已正式申報」的近四季 EPS。財報季期間（法說會後到申報期限前），高成長股的本益比會暫時偏高，申報後自動修正。
       <b>預估本益比（Forward PE）</b>：沒有免費的法人預估，改用「近四季 EPS ×（1 + 預估成長率）」推估未來 12 個月 EPS，
       成長率 = 近 3 月與近 12 月營收年增率平均（限制在 −50%~+80%），假設利潤率不變。估值分數用預估本益比在自己 5 年本益比歷史中的位階，直接換算（位階 30% = 70 分）。
       建材營造、金融保險業的營收與獲利關係不穩定，改用一般本益比位階。</p>
    <p>每一項指標（支撐位置除外）先在「列入評分的股票」裡排名，換成 0~100 的百分位（贏過多少比例的股票），再依權重加總。
       分數是<b>相對排名</b>：70 分代表綜合起來贏過約七成的股票，不是「70% 機率會漲」。</p>
    <p>列入評分：一般上市股票（排除 ETF），且近 20 日平均成交金額 ≥ ${(state.data.min_avg_value / 1e8).toFixed(1)} 億。某項資料缺漏時，用其餘指標重新分配權重。</p>
    <h3>營收成長</h3><table>${list(w.fund)}</table>
    <p class="note"><b>營收動能</b> = 近 3 個月營收年增率 − 近 12 個月營收年增率。正值代表成長在加速，負值代表成長在放緩；
       ≤ −10 會標示「動能轉弱」，可視為營收預期可能下修的訊號。<br>
       <b>全年營收推估</b> = 今年已公布的累計營收 + 去年剩餘月份營收 ×（1 + 近 3 月年增率）。這是簡單外推，不是法人預估。</p>
    <h3>外資</h3><table>${list(w.chip)}</table>
    <p class="note">買超比例 = 期間內法人買超股數 ÷ 同期間總成交股數，大型股小型股才能公平比較。</p>
    <h3>趨勢（參考）</h3><table>${list(w.tech)}</table>
    <h3>本益比位階與河流圖</h3>
    <p>每月取樣一次本益比，共 5 年。位階 = 歷史取樣中比現在低的比例。河流圖的各條線 = 歷史本益比的 10/30/50/70/90 百分位 × 當時的近四季 EPS（由收盤價 ÷ 本益比反推）。
       股價在下緣代表以自己的歷史看偏便宜，上緣代表偏貴。</p>
    <h3>價值股清單</h3>
    <p>河流圖位階（有預估用預估）≤ ${VALUE_PEPOS}%、本益比 &gt; 0、營收動能沒有轉弱，依長線分數排序。</p>
    <h3>使用小技巧</h3>
    <p>☆ 加入自選股（存在這台裝置的瀏覽器裡）。篩選條件會記住，所有清單共用。網址加上 <code>#2330</code> 可直接打開個股。</p>
    <h3>資料狀態</h3>
    <p class="note">收盤價 ${state.data.price_days} 個交易日、法人買賣超 ${state.data.t86_days} 個交易日、本益比月取樣 ${state.data.pe_months} 個月。
       每個交易日台灣時間約 18:30 自動更新。</p></div>`;
}

function render() {
  const t = state.tab;
  const isList = !!TABCOLS[t];
  $("#tableWrap").hidden = !isList;
  $("#filters").hidden = !isList;
  $("#adv").hidden = !isList || !state.advOpen;
  $("#page").hidden = isList;
  $("#ruleInfo").hidden = t !== "pick";
  $("#watchInfo").hidden = t !== "watch";
  if (t === "watch") renderWatchInfo();
  if (t === "pick") renderRuleInfo();
  if (isList) renderTable();
  else if (t === "etf") renderETF();
  else if (t === "ind") renderIndustries();
  else if (t === "bt") renderBacktest();
  else renderHow();
  $("#watchN").textContent = state.watch.size ? `(${state.watch.size})` : "";
}

function setTab(t) {
  state.tab = t;
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  if (DEFAULT_SORT[t]) {
    [state.sort, state.asc] = DEFAULT_SORT[t];
    if (state.sort === "$strat") state.sort = state.strat;
  }
  render();
}

function setStrat(k) {
  state.strat = k;
  LS.set("strat", k);
  $$("#strat button").forEach((b) => b.classList.toggle("on", b.dataset.s === k));
  if (state.sort === "short" || state.sort === "long") state.sort = k;
  renderMarket();
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
        <div class="ind">${esc(s.ind || "")}${m.liquid ? "" : "　・成交量不足，不列入評分"}　<a href="https://tw.stock.yahoo.com/quote/${s.c}.TW" target="_blank" rel="noopener">Yahoo 股市 →</a></div>
      </div>
      <button class="ghost" id="closeDetail" title="關閉（Esc）">✕</button>
    </div>
    <div class="dtop">
      <div>
        <div class="bigwrap">
          <div class="${state.strat === "short" ? "cur" : ""}"><span class="big">${s.s.short == null ? "—" : s.s.short.toFixed(0)}</span><span class="sub">短線 ${s.s.d5_short != null ? "5日 " + signed(s.s.d5_short, 1, "") : ""}</span></div>
          <div class="${state.strat === "long" ? "cur" : ""}"><span class="big">${s.s.long == null ? "—" : s.s.long.toFixed(0)}</span><span class="sub">長線 ${s.s.d5_long != null ? "5日 " + signed(s.s.d5_long, 1, "") : ""}</span></div>
        </div>
        <div class="subscores">${sub("營收成長", s.s.fund)}${sub("流動性", s.s.liq)}${sub("外資", s.s.chip)}
          <div class="note" style="margin:8px 0 2px">參考（回測無預測力，不計分）</div>${sub("估值", s.s.value)}${sub("支撐位置", s.s.sup)}${sub("趨勢", s.s.tech)}</div>
        ${(m.rules || []).map((r) => `<span class="chip good">符合${r === "long_core" ? "長線核心" : "短線"}選股規則</span>`).join(" ")}
      </div>
      <div class="pricebox"><div class="val">${fmt(m.p)}</div><div>${signed(m.chg)} 今日</div><div>${signed(m.r20, 1)} 20 日</div></div>
    </div>
    <div class="chips">${chips}</div>
    ${["short", "long"].map((k) => { const b = btBand(k, s.s[k]); return b ? `<p class="note">${STRAT_NAME[k]} ${b.band} 分的股票，回測中 ${bt_h(k)} 日後勝率 <b>${b.win}%</b>、平均超額 ${signed(b.avg)}（樣本 ${b.n.toLocaleString()}）</p>` : ""; }).join("")}

    <h3>股價與支撐（近 ${"130"} 個交易日）</h3><div id="chPrice" class="chartbox"></div>
    <div class="supbox">${supportHtml(m)}</div>
    <h3>分價量表（哪個價位成交最多）</h3><div id="chVP" class="chartbox"></div>
    <h3>法人買賣超（張）
      <span class="seg" id="segT86"><button data-k="f" class="on">外資</button><button data-k="i">投信</button><button data-k="s">自營商</button><button data-k="all">合計</button></span></h3>
    <div id="chT86" class="chartbox"></div>
    <h3>本益比河流圖</h3><div id="chRiver" class="chartbox"></div>
    <h3>月營收</h3><div id="chRev" class="chartbox"></div>
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
          ${kv(LABEL.f20, signed(m.f20), "f20")}
          ${kv(LABEL.t20, signed(m.t20), "t20")}
          ${kv(LABEL.i5, signed(m.i5), "i5")}
          ${kv(LABEL.r20, signed(m.r20, 1), "r20")}
          ${kv(LABEL.b60, signed(m.b60, 1), "b60")}
          ${kv("20 日均成交金額", m.avgval ? (m.avgval / 1e8).toFixed(2) + " 億" : NA)}
        </table>
        <h3>價值</h3>
        <table class="kv">
          ${kv("本益比", fmt(m.pe, 1, " 倍"))}
          ${kv("預估本益比（Forward）", m.fpe != null ? `${fmt(m.fpe, 1, " 倍")}<br><span class="note">預估 EPS ${m.feps}（成長 ${m.fg > 0 ? "+" : ""}${m.fg}%）</span>` : '<span class="na">不適用</span>')}
          ${kv("預估本益比位階", m.fpe_pos != null ? fmt(m.fpe_pos, 0, "%") : NA)}
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
  drawRiver(det, m);
  drawRev(det);
  drawScore(det);
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

function riverZone(m) {
  const p = m.fpe_pos ?? m.pe_pos;
  if (p == null) return null;
  return p <= 10 ? ["good", "河流圖：非常便宜"] : p <= 30 ? ["good", "河流圖：偏便宜"] : p <= 70 ? ["neutral", "河流圖：合理"]
    : p <= 90 ? ["warn", "河流圖：偏貴"] : ["warn", "河流圖：非常貴"];
}

const bt_h = (k) => state.bt?.strats?.[k]?.horizon ?? "";

function signals(s) {
  const m = s.m, out = [];
  if (m.over) out.push(["warn", `乖離過大（月乖離 ${m.b20 > 0 ? "+" : ""}${m.b20?.toFixed(1)}%、季線乖離 ${m.b60 > 0 ? "+" : ""}${m.b60?.toFixed(1)}%）：回測勝率約 50%，分數已扣 15`]);
  else if (m.b20 != null && m.b20 < 0) out.push(["neutral", `回檔中（月乖離 ${m.b20.toFixed(1)}%）`]);
  const rz = riverZone(m);
  if (rz) out.push([rz[0], `${rz[1]}（${m.fpe_pos != null ? "預估" : ""}位階 ${(m.fpe_pos ?? m.pe_pos).toFixed(0)}%）`]);
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
  if (m.t20 >= 1) out.push(["good", "投信 20 日明顯買超"]);
  if (m.f20 >= 5) out.push(["good", "外資 20 日大買"]);
  if (m.f20 <= -5) out.push(["warn", "外資 20 日大賣"]);
  if (m.b60 != null && m.b60 < 0) out.push(["neutral", "股價在季線下"]);
  const d5 = s.s["d5_" + state.strat];
  if (d5 >= 10) out.push(["good", `近 5 日${STRAT_NAME[state.strat]}分數大幅進步`]);
  if (d5 <= -10) out.push(["warn", `近 5 日${STRAT_NAME[state.strat]}分數大幅退步`]);
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
  const lbl = r.m.map((k) => k === "F" ? "預估" : `${k.slice(0, 4)}/${k.slice(4, 6)}`);
  Charts.line($("#chRiver"), {
    labels: lbl, height: 260,
    xlabels: lbl.map((l, i) => r.m[i] === "F" ? "預估" : (r.m[i].slice(4, 6) === "01" ? l.slice(0, 4) : "")),
    bands: [0, 1, 2, 3].map((i) => ({ lo: lines[i], hi: lines[i + 1], cls: "b" + (i + 1),
      name: `${r.q[i]}~${r.q[i + 1]} 倍` })),
    series: [
      ...lines.map((l, i) => ({ values: l, cls: "qline", endLabel: `${r.q[i]}倍` })),
      { name: "股價", values: r.p, cls: "s-price" },
    ],
    tip: (i) => `<b>${r.m[i] === "F" ? "預估（未來 12 個月 EPS）" : lbl[i]}</b><br>股價 ${r.p[i]}<br>${r.m[i] === "F" ? "預估本益比" : "本益比"} ${(r.p[i] / r.e[i]).toFixed(1)} 倍<br>${r.m[i] === "F" ? "預估 EPS" : "近四季 EPS"} ≈ ${r.e[i].toFixed(2)}<br>` +
      r.q.map((q, k) => `${q} 倍 → ${lines[k][i]}`).reverse().join("<br>"),
  });
  const last = r.p[n - 1], e = r.e[n - 1], isF = r.m[n - 1] === "F";
  const zone = last < r.q[0] * e ? "最下緣（歷史 10% 以下）" : last < r.q[1] * e ? "偏低區" : last < r.q[3] * e ? "中間區" : last < r.q[4] * e ? "偏高區" : "最上緣（歷史 90% 以上）";
  $("#chRiver").insertAdjacentHTML("beforeend", `<p class="note">${isF ? "以預估 EPS 計算，" : ""}目前股價落在河流的<b>${zone}</b>。歷史 EPS 由「收盤價 ÷ 本益比」反推（近四季合計）；最右邊「預估」點 = 近四季 EPS ×（1 + 營收成長率），假設利潤率不變。</p>`);
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
    series: [{ name: "短線", values: det.sc.short, cls: "s-1" }, { name: "長線", values: det.sc.long, cls: "s-2" }],
    tip: (i) => `<b>${ymdTxt(det.sc.d[i])}</b><br>短線 ${det.sc.short[i] ?? "—"}<br>長線 ${det.sc.long[i] ?? "—"}`,
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
    if (state.tab === "watch") { renderTable(); renderWatchInfo(); }
    return;
  }
  const op = e.target.closest("[data-open]");
  if (op) { e.preventDefault(); showDetail(op.dataset.open); return; }
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
  if (p) { setFilters(p.f); if (!TABCOLS[state.tab] || state.tab === "watch") setTab("screen"); }
  e.target.value = "";
});
$("#strat").onclick = (e) => { const b = e.target.closest("button"); if (b) setStrat(b.dataset.s); };
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
    const scored = d.stocks.filter((s) => s.s.long != null).length;
    $("#meta").textContent = `資料日期 ${ymdTxt(d.date)}・評分 ${scored} 檔・更新於 ${d.generated_at.replace("T", " ").slice(0, 16)}`;
    const inds = [...new Set(d.stocks.map((s) => s.ind).filter(Boolean))].sort();
    $("#ind").insertAdjacentHTML("beforeend", inds.map((i) => `<option>${esc(i)}</option>`).join(""));
    $("#preset").insertAdjacentHTML("beforeend", Object.entries(PRESETS).map(([k, p]) => `<option value="${k}">${p.t}</option>`).join(""));
    $$("#strat button").forEach((b) => b.classList.toggle("on", b.dataset.s === state.strat));
    fetch("data/backtest.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : null).then((b) => { state.bt = b; render(); }).catch(() => {});
    renderMarket();
    renderAdv();
    const qt = new URLSearchParams(location.search).get("tab");
    setTab(qt && (TABCOLS[qt] || ["etf", "ind", "bt", "how"].includes(qt)) ? qt : "pick");
    const c = location.hash.slice(1);
    if (c) showDetail(c);
  })
  .catch((e) => { $("#meta").textContent = "資料載入失敗：" + e.message + "（第一次部署需要等 Actions 跑完）"; });
