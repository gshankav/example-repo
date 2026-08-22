/* Shared helpers: formatting, theme, fetch, and the small SVG chart layer. */

export const fmt = {
  usd(x, dp) {
    if (x === null || x === undefined || !isFinite(x)) return "n/a";
    const d = dp === undefined ? (Math.abs(x) < 1000 ? 2 : 0) : dp;
    return "$" + x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  },
  big(x) {
    if (x === null || x === undefined || !isFinite(x)) return "n/a";
    const a = Math.abs(x);
    if (a >= 1e12) return "$" + (x / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return "$" + (x / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return "$" + (x / 1e6).toFixed(1) + "M";
    return fmt.usd(x, 0);
  },
  pct(x, dp = 1) {
    if (x === null || x === undefined || !isFinite(x)) return "n/a";
    return (x * 100).toFixed(dp) + "%";
  },
  signedPct(x, dp = 1) {
    if (x === null || x === undefined || !isFinite(x)) return "n/a";
    const v = x * 100;
    return (v >= 0 ? "+" : "") + v.toFixed(dp) + "%";
  },
  num(x, dp = 0) {
    if (x === null || x === undefined || !isFinite(x)) return "n/a";
    return x.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  },
  x(v, dp = 2) {
    if (v === null || v === undefined || !isFinite(v)) return "n/a";
    return v.toFixed(dp) + "x";
  },
};

/* Sign is carried by the text itself, so the colour is reinforcement rather
   than the only cue. */
export function deltaClass(x) {
  if (x === null || x === undefined || !isFinite(x)) return "";
  return x >= 0 ? "up" : "down";
}

export const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of [].concat(kids)) if (kid) n.append(kid);
  return n;
};

const SVG = "http://www.w3.org/2000/svg";
export const svg = (tag, attrs = {}) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  }
  return n;
};

/* ---- theme ---------------------------------------------------------- */

export function initTheme() {
  const saved = localStorage.getItem("pm-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const paint = () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark"
      || (!document.documentElement.hasAttribute("data-theme")
          && matchMedia("(prefers-color-scheme: dark)").matches);
    btn.textContent = dark ? "Light" : "Dark";
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  };
  paint();
  btn.addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark"
      || (!document.documentElement.hasAttribute("data-theme")
          && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("pm-theme", next);
    paint();
    document.dispatchEvent(new CustomEvent("themechange"));
  });
}

/* ---- data ----------------------------------------------------------- */

export async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({ error: "unreadable response" }));
  if (!res.ok) {
    const e = new Error(body.detail || body.error || res.statusText);
    e.payload = body;
    e.status = res.status;
    throw e;
  }
  return body;
}

export function banner(text, critical = false) {
  return el("div", { class: "banner" + (critical ? " critical" : "") }, [
    el("span", { class: "icon", text: "!" }),
    el("span", { text }),
  ]);
}

export function renderWarnings(host, warnings) {
  host.replaceChildren();
  for (const w of warnings || []) {
    host.append(banner(w, /UNVERIFIED|failed/i.test(w)));
  }
}

/* ---- tooltip -------------------------------------------------------- */

let tipEl = null;
export const tip = {
  show(x, y, title, rows) {
    if (!tipEl) {
      tipEl = el("div", { class: "tooltip" });
      document.body.append(tipEl);
    }
    tipEl.replaceChildren(
      el("div", { class: "t-title", text: title }),
      ...rows.map(([k, v]) =>
        el("div", { class: "t-row" }, [el("span", { text: k }), el("span", { text: v })])
      )
    );
    tipEl.classList.add("on");
    // Flip before the viewport edge so the tooltip never causes a scrollbar.
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.min(x + 14, innerWidth - w - 8) + "px";
    tipEl.style.top = (y - h - 12 < 8 ? y + 18 : y - h - 12) + "px";
  },
  hide() {
    if (tipEl) tipEl.classList.remove("on");
  },
};

/* ---- charts ---------------------------------------------------------- */

/* A sparkline: one series, so no legend - the card title names it. No axes
   either; this is shape at a glance, and the exact values live in the table
   beside it and in the hover tooltip. */
export function sparkline(host, points, color, label) {
  const W = 260, H = 56, pad = 4;
  host.replaceChildren();
  if (!points || points.length < 2) return;
  const vals = points.map((p) => p.c);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i) => pad + (i * (W - 2 * pad)) / (points.length - 1);
  const y = (v) => H - pad - ((v - lo) / span) * (H - 2 * pad);

  const s = svg("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none",
    height: H, role: "img", "aria-label": `${label} price, last ${points.length} days`,
  });
  s.append(svg("path", {
    class: "series-line", stroke: color,
    d: points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join(""),
  }));
  const last = points.length - 1;
  s.append(svg("circle", { cx: x(last), cy: y(points[last].c), r: 4, fill: color }));

  const cursor = svg("line", {
    class: "axis-line", y1: 0, y2: H, opacity: 0, "stroke-dasharray": "",
  });
  const dot = svg("circle", { r: 3.5, fill: color, opacity: 0 });
  s.append(cursor, dot);

  const hit = svg("rect", { class: "hit", x: 0, y: 0, width: W, height: H });
  s.append(hit);
  hit.addEventListener("pointermove", (ev) => {
    const box = s.getBoundingClientRect();
    const frac = (ev.clientX - box.left) / box.width;
    const i = Math.max(0, Math.min(points.length - 1, Math.round(frac * (points.length - 1))));
    const p = points[i];
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i));
    cursor.setAttribute("opacity", 1);
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.c));
    dot.setAttribute("opacity", 1);
    tip.show(ev.clientX, ev.clientY, label, [[p.d, fmt.usd(p.c)]]);
  });
  hit.addEventListener("pointerleave", () => {
    cursor.setAttribute("opacity", 0);
    dot.setAttribute("opacity", 0);
    tip.hide();
  });
  host.append(s);
}

/* The forecast fan: percentile bands around a median, over the three horizons.
   Horizons are spaced evenly rather than by day count - 1, 7 and 30 on a linear
   day axis would pile the first two on top of each other. */
export function forecastFan(host, spot, forecasts, color, label) {
  const W = 340, H = 170, padL = 52, padR = 16, padT = 12, padB = 26;
  host.replaceChildren();
  if (!forecasts || !forecasts.length) return;

  const cols = [{ label: "now", p: { 5: spot, 25: spot, 50: spot, 75: spot, 95: spot } }].concat(
    forecasts.map((f) => ({
      label: f.horizon_days + "d",
      p: {
        5: f.percentiles["5"], 25: f.percentiles["25"], 50: f.percentiles["50"],
        75: f.percentiles["75"], 95: f.percentiles["95"],
      },
      prob_up: f.prob_up,
    }))
  );
  const all = cols.flatMap((c) => Object.values(c.p));
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || 1;
  const yLo = lo - pad, yHi = hi + pad;
  const x = (i) => padL + (i * (W - padL - padR)) / (cols.length - 1);
  const y = (v) => padT + (1 - (v - yLo) / (yHi - yLo)) * (H - padT - padB);

  const s = svg("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, height: H,
    role: "img", "aria-label": `${label} simulated price distribution`,
  });

  for (let t = 0; t <= 3; t++) {
    const v = yLo + ((yHi - yLo) * t) / 3;
    s.append(svg("line", { class: "grid-line", x1: padL, x2: W - padR, y1: y(v), y2: y(v) }));
    const tick = svg("text", { class: "tick", x: padL - 7, y: y(v) + 3.5, "text-anchor": "end" });
    tick.textContent = fmt.usd(v, 0);
    s.append(tick);
  }

  // Trace the upper percentile left-to-right, then the lower one back, and
  // close - one filled band between the two.
  const area = (upper, lower) => {
    const out = cols.map((c, i) => `${i ? "L" : "M"}${x(i)},${y(c.p[upper])}`).join("");
    const back = cols
      .map((_, i) => cols.length - 1 - i)
      .map((i) => `L${x(i)},${y(cols[i].p[lower])}`)
      .join("");
    return out + back + "Z";
  };

  s.append(svg("path", { class: "band", fill: color, "fill-opacity": 0.16, d: area(95, 5) }));
  s.append(svg("path", { class: "band", fill: color, "fill-opacity": 0.3, d: area(75, 25) }));
  s.append(svg("path", {
    class: "series-line", stroke: color,
    d: cols.map((c, i) => `${i ? "L" : "M"}${x(i)},${y(c.p[50])}`).join(""),
  }));

  s.append(svg("line", { class: "axis-line", x1: padL, x2: W - padR, y1: H - padB, y2: H - padB }));
  cols.forEach((c, i) => {
    const t = svg("text", { class: "tick", x: x(i), y: H - padB + 15, "text-anchor": "middle" });
    t.textContent = c.label;
    s.append(t);
    // Hit target spans the whole column, well past the 24px minimum.
    const hit = svg("rect", {
      class: "hit", x: x(i) - (W - padL - padR) / (2 * (cols.length - 1)),
      y: padT, width: (W - padL - padR) / (cols.length - 1), height: H - padT - padB,
    });
    hit.addEventListener("pointerenter", (ev) => {
      const rows = i === 0
        ? [["spot", fmt.usd(c.p[50])]]
        : [["95th", fmt.usd(c.p[95])], ["75th", fmt.usd(c.p[75])], ["median", fmt.usd(c.p[50])],
           ["25th", fmt.usd(c.p[25])], ["5th", fmt.usd(c.p[5])], ["P(up)", fmt.pct(c.prob_up, 0)]];
      tip.show(ev.clientX, ev.clientY, `${label} · ${c.label}`, rows);
    });
    hit.addEventListener("pointerleave", tip.hide);
    s.append(hit);
  });

  host.append(s);
  host.append(el("div", { class: "legend" }, [
    el("span", { class: "item" }, [
      el("span", { class: "swatch", style: `background:${color};opacity:.16` }),
      el("span", { text: "5th–95th" }),
    ]),
    el("span", { class: "item" }, [
      el("span", { class: "swatch", style: `background:${color};opacity:.3` }),
      el("span", { text: "25th–75th" }),
    ]),
    el("span", { class: "item" }, [
      el("span", { class: "swatch line", style: `background:${color}` }),
      el("span", { text: "median" }),
    ]),
  ]));
}

export const assetColor = (key) =>
  getComputedStyle(document.documentElement).getPropertyValue("--" + key.toLowerCase()).trim();
