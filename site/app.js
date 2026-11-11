"use strict";

const $ = (s) => document.querySelector(s);
const state = { data: null, tab: "win", sort: null, asc: false };
const VALUE_YIELD = 4.5;   // 價值股：殖利率門檻（%）
const VALUE_PEPOS = 40;    // 價值股：本益比位階門檻（%）

const LABEL = {
  rev_yoy: "月營收年增率", cum_yoy: "累計營收年增率", roe: "ROE（年化）", gm: "毛利率",
  f20: "外資 20 日買超 / 成交量", t20: "投信 20 日買超 / 成交量", i5: "三大法人 5 日買超 / 成交量",
  r20: "20 日漲跌幅", b60: "股價相對 60 日均線",
};

// ---------- 格式化
const fmt = (v, d = 2, suf = "") => v == null || Number.isNaN(v) ? '<span class="na">—</span>' : `${(+v).toFixed(d)}${suf}`;
const signed = (v, d = 2, suf = "%") => {
  if (v == null) return '<span class="na">—</span>';
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${(+v).toFixed(d)}${suf}</span>`;
};
const bar = (v, cls = "") => v == null ? '<span class="na">—</span>' :
  `<span class="bar ${cls}" title="${v} 分"><span class="track"><span class="fill" style="width:${Math.max(2, v)}%"></span></span><span class="v">${v.toFixed(0)}</span></span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------- 欄位定義
const COLS = {
  win: [
    { k: "rank", t: "#", cls: "rank", get: (_, i) => i + 1, nosort: true },
    { k: "name", t: "股票", cls: "l", get: (s) => s.c, html: (s) => `<span class="code">${s.c}</span>${esc(s.n)}` },
    { k: "ind", t: "產業", cls: "l hide-sm", get: (s) => s.ind || "", html: (s) => `<span class="ind">${esc(s.ind || "")}</span>` },
    { k: "p", t: "收盤", get: (s) => s.m.p, html: (s) => fmt(s.m.p) },
    { k: "chg", t: "今日", cls: "hide-sm", get: (s) => s.m.chg, html: (s) => signed(s.m.chg) },
    { k: "r20", t: "20 日", get: (s) => s.m.r20, html: (s) => signed(s.m.r20, 1) },
    { k: "total", t: "總分", get: (s) => s.s.total, html: (s) => s.s.total == null ? '<span class="na">—</span>' : `<span class="total">${s.s.total.toFixed(0)}</span>` },
    { k: "fund", t: "基本面", cls: "hide-sm", get: (s) => s.s.fund, html: (s) => bar(s.s.fund) },
    { k: "chip", t: "籌碼面", cls: "hide-sm", get: (s) => s.s.chip, html: (s) => bar(s.s.chip) },
    { k: "tech", t: "技術面", cls: "hide-sm", get: (s) => s.s.tech, html: (s) => bar(s.s.tech) },
  ],
  value: [
    { k: "rank", t: "#", cls: "rank", get: (_, i) => i + 1, nosort: true },
    { k: "name", t: "股票", cls: "l", get: (s) => s.c, html: (s) => `<span class="code">${s.c}</span>${esc(s.n)}` },
    { k: "ind", t: "產業", cls: "l hide-sm", get: (s) => s.ind || "", html: (s) => `<span class="ind">${esc(s.ind || "")}</span>` },
    { k: "p", t: "收盤", get: (s) => s.m.p, html: (s) => fmt(s.m.p) },
    { k: "yld", t: "殖利率", get: (s) => s.m.yld, html: (s) => fmt(s.m.yld, 2, "%") },
    { k: "pe", t: "本益比", get: (s) => s.m.pe, html: (s) => fmt(s.m.pe, 1) },
    { k: "pe_pos", t: "本益比位階", get: (s) => s.m.pe_pos, html: (s) => s.m.pe_pos == null ? '<span class="na">累積中</span>' : fmt(s.m.pe_pos, 0, "%") },
    { k: "pb", t: "股價淨值比", cls: "hide-sm", get: (s) => s.m.pb, html: (s) => fmt(s.m.pb) },
    { k: "total", t: "總分", get: (s) => s.s.total, html: (s) => s.s.total == null ? '<span class="na">—</span>' : `<span class="total">${s.s.total.toFixed(0)}</span>` },
  ],
};
COLS.all = COLS.win;
const DEFAULT_SORT = { win: ["total", false], value: ["yld", false], all: ["total", false] };

// ---------- 資料篩選
function rows() {
  const d = state.data;
  const q = $("#q").value.trim().toLowerCase();
  const ind = $("#ind").value;
  const min = +$("#min").value || 0;
  let list = d.stocks;
  if (state.tab === "win") list = list.filter((s) => s.s.total != null);
  if (state.tab === "value") list = list.filter((s) => s.m.liquid && s.m.yld >= VALUE_YIELD && s.m.pe > 0 &&
    (s.m.pe_pos == null || s.m.pe_pos <= VALUE_PEPOS));
  if (q) list = list.filter((s) => s.c.includes(q) || (s.n || "").toLowerCase().includes(q));
  if (ind) list = list.filter((s) => s.ind === ind);
  if (min > 0) list = list.filter((s) => (s.s.total ?? -1) >= min);
  const col = COLS[state.tab].find((c) => c.k === state.sort);
  if (col) {
    const dir = state.asc ? 1 : -1;
    list = [...list].sort((a, b) => {
      const va = col.get(a), vb = col.get(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
  }
  return list;
}

function render() {
  const how = state.tab === "how";
  $("#how").hidden = !how;
  $("#tableWrap").hidden = how;
  $("#filters").hidden = how;
  if (how) return renderHow();
  const cols = COLS[state.tab];
  const list = rows();
  const shown = list.slice(0, 500);
  $("#count").textContent = `共 ${list.length} 檔${list.length > 500 ? "（顯示前 500）" : ""}`;
  const th = cols.map((c) => `<th data-k="${c.k}" class="${c.cls || ""} ${state.sort === c.k ? "sorted" + (state.asc ? " asc" : "") : ""}">${c.t}</th>`).join("");
  const tb = shown.map((s, i) => `<tr data-c="${s.c}">${cols.map((c) =>
    `<td class="${c.cls || ""}">${c.html ? c.html(s, i) : c.get(s, i)}</td>`).join("")}</tr>`).join("");
  $("#tableWrap").innerHTML = list.length ? `<table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`
    : `<p style="padding:20px" class="note">沒有符合條件的股票。</p>`;
}

// ---------- 個股詳細
function showDetail(code) {
  const s = state.data.stocks.find((x) => x.c === code);
  if (!s) return;
  const m = s.m, p = m.pct || {};
  const kv = (label, val, key) => `<tr><td>${label}</td><td>${val}</td><td class="pct">${p[key] != null ? `贏過 ${p[key]}%` : ""}</td></tr>`;
  const sub = (label, v) => `<div class="row"><span>${label}</span><span class="track"><span class="fill" style="width:${v ?? 0}%"></span></span><span>${v == null ? "—" : v.toFixed(0)}</span></div>`;
  let pe = "";
  if (m.pe_pos != null) {
    pe = `<div class="pebar"><span class="mark" style="left:${m.pe_pos}%"></span><span class="lbl" style="left:${Math.min(92, Math.max(8, m.pe_pos))}%">目前 ${m.pe.toFixed(1)} 倍（位階 ${m.pe_pos.toFixed(0)}%）</span></div>
      <div class="pescale"><span>近 5 年低 ${m.pe_lo.toFixed(1)}</span><span>高 ${m.pe_hi.toFixed(1)}</span></div>
      <p class="note">位階 = 過去 ${m.pe_n} 個月的月取樣中，本益比比現在低的比例。越低代表以自己的歷史來看越便宜。</p>`;
  } else {
    pe = `<p class="note">本益比歷史資料不足（需要至少 24 個月），或目前虧損沒有本益比。</p>`;
  }
  $("#detail").innerHTML = `
    <button class="ghost close" id="closeDetail">✕</button>
    <h2><span class="code">${s.c}</span>${esc(s.n)}</h2>
    <div class="ind">${esc(s.ind || "")}${m.liquid ? "" : "　・成交量不足，不列入評分"}</div>
    <div class="bigwrap"><span class="big">${s.s.total == null ? "—" : s.s.total.toFixed(0)}</span><span class="sub">總分 / 100</span></div>
    <div class="subscores">${sub("基本面", s.s.fund)}${sub("籌碼面", s.s.chip)}${sub("技術面", s.s.tech)}</div>
    <p class="note">「贏過 X%」= 在所有列入評分的股票中，這項數字贏過多少比例的股票。</p>

    <h3>價格</h3>
    <table class="kv">
      ${kv("收盤價", fmt(m.p))}
      ${kv("今日漲跌", signed(m.chg))}
      ${kv(LABEL.r20, signed(m.r20, 1), "r20")}
      ${kv(LABEL.b60, signed(m.b60, 1), "b60")}
      ${kv("20 日均成交金額", m.avgval ? (m.avgval / 1e8).toFixed(2) + " 億" : "—")}
    </table>

    <h3>基本面 ${m.rev_ym ? `<span class="note">（營收 ${esc(m.rev_ym)}${m.eps_y ? `、財報 ${esc(m.eps_y)} Q${m.eps_q}` : ""}）</span>` : ""}</h3>
    <table class="kv">
      ${kv(LABEL.rev_yoy, signed(m.rev_yoy, 1), "rev_yoy")}
      ${kv("月營收月增率", signed(m.rev_mom, 1))}
      ${kv(LABEL.cum_yoy, signed(m.cum_yoy, 1), "cum_yoy")}
      ${kv(LABEL.roe, fmt(m.roe, 1, "%"), "roe")}
      ${kv(LABEL.gm, fmt(m.gm, 1, "%"), "gm")}
      ${kv("今年累計 EPS", fmt(m.eps, 2, " 元"))}
    </table>

    <h3>籌碼面</h3>
    <table class="kv">
      ${kv(LABEL.f20, signed(m.f20), "f20")}
      ${kv(LABEL.t20, signed(m.t20), "t20")}
      ${kv(LABEL.i5, signed(m.i5), "i5")}
    </table>

    <h3>價值</h3>
    <table class="kv">
      ${kv("本益比", fmt(m.pe, 1, " 倍"))}
      ${kv("殖利率", fmt(m.yld, 2, "%"))}
      ${kv("股價淨值比", fmt(m.pb))}
    </table>
    ${pe}
    <p class="note" style="margin-top:20px"><a href="https://tw.stock.yahoo.com/quote/${s.c}.TW" target="_blank" rel="noopener">在 Yahoo 股市看 ${s.c} →</a></p>`;
  $("#detail").hidden = false;
  $("#closeDetail").onclick = () => ($("#detail").hidden = true);
}

function renderHow() {
  const w = state.data.weights;
  const pct = (x) => Math.round(x * 100) + "%";
  const list = (o) => Object.entries(o).map(([k, v]) => `<tr><td>${LABEL[k] || k}</td><td>${pct(v)}</td></tr>`).join("");
  $("#how").innerHTML = `
    <h3>總分 = 基本面 ${pct(w.total.fund)} + 籌碼面 ${pct(w.total.chip)} + 技術面 ${pct(w.total.tech)}</h3>
    <p>每一項指標先在「列入評分的股票」裡排名，換成 0~100 的百分位（贏過多少比例的股票），再依權重加總。
       所以分數是<b>相對排名</b>：70 分代表綜合起來贏過約七成的股票，不是「70% 機率會漲」。</p>
    <p>列入評分的條件：一般上市股票（排除 ETF），且近 20 日平均成交金額 ≥ ${(state.data.min_avg_value / 1e8).toFixed(1)} 億。
       某項資料缺漏時，會用其餘有資料的指標重新分配權重。</p>
    <h3>基本面</h3><table>${list(w.fund)}</table>
    <h3>籌碼面</h3><table>${list(w.chip)}</table>
    <p class="note">買超比例 = 期間內法人買超股數 ÷ 同期間總成交股數，用比例才能讓大型股和小型股公平比較。</p>
    <h3>技術面</h3><table>${list(w.tech)}</table>
    <h3>價值股清單</h3>
    <p>條件：殖利率 ≥ ${VALUE_YIELD}%、本益比 &gt; 0、本益比位階 ≤ ${VALUE_PEPOS}%（位階資料還沒累積夠時暫不篩選），依殖利率排序。
       殖利率是用最近一次配息計算的，不是預估值。</p>
    <h3>資料狀態</h3>
    <p class="note">收盤價 ${state.data.price_days} 個交易日、法人買賣超 ${state.data.t86_days} 個交易日、本益比月取樣 ${state.data.pe_months} 個月。
       每個交易日台灣時間約 18:30 自動更新。</p>`;
}

// ---------- 事件
function setTab(t) {
  state.tab = t;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  if (DEFAULT_SORT[t]) [state.sort, state.asc] = DEFAULT_SORT[t];
  render();
}

document.querySelector(".tabs").onclick = (e) => { if (e.target.dataset.tab) setTab(e.target.dataset.tab); };
$("#tableWrap").onclick = (e) => {
  const th = e.target.closest("th");
  if (th) {
    const col = COLS[state.tab].find((c) => c.k === th.dataset.k);
    if (!col || col.nosort) return;
    if (state.sort === col.k) state.asc = !state.asc;
    else { state.sort = col.k; state.asc = col.k === "name" || col.k === "ind"; }
    return render();
  }
  const tr = e.target.closest("tr[data-c]");
  if (tr) showDetail(tr.dataset.c);
};
["#q", "#ind", "#min"].forEach((s) => $(s).addEventListener("input", render));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#detail").hidden = true; });
$("#themeBtn").onclick = () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  localStorage.setItem("theme", root.dataset.theme);
};
if (localStorage.getItem("theme")) document.documentElement.dataset.theme = localStorage.getItem("theme");

fetch("data/latest.json", { cache: "no-cache" })
  .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then((d) => {
    state.data = d;
    const dt = `${d.date.slice(0, 4)}/${d.date.slice(4, 6)}/${d.date.slice(6)}`;
    const scored = d.stocks.filter((s) => s.s.total != null).length;
    $("#meta").textContent = `資料日期 ${dt}・評分 ${scored} 檔・更新於 ${d.generated_at.replace("T", " ").slice(0, 16)}`;
    const inds = [...new Set(d.stocks.map((s) => s.ind).filter(Boolean))].sort();
    $("#ind").insertAdjacentHTML("beforeend", inds.map((i) => `<option>${esc(i)}</option>`).join(""));
    setTab("win");
  })
  .catch((e) => { $("#meta").textContent = "資料載入失敗：" + e.message + "（第一次部署需要等 Actions 跑完）"; });
