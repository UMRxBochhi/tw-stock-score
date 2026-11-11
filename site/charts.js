"use strict";
/* 輕量 SVG 圖表：折線（可含色帶）、長條。全部帶 hover 十字線＋tooltip。 */
const Charts = (() => {
  const W = 760, PAD = { l: 8, r: 56, t: 10, b: 22 };
  const tip = () => document.getElementById("tip");

  function niceTicks(min, max, n = 4) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min, step0 = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 0.5);
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    const out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const fmtTick = (v) => Math.abs(v) >= 10000 ? (v / 10000).toFixed(Math.abs(v) >= 1e5 ? 0 : 1) + "萬"
    : Math.abs(v) >= 100 ? v.toFixed(0) : +v.toFixed(2) + "";

  function frame(H, labels, ticks, y, xAt, labelEvery) {
    let g = "";
    ticks.forEach((t) => {
      g += `<line class="grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(t)}" y2="${y(t)}"/>` +
        `<text class="axis" x="${W - PAD.r + 6}" y="${y(t) + 4}">${fmtTick(t)}</text>`;
    });
    const step = labelEvery || Math.max(1, Math.ceil(labels.length / 6));
    labels.forEach((l, i) => {
      if (i % step === 0) g += `<text class="axis" text-anchor="middle" x="${xAt(i)}" y="${H - 4}">${l}</text>`;
    });
    return g;
  }

  function hover(el, n, xAt, H, tipFn, marks) {
    const svg = el.querySelector("svg");
    const cross = svg.querySelector(".cross");
    const dots = svg.querySelector(".dots");
    const move = (ev) => {
      const r = svg.getBoundingClientRect();
      const x = (ev.clientX - r.left) / r.width * W;
      let i = 0, best = Infinity;
      for (let k = 0; k < n; k++) { const d = Math.abs(xAt(k) - x); if (d < best) { best = d; i = k; } }
      if (cross) { cross.setAttribute("x1", xAt(i)); cross.setAttribute("x2", xAt(i)); cross.style.display = ""; }
      if (dots && marks) dots.innerHTML = marks(i);
      const t = tip();
      t.innerHTML = tipFn(i);
      t.hidden = false;
      const tw = t.offsetWidth, th = t.offsetHeight;
      let left = ev.clientX + 14, top = ev.clientY - th - 10;
      if (left + tw > window.innerWidth - 8) left = ev.clientX - tw - 14;
      if (top < 8) top = ev.clientY + 16;
      t.style.left = left + "px"; t.style.top = top + "px";
    };
    const leave = () => { tip().hidden = true; if (cross) cross.style.display = "none"; if (dots) dots.innerHTML = ""; };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", leave);
  }

  /* opt: {labels, series:[{name, values, cls}], bands:[{lo, hi, cls}], height, tip(i), legend:true} */
  function line(el, opt) {
    const H = opt.height || 220, n = opt.labels.length;
    if (n < 2) { el.innerHTML = '<p class="note">資料不足</p>'; return; }
    const all = [];
    opt.series.forEach((s) => s.values.forEach((v) => v != null && all.push(v)));
    (opt.bands || []).forEach((b) => { b.lo.forEach((v) => v != null && all.push(v)); b.hi.forEach((v) => v != null && all.push(v)); });
    (opt.hlines || []).forEach((h) => all.push(h.v));
    const ticks = niceTicks(Math.min(...all), Math.max(...all));
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const y = (v) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
    const xAt = (i) => PAD.l + i / (n - 1) * (W - PAD.l - PAD.r);
    let g = frame(H, opt.xlabels || opt.labels, ticks, y, xAt);
    (opt.bands || []).forEach((b) => {
      let top = "", bot = "";
      for (let i = 0; i < n; i++) if (b.hi[i] != null) top += `${xAt(i)},${y(b.hi[i])} `;
      for (let i = n - 1; i >= 0; i--) if (b.lo[i] != null) bot += `${xAt(i)},${y(b.lo[i])} `;
      g += `<polygon class="band ${b.cls}" points="${top}${bot}"/>`;
    });
    opt.series.forEach((s) => {
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${xAt(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
      });
      g += `<path class="ln ${s.cls}" d="${d}"/>`;
      if (s.endLabel) {
        let li = s.values.length - 1; while (li > 0 && s.values[li] == null) li--;
        g += `<text class="endlbl" x="${W - PAD.r + 6}" y="${y(s.values[li]) + 4}">${s.endLabel}</text>`;
      }
    });
    (opt.hlines || []).forEach((h, hi) => {
      g += `<line class="hline ${h.cls}" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(h.v)}" y2="${y(h.v)}"/>` +
        `<text class="hlbl ${h.cls}" text-anchor="end" x="${W - PAD.r - 4 - hi * 90}" y="${y(h.v) - 4}">${h.label} ${h.v}</text>`;
    });
    g += `<line class="cross" y1="${PAD.t}" y2="${H - PAD.b}" style="display:none"/><g class="dots"></g>`;
    g += `<rect x="0" y="0" width="${W}" height="${H}" fill="transparent"/>`;
    const legend = opt.legend === false ? "" : `<div class="legend">${opt.series.filter((s) => s.name).map((s) =>
      `<span><i class="key ${s.cls}"></i>${s.name}</span>`).join("")}${(opt.bands || []).filter((b) => b.name).map((b) =>
      `<span><i class="key band ${b.cls}"></i>${b.name}</span>`).join("")}</div>`;
    el.innerHTML = `${legend}<svg viewBox="0 0 ${W} ${H}" class="chart" role="img">${g}</svg>`;
    const marks = (i) => opt.series.map((s) => s.values[i] == null ? "" :
      `<circle class="dot ${s.cls}" cx="${xAt(i)}" cy="${y(s.values[i])}" r="4"/>`).join("");
    hover(el, n, xAt, H, opt.tip, marks);
  }

  /* opt: {labels, values, height, tip(i), signed} 正值上、負值下 */
  function bars(el, opt) {
    const H = opt.height || 160, n = opt.labels.length;
    if (!n) { el.innerHTML = '<p class="note">資料不足</p>'; return; }
    const vals = opt.values.filter((v) => v != null);
    const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const y = (v) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
    const slot = (W - PAD.l - PAD.r) / n;
    const xAt = (i) => PAD.l + slot * (i + 0.5);
    let g = frame(H, opt.labels, ticks, y, xAt);
    const bw = Math.max(2, Math.min(28, slot - 2));
    opt.values.forEach((v, i) => {
      if (v == null || v === 0) return;
      const top = y(Math.max(v, 0)), bot = y(Math.min(v, 0));
      const cls = (opt.signed ? (v > 0 ? "pos" : "neg") : "pos") + (opt.mark === i ? " cur" : "");
      const h = Math.max(1, bot - top), r = Math.min(4, bw / 2, h);
      // 只在資料端圓角
      const x = xAt(i) - bw / 2;
      const d = v > 0
        ? `M${x},${bot}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${bot}Z`
        : `M${x},${top}V${bot - r}Q${x},${bot} ${x + r},${bot}H${x + bw - r}Q${x + bw},${bot} ${x + bw},${bot - r}V${top}Z`;
      g += `<path class="bar ${cls}" d="${d}"/>`;
    });
    g += `<line class="zero" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(0)}" y2="${y(0)}"/>`;
    g += `<line class="cross" y1="${PAD.t}" y2="${H - PAD.b}" style="display:none"/>`;
    g += `<rect x="0" y="0" width="${W}" height="${H}" fill="transparent"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img">${g}</svg>`;
    hover(el, n, xAt, H, opt.tip);
  }

  function spark(values, w = 120, h = 32) {
    const v = values.filter((x) => x != null);
    if (v.length < 2) return "";
    const lo = Math.min(...v), hi = Math.max(...v);
    const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 2 - (x - lo) / ((hi - lo) || 1) * (h - 4)).toFixed(1)}`).join(" ");
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><polyline points="${pts}"/></svg>`;
  }

  return { line, bars, spark };
})();
