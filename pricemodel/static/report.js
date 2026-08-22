/* The daily report as a dashboard. One fetch of /api/report, then render;
   every number here comes from the same run_model() the emailed report uses. */

import {
  fmt, el, deltaClass, initTheme, getJSON, renderWarnings,
  sparkline, forecastFan, assetColor,
} from "/static/common.js";

const $ = (id) => document.getElementById(id);
const ORDER = ["BTC", "ETH", "MSTR"];
let latest = null;

initTheme();
$("refresh").addEventListener("click", () => load(true));
// Colours are read from CSS custom properties, so a theme flip needs a repaint
// of the SVGs - the DOM text is already theme-driven.
document.addEventListener("themechange", () => latest && render(latest));

async function load(force = false) {
  document.querySelectorAll(".card, .grid").forEach((n) => n.classList.add("stale"));
  try {
    const data = await getJSON("/api/report" + (force ? "?refresh=1" : ""));
    latest = data;
    $("loading").hidden = true;
    render(data);
  } catch (err) {
    $("loading").hidden = true;
    renderWarnings($("warnings"), [
      err.payload?.hint ? `${err.message} — ${err.payload.hint}` : err.message,
    ]);
  } finally {
    document.querySelectorAll(".card, .grid").forEach((n) => n.classList.remove("stale"));
  }
}

function render(d) {
  const keys = ORDER.filter((k) => d.assets[k]);
  $("meta-line").textContent =
    `${d.run_date}${d.offline ? " · OFFLINE synthetic data" : ""}`;
  renderWarnings($("warnings"), d.offline
    ? ["OFFLINE: synthetic prices. Every number on this page is meaningless as market data."].concat(d.warnings)
    : d.warnings);

  /* --- asset cards: price, deltas, sparkline --- */
  $("assets").replaceChildren(...keys.map((k) => {
    const a = d.assets[k], color = assetColor(k);
    const spark = el("div", {});
    const card = el("div", { class: "card" }, [
      el("h2", { text: `${a.name} · ${a.key}` }),
      el("div", { class: "tile", style: "border:none;padding:0;background:none" }, [
        el("div", { class: "value", text: fmt.usd(a.price) }),
        el("div", { class: "foot" }, [
          el("span", { class: deltaClass(a.changes["1d"]), text: fmt.signedPct(a.changes["1d"]) }),
          el("span", { text: ` today · ${a.source} · ${a.as_of}` }),
        ]),
      ]),
      spark,
      el("div", { class: "spark-cap", text: "90-day close" }),
      table(
        ["", "7D", "30D", "90D", "1Y"],
        [["Return",
          ...["7d", "30d", "90d", "365d"].map((w) => ({
            text: fmt.signedPct(a.changes[w]), cls: deltaClass(a.changes[w]),
          }))]]
      ),
      el("div", { class: "legend" }, [
        el("span", { class: "pill", text: a.regime }),
        el("span", { class: "pill", text: `RSI ${a.rsi === null ? "n/a" : a.rsi.toFixed(0)} · ${a.rsi_label}` }),
        el("span", { class: "pill", text: `vol ${fmt.pct(a.ewma_vol, 0)}` }),
      ]),
    ]);
    // Sparkline needs layout before it can size itself to the card.
    queueMicrotask(() => sparkline(spark, a.history, color, `${a.key} · 90 days`));
    return card;
  }));

  /* --- forecast fans --- */
  $("fans").replaceChildren(...keys.map((k) => {
    const a = d.assets[k];
    const host = el("div", {});
    const wrap = el("div", {}, [
      el("h2", { style: "font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 8px", text: a.key }),
      host,
      table(
        ["Horizon", "5th", "Median", "95th", "P(up)"],
        a.forecasts.map((f) => [
          f.horizon_days + "d",
          { text: fmt.usd(f.percentiles["5"], 0) },
          { text: fmt.usd(f.percentiles["50"], 0) },
          { text: fmt.usd(f.percentiles["95"], 0) },
          { text: fmt.pct(f.prob_up, 0) },
        ])
      ),
    ]);
    queueMicrotask(() => forecastFan(host, a.price, a.forecasts, assetColor(k), a.key));
    return wrap;
  }));

  /* --- technical position --- */
  $("tech").replaceChildren(...tableParts(
    ["", "vs 20D", "vs 50D", "vs 200D", "30D vol", "90D vol", "z(200)", "drawdown"],
    keys.map((k) => {
      const a = d.assets[k];
      return [
        a.key,
        ...["20", "50", "200"].map((w) => ({
          text: fmt.signedPct(a.price_vs_sma[w]), cls: deltaClass(a.price_vs_sma[w]),
        })),
        { text: fmt.pct(a.vol["30"], 0) },
        { text: fmt.pct(a.vol["90"], 0) },
        { text: a.zscore_200 === null ? "n/a" : a.zscore_200.toFixed(2) },
        { text: fmt.signedPct(a.drawdown), cls: deltaClass(a.drawdown) },
      ];
    })
  ));

  /* --- cross-asset --- */
  const pairs = Object.keys(d.cross.correlations);
  $("cross").replaceChildren(...tableParts(
    ["Pair", "30D corr", "90D corr", "BTC beta"],
    pairs.map((p) => [
      p,
      { text: num(d.cross.correlations[p]["30"]) },
      { text: num(d.cross.correlations[p]["90"]) },
      { text: d.cross.betas[p] === null || d.cross.betas[p] === undefined ? "—" : d.cross.betas[p].toFixed(2) },
    ])
  ));

  /* --- mNAV --- */
  const m = d.mnav;
  $("mnav").replaceChildren(m
    ? el("div", {}, [
        el("div", { class: "tile", style: "border:none;padding:0;background:none" }, [
          el("div", { class: "value", text: fmt.x(m.mnav) }),
          el("div", { class: "foot", text:
            `${fmt.signedPct(m.premium_pct / 100)} ${m.premium_pct >= 0 ? "premium" : "discount"} to bitcoin backing` }),
        ]),
        kvList([
          ["BTC NAV", fmt.big(m.btc_nav)],
          ["Market cap", fmt.big(m.market_cap)],
          ["BTC NAV / share", fmt.usd(m.nav_per_share)],
          ["BTC held", fmt.num(m.btc_holdings) + " BTC"],
          ["Percentile vs own range", m.percentile_rank === null ? "n/a" : Math.round(m.percentile_rank) + "th"],
          ["Treasury as of", `${m.as_of}${m.verified ? "" : " (UNVERIFIED)"}`],
        ]),
      ])
    : el("p", { class: "note", text: "mNAV unavailable — treasury inputs missing." }));

  /* --- scorecard --- */
  if (d.accuracy.length) {
    $("score").replaceChildren(...tableParts(
      ["Asset", "Horizon", "n", "Median abs error", "5–95 hit rate", "Direction"],
      d.accuracy.map((a) => [
        a.asset,
        { text: a.horizon_days + "d" },
        { text: String(a.n) },
        { text: a.median_abs_error_pct.toFixed(2) + "%" },
        { text: fmt.pct(a.band_hit_rate, 0) },
        { text: fmt.pct(a.direction_hit_rate, 0) },
      ])
    ));
    $("score-note").textContent =
      "Scored once a horizon has elapsed. Needs weeks of runs before the sample means much.";
  } else {
    $("score").replaceChildren();
    $("score-note").textContent =
      "No elapsed forecasts yet — the scorecard fills in as history/forecasts.jsonl accumulates.";
  }
}

const num = (v) => (v === null || v === undefined ? "—" : v.toFixed(2));

function tableParts(headers, rows) {
  const head = el("tr", {}, headers.map((h, i) =>
    el("th", { class: i ? "num" : "", text: h })));
  const body = el("tbody", {}, rows.map((r) =>
    el("tr", {}, r.map((c, i) => {
      const cell = typeof c === "string" ? { text: c } : c;
      return el(i === 0 ? "th" : "td", {
        class: (i === 0 ? "name" : "num ") + (cell.cls || ""),
        text: cell.text,
      });
    }))));
  return [el("thead", {}, [head]), body];
}

function table(headers, rows) {
  return el("table", {}, tableParts(headers, rows));
}

function kvList(rows) {
  return el("dl", { class: "kv", style: "margin-top:14px" },
    rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
}

load();
