/* The valuation page. Inputs go to /api/value, Python does the arithmetic, the
   result renders here - so the browser never holds a second copy of the model
   that could drift from the CLI's. */

import { fmt, el, deltaClass, initTheme, getJSON, renderWarnings, tip } from "/static/common.js";

const INPUTS = ["btc_price", "mstr_price", "btc_holdings", "shares"];
const SLIDERS = { btc_price: 1, mstr_price: 1 };
const $ = (id) => document.getElementById(id);

let spot = null;      // today's values, for the reset button and slider ranges
let inFlight = 0;

initTheme();

/* Debounced so dragging a slider issues one request per pause rather than one
   per pixel; the server-side cache makes each of those cheap anyway. */
let timer = null;
function schedule(delay = 120) {
  clearTimeout(timer);
  timer = setTimeout(load, delay);
}

function overrides() {
  const out = {};
  for (const k of INPUTS) {
    const v = parseFloat($(k).value);
    if (isFinite(v) && v > 0 && spot && Math.abs(v - spotFor(k)) > 1e-9) out[k] = v;
  }
  return out;
}

const spotFor = (k) => (k === "shares" ? spot.shares : spot[k]);

async function load(force = false) {
  const qs = new URLSearchParams(overrides());
  if (force) qs.set("refresh", "1");
  const seq = ++inFlight;
  document.querySelectorAll(".tiles, .card").forEach((n) => n.classList.add("stale"));
  try {
    const data = await getJSON("/api/value?" + qs.toString());
    if (seq !== inFlight) return;   // a later request already won
    render(data);
  } catch (err) {
    if (seq !== inFlight) return;
    renderWarnings($("warnings"), [
      err.payload?.hint ? `${err.message} — ${err.payload.hint}` : err.message,
    ]);
  } finally {
    if (seq === inFlight) {
      document.querySelectorAll(".tiles, .card").forEach((n) => n.classList.remove("stale"));
    }
  }
}

function render(data) {
  const v = data.valuation, m = data.meta;
  const firstRun = spot === null;
  spot = data.spot;

  if (firstRun) initInputs();
  for (const k of INPUTS) {
    $("f-" + k).classList.toggle("changed", data.overrides.includes(k));
  }
  $("reset").hidden = data.overrides.length === 0;

  $("meta-line").textContent =
    `${m.run_date} · BTC ${m.btc_source} · MSTR ${m.mstr_source} · treasury as of ${m.holdings_as_of}`;
  renderWarnings($("warnings"), data.warnings);

  const premium = v.gross_mnav_diluted - 1;
  $("tiles").replaceChildren(
    tile("Gross mNAV", fmt.x(v.gross_mnav_diluted),
         `${fmt.signedPct(premium)} ${premium >= 0 ? "premium" : "discount"} to bitcoin backing`,
         deltaClass(null)),
    tile("Net mNAV", v.net_mnav === null ? "n/a" : fmt.x(v.net_mnav),
         "after debt and preferred claims"),
    tile("BTC per share", v.btc_per_share.toFixed(6),
         `${fmt.num(v.sats_per_share)} sats`, "", true),
    tile("Implied BTC price", fmt.usd(v.btc_price_at_par, 0),
         `vs ${fmt.usd(v.btc_price, 0)} spot`, "", true),
  );

  kv($("kv-gross"), [
    ["BTC NAV", fmt.big(v.btc_nav)],
    ["Market cap (diluted)", fmt.big(v.market_cap_diluted)],
    ["Gross NAV / share", fmt.usd(v.gross_nav_per_share_diluted)],
    ["Gross mNAV (diluted)", fmt.x(v.gross_mnav_diluted, 3)],
    ["Gross mNAV (basic)", fmt.x(v.gross_mnav_basic, 3)],
    ["EV / BTC NAV", fmt.x(v.ev_to_btc_nav, 3)],
    ["Percentile vs own range",
     m.mnav_percentile === null ? "n/a" : Math.round(m.mnav_percentile) + "th"],
  ]);

  kv($("kv-net"), [
    ["BTC NAV", fmt.big(v.btc_nav)],
    ["+ cash & other assets", fmt.big(v.non_btc_assets)],
    ["− debt-like converts", fmt.big(v.debt_claim)],
    ["− preferred preference", fmt.big(v.preferred_claim)],
    ["= Net NAV to common", fmt.big(v.net_nav)],
    ["Net NAV / share", fmt.usd(v.net_nav_per_share)],
    ["Preferred dividends", v.preferred_dividends ? fmt.big(v.preferred_dividends) + " / yr" : "none"],
  ]);

  const notes = [
    ...v.equity_like_notes.map((n) => `${n} — equity (in the money)`),
    ...v.debt_like_notes.map((n) => `${n} — debt (out of the money)`),
  ];
  $("note-notes").textContent = notes.length
    ? notes.join(" · ")
    : "No convertible notes configured.";

  kv($("kv-share"), [
    ["BTC per share", v.btc_per_share.toFixed(6) + " BTC"],
    ["Sats per share", fmt.num(v.sats_per_share)],
    ["Shares (basic)", fmt.num(v.basic_shares)],
    ["+ in-the-money converts", fmt.num(v.convert_shares)],
    ["Shares (diluted)", fmt.num(v.diluted_shares)],
    ["BTC price at par", fmt.usd(v.btc_price_at_par, 0)],
    ["BTC price at zero net NAV", fmt.usd(v.btc_price_at_zero_net_nav, 0)],
  ]);

  kv($("kv-lev"), [
    ["Structural (gross / net)", v.structural_leverage === null ? "n/a" : fmt.x(v.structural_leverage)],
    ["Realized 90d beta", m.realized_beta_90d === null ? "n/a" : fmt.x(m.realized_beta_90d)],
    ["Gap (premium-driven)",
     v.structural_leverage !== null && m.realized_beta_90d !== null
       ? fmt.x(m.realized_beta_90d - v.structural_leverage) : "n/a"],
  ]);

  heatmap(data);
}

function tile(label, value, foot, cls = "", small = false) {
  return el("div", { class: "tile" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value" + (small ? " sm" : "") + (cls ? " " + cls : ""), text: value }),
    el("div", { class: "foot", text: foot }),
  ]);
}

function kv(host, rows) {
  host.replaceChildren();
  for (const [k, v] of rows) {
    host.append(el("dt", { text: k }), el("dd", { text: v }));
  }
}

/* Seven bins, the most a reader can hold apart at a glance. Blue = gain,
   red = loss, neutral gray at no change - and the price is printed in every
   cell, so the colour only speeds the read, it never carries it alone. */
function bin(r) {
  if (r >= 0.5) return "p3";
  if (r >= 0.2) return "p2";
  if (r >= 0.05) return "p1";
  if (r > -0.05) return "z";
  if (r > -0.2) return "n1";
  if (r > -0.5) return "n2";
  return "n3";
}

function heatmap(data) {
  const v = data.valuation;
  const t = $("heat");
  const head = el("tr", {}, [el("th", { text: "BTC price" })]);
  for (const m of data.multiples) head.append(el("th", { class: "num", text: fmt.x(+m, 1) }));
  const body = el("tbody");

  for (const row of data.sensitivity) {
    const tr = el("tr", { class: Math.abs(row.btc_move) < 1e-9 ? "spot" : "" }, [
      el("th", { class: "num", text: fmt.usd(row.btc_price, 0) }),
    ]);
    for (const m of data.multiples) {
      const price = row.implied[m], ret = row.returns[m];
      const td = el("td", { class: "cell num " + bin(ret) }, [
        el("span", { text: fmt.usd(price, 0) }),
        el("span", { class: "ret", text: fmt.signedPct(ret, 0) }),
      ]);
      td.addEventListener("pointerenter", (ev) =>
        tip.show(ev.clientX, ev.clientY, `BTC ${fmt.usd(row.btc_price, 0)} · ${fmt.x(+m, 1)}`, [
          ["Implied MSTR", fmt.usd(price, 0)],
          ["vs today", fmt.signedPct(ret)],
          ["BTC move", fmt.signedPct(row.btc_move, 0)],
          ["Gross NAV/share", fmt.usd(row.gross_nav_per_share)],
        ]));
      td.addEventListener("pointerleave", tip.hide);
      tr.append(td);
    }
    body.append(tr);
  }
  t.replaceChildren(el("thead", {}, [head]), body);

  $("scale").replaceChildren(
    el("span", { class: "cap", text: "−50%" }),
    ...["n3", "n2", "n1", "z", "p1", "p2", "p3"].map((c) =>
      el("span", { class: "step", style: `background:var(--div-${c.replace("n", "neg-").replace("p", "pos-").replace("z", "mid")})` })),
    el("span", { class: "cap", text: "+50% vs today's MSTR price" }),
  );
}

function initInputs() {
  for (const k of INPUTS) {
    const box = $(k);
    box.value = round(spotFor(k), k);
    $("h-" + k).textContent = "spot " + hint(k, spotFor(k));
    box.addEventListener("input", () => { syncSlider(k); schedule(); });

    if (SLIDERS[k]) {
      const r = $(k + "_r");
      r.addEventListener("input", () => {
        // Slider spans 0.25x to 3x spot - wide enough for a real scenario,
        // narrow enough that a pixel is still a meaningful step.
        box.value = round(spotFor(k) * (0.25 + (r.value / 100) * 2.75), k);
        schedule(60);
      });
      syncSlider(k);
    }
  }
  $("reset").addEventListener("click", () => {
    for (const k of INPUTS) { $(k).value = round(spotFor(k), k); syncSlider(k); }
    schedule(0);
  });
  $("refresh").addEventListener("click", () => load(true));
}

function syncSlider(k) {
  if (!SLIDERS[k]) return;
  const v = parseFloat($(k).value);
  if (!isFinite(v) || v <= 0) return;
  const frac = (v / spotFor(k) - 0.25) / 2.75;
  $(k + "_r").value = Math.max(0, Math.min(100, frac * 100));
}

const round = (v, k) => (k === "mstr_price" ? +v.toFixed(2) : Math.round(v));
const hint = (k, v) =>
  k === "btc_holdings" ? fmt.num(v) + " BTC"
  : k === "shares" ? fmt.num(v)
  : fmt.usd(v, 0);

load();
