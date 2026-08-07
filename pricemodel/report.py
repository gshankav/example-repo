"""Render the model output as HTML email and a plaintext fallback.

Mail clients strip <style> blocks, external CSS and scripts, so everything here
is table-based layout with inline styles - deliberately old-fashioned HTML.
"""

from __future__ import annotations

from datetime import date

from .history import Accuracy
from .model import AssetSignals, ModelOutput

# Palette
INK = "#12181f"
MUTED = "#5b6b7c"
LINE = "#e2e8ef"
BG = "#f5f7fa"
CARD = "#ffffff"
UP = "#0f7b4f"
DOWN = "#c0392b"
WARN_BG = "#fff6e5"
WARN_LINE = "#e8a33d"

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "'SF Mono',SFMono-Regular,Menlo,Consolas,monospace"


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def fmt_price(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:,.4f}"


def fmt_pct(v: float | None, decimals: int = 2, signed: bool = True) -> str:
    """``v`` is a fraction (0.031 -> +3.10%)."""
    if v is None:
        return "n/a"
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v * 100:.{decimals}f}%"


def fmt_pct_points(v: float | None, decimals: int = 1, signed: bool = True) -> str:
    """``v`` is already in percentage points (3.1 -> +3.1%)."""
    if v is None:
        return "n/a"
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def fmt_num(v: float | None, decimals: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{decimals}f}"


def fmt_big(v: float | None) -> str:
    if v is None:
        return "n/a"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cutoff:
            return f"${v / cutoff:,.2f}{suffix}"
    return f"${v:,.2f}"


def color_for(v: float | None) -> str:
    if v is None:
        return MUTED
    return UP if v >= 0 else DOWN


def _regime_style(regime: str) -> tuple[str, str]:
    return {
        "bull": ("#e6f5ee", UP),
        "recovering": ("#eef6e6", "#4d7c26"),
        "pullback": ("#fdf6e3", "#8a6d1b"),
        "corrective": ("#fdf0e6", "#b3651b"),
        "bear": ("#fdeceb", DOWN),
    }.get(regime, ("#eef1f5", MUTED))


# --------------------------------------------------------------------------
# HTML building blocks
# --------------------------------------------------------------------------


def _th(text: str, align: str = "left") -> str:
    return (
        f'<th style="text-align:{align};padding:8px 10px;font:600 11px {FONT};'
        f'letter-spacing:.06em;text-transform:uppercase;color:{MUTED};'
        f'border-bottom:1px solid {LINE};">{text}</th>'
    )


def _td(text: str, align: str = "left", color: str = INK, bold: bool = False) -> str:
    weight = "600" if bold else "400"
    return (
        f'<td style="text-align:{align};padding:9px 10px;font:{weight} 13px {FONT};'
        f'color:{color};border-bottom:1px solid {LINE};white-space:nowrap;">{text}</td>'
    )


def _section(title: str, body: str, subtitle: str = "") -> str:
    sub = (
        f'<div style="font:400 12px {FONT};color:{MUTED};margin:2px 0 12px;">{subtitle}</div>'
        if subtitle
        else '<div style="height:10px;"></div>'
    )
    return f"""
    <tr><td style="padding:22px 20px 0;">
      <div style="font:600 15px {FONT};color:{INK};">{title}</div>
      {sub}
      <div style="background:{CARD};border:1px solid {LINE};border-radius:10px;overflow:hidden;">
        {body}
      </div>
    </td></tr>"""


def _table(rows: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="border-collapse:collapse;background:{CARD};">{rows}</table>'
    )


def _headline_card(sig: AssetSignals) -> str:
    bg, fg = _regime_style(sig.regime)
    change = sig.change_1d
    return f"""
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
           style="border-collapse:collapse;">
      <tr>
        <td style="padding:14px 16px;border-bottom:1px solid {LINE};">
          <div style="font:600 12px {FONT};color:{MUTED};letter-spacing:.05em;">
            {sig.key} &middot; {sig.name}
          </div>
          <div style="font:700 26px {FONT};color:{INK};padding:4px 0 2px;">
            {fmt_price(sig.price)}
            <span style="font:600 14px {FONT};color:{color_for(change)};padding-left:8px;">
              {fmt_pct(change)}
            </span>
          </div>
          <div style="font:400 12px {FONT};color:{MUTED};">
            <span style="background:{bg};color:{fg};border-radius:4px;padding:2px 7px;
                         font:600 11px {FONT};text-transform:uppercase;letter-spacing:.05em;">
              {sig.regime}
            </span>
            <span style="padding-left:8px;">RSI {fmt_num(sig.rsi, 1)} ({sig.rsi_label})</span>
            <span style="padding-left:8px;">vol {fmt_pct(sig.ewma_vol, 1, signed=False)} ann.</span>
          </div>
        </td>
      </tr>
    </table>"""


def _performance_table(signals: dict[str, AssetSignals]) -> str:
    head = (
        "<tr>"
        + _th("Asset")
        + _th("Price", "right")
        + _th("1D", "right")
        + _th("7D", "right")
        + _th("30D", "right")
        + _th("90D", "right")
        + _th("1Y", "right")
        + "</tr>"
    )
    rows = [head]
    for s in signals.values():
        rows.append(
            "<tr>"
            + _td(s.key, bold=True)
            + _td(fmt_price(s.price), "right")
            + _td(fmt_pct(s.change_1d), "right", color_for(s.change_1d))
            + _td(fmt_pct(s.change_7d), "right", color_for(s.change_7d))
            + _td(fmt_pct(s.change_30d), "right", color_for(s.change_30d))
            + _td(fmt_pct(s.change_90d), "right", color_for(s.change_90d))
            + _td(fmt_pct(s.change_365d), "right", color_for(s.change_365d))
            + "</tr>"
        )
    return _table("".join(rows))


def _technicals_table(signals: dict[str, AssetSignals]) -> str:
    head = (
        "<tr>"
        + _th("Asset")
        + _th("vs 20D", "right")
        + _th("vs 50D", "right")
        + _th("vs 200D", "right")
        + _th("30D vol", "right")
        + _th("90D vol", "right")
        + _th("Z(200)", "right")
        + _th("Drawdown", "right")
        + "</tr>"
    )
    rows = [head]
    for s in signals.values():
        rows.append(
            "<tr>"
            + _td(s.key, bold=True)
            + _td(fmt_pct(s.price_vs_sma.get(20)), "right", color_for(s.price_vs_sma.get(20)))
            + _td(fmt_pct(s.price_vs_sma.get(50)), "right", color_for(s.price_vs_sma.get(50)))
            + _td(fmt_pct(s.price_vs_sma.get(200)), "right", color_for(s.price_vs_sma.get(200)))
            + _td(fmt_pct(s.vol.get(30), 1, signed=False), "right")
            + _td(fmt_pct(s.vol.get(90), 1, signed=False), "right")
            + _td(fmt_num(s.zscore_200, 2), "right")
            + _td(fmt_pct(s.drawdown), "right", color_for(s.drawdown))
            + "</tr>"
        )
    return _table("".join(rows))


def _forecast_table(signals: dict[str, AssetSignals]) -> str:
    head = (
        "<tr>"
        + _th("Asset")
        + _th("Horizon", "right")
        + _th("5%", "right")
        + _th("25%", "right")
        + _th("Median", "right")
        + _th("75%", "right")
        + _th("95%", "right")
        + _th("P(up)", "right")
        + "</tr>"
    )
    rows = [head]
    for s in signals.values():
        for i, f in enumerate(s.forecasts):
            rows.append(
                "<tr>"
                + _td(s.key if i == 0 else "", bold=True)
                + _td(f"{f.horizon_days}D", "right", MUTED)
                + _td(fmt_price(f.percentiles.get(5)), "right", MUTED)
                + _td(fmt_price(f.percentiles.get(25)), "right")
                + _td(fmt_price(f.percentiles.get(50)), "right", INK, bold=True)
                + _td(fmt_price(f.percentiles.get(75)), "right")
                + _td(fmt_price(f.percentiles.get(95)), "right", MUTED)
                + _td(f"{f.prob_up * 100:.0f}%", "right")
                + "</tr>"
            )
    return _table("".join(rows))


def _mnav_block(output: ModelOutput) -> str:
    m = output.mnav
    if m is None:
        return ""

    rank = m.percentile_rank
    rank_text = (
        f"{rank:.0f}th percentile of its trailing {len(m.history)}-day range"
        if rank is not None
        else "insufficient history for a percentile"
    )
    verdict = "premium to" if m.mnav >= 1 else "discount to"

    banner = ""
    if not m.verified:
        banner = f"""
        <tr><td colspan="2" style="padding:10px 14px;background:{WARN_BG};
             border-bottom:1px solid {LINE};font:600 12px {FONT};color:#8a5a12;">
          Treasury inputs are unverified placeholders - update config/holdings.json
          before acting on these numbers.
        </td></tr>"""
    elif m.stale:
        banner = f"""
        <tr><td colspan="2" style="padding:10px 14px;background:{WARN_BG};
             border-bottom:1px solid {LINE};font:600 12px {FONT};color:#8a5a12;">
          Treasury data is {m.days_old} days old (as of {m.as_of}).
        </td></tr>"""

    def row(label: str, value: str, bold: bool = False) -> str:
        return (
            "<tr>"
            + _td(label, color=MUTED)
            + _td(value, "right", INK, bold)
            + "</tr>"
        )

    body = banner + "".join(
        [
            f"""<tr><td colspan="2" style="padding:14px 14px 6px;">
              <div style="font:700 30px {FONT};color:{color_for(m.premium_pct)};">
                {m.mnav:.2f}x
              </div>
              <div style="font:400 12px {FONT};color:{MUTED};padding-top:2px;">
                MSTR trades at a {fmt_pct_points(abs(m.premium_pct), 1, signed=False)}
                {verdict} the market value of its bitcoin &middot; {rank_text}
              </div>
            </td></tr>""",
            row("BTC held", f"{m.btc_holdings:,.0f} BTC"),
            row("BTC NAV", fmt_big(m.btc_nav)),
            row("Diluted shares", f"{m.diluted_shares:,.0f}"),
            row("BTC NAV / share", fmt_price(m.nav_per_share)),
            row("MSTR price", fmt_price(m.mstr_price), bold=True),
            row("Implied market cap", fmt_big(m.market_cap)),
            row("Treasury data as of", f"{m.as_of} ({m.source})"),
        ]
    )
    return _table(body)


def _cross_table(output: ModelOutput) -> str:
    cross = output.cross
    if not cross.correlations:
        return ""
    head = (
        "<tr>"
        + _th("Pair")
        + _th("30D corr", "right")
        + _th("90D corr", "right")
        + _th("Beta vs BTC", "right")
        + "</tr>"
    )
    rows = [head]
    for pair, windows in cross.correlations.items():
        rows.append(
            "<tr>"
            + _td(pair, bold=True)
            + _td(fmt_num(windows.get(30), 2), "right")
            + _td(fmt_num(windows.get(90), 2), "right")
            + _td(fmt_num(cross.betas.get(pair), 2), "right")
            + "</tr>"
        )
    return _table("".join(rows))


def _accuracy_table(accuracy: list[Accuracy]) -> str:
    if not accuracy:
        return ""
    head = (
        "<tr>"
        + _th("Asset")
        + _th("Horizon", "right")
        + _th("Scored", "right")
        + _th("Median abs err", "right")
        + _th("In 5-95 band", "right")
        + _th("Direction", "right")
        + "</tr>"
    )
    rows = [head]
    for a in accuracy:
        rows.append(
            "<tr>"
            + _td(a.asset, bold=True)
            + _td(f"{a.horizon_days}D", "right", MUTED)
            + _td(str(a.n), "right", MUTED)
            + _td(fmt_pct_points(a.median_abs_error_pct, 2, signed=False), "right")
            + _td(fmt_pct_points(a.band_hit_rate, 0, signed=False), "right")
            + _td(fmt_pct_points(a.direction_hit_rate, 0, signed=False), "right")
            + "</tr>"
        )
    return _table("".join(rows))


def _warnings_block(warnings: list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(
        f'<li style="margin:3px 0;">{w}</li>' for w in warnings
    )
    return f"""
    <tr><td style="padding:22px 20px 0;">
      <div style="background:{WARN_BG};border:1px solid {WARN_LINE};border-radius:10px;
                  padding:12px 16px;font:400 12px {FONT};color:#7a4d0c;">
        <div style="font-weight:600;padding-bottom:4px;">Data notes</div>
        <ul style="margin:0;padding-left:18px;">{items}</ul>
      </div>
    </td></tr>"""


def render_html(output: ModelOutput, accuracy: list[Accuracy] | None = None) -> str:
    accuracy = accuracy or []
    signals = output.signals

    cards = "".join(
        f'<tr><td style="padding:0 20px;">'
        f'<div style="background:{CARD};border:1px solid {LINE};border-radius:10px;'
        f'margin-top:10px;overflow:hidden;">{_headline_card(s)}</div></td></tr>'
        for s in signals.values()
    )

    sources = ", ".join(f"{k} via {s.source}" for k, s in signals.items())

    body = [
        f"""<tr><td style="padding:24px 20px 4px;">
          <div style="font:700 20px {FONT};color:{INK};">Daily price model</div>
          <div style="font:400 13px {FONT};color:{MUTED};padding-top:3px;">
            BTC &middot; ETH &middot; MSTR &nbsp;|&nbsp; {output.run_date:%A, %d %B %Y}
          </div>
        </td></tr>""",
        cards,
        _warnings_block(output.warnings),
        _section("Performance", _performance_table(signals)),
        _section(
            "Technical position",
            _technicals_table(signals),
            "Volatility is annualized. Z(200) is standard deviations from the 200-day mean; "
            "drawdown is measured from the trailing 1-year high.",
        ),
        _section(
            "Forward distribution",
            _forecast_table(signals),
            "Monte Carlo simulation with fat-tailed (Student-t) innovations and heavily "
            "shrunk drift. These are distributional ranges, not point predictions.",
        ),
    ]

    if output.mnav:
        body.append(
            _section(
                "MSTR mNAV",
                _mnav_block(output),
                "Market value of the equity relative to the market value of its bitcoin. "
                "Historical percentile holds today's BTC and share counts constant.",
            )
        )

    cross = _cross_table(output)
    if cross:
        body.append(
            _section(
                "Cross-asset",
                cross,
                f"Correlations and betas on date-aligned daily log returns.",
            )
        )

    acc = _accuracy_table(accuracy)
    if acc:
        body.append(
            _section(
                "Forecast scorecard",
                acc,
                "How previous forecasts from this model actually turned out.",
            )
        )

    body.append(
        f"""<tr><td style="padding:22px 20px 28px;">
          <div style="border-top:1px solid {LINE};padding-top:12px;
                      font:400 11px {FONT};color:{MUTED};line-height:1.6;">
            Sources: {sources}.<br>
            Generated automatically by the pricemodel job in
            <span style="font-family:{MONO};">gshankav/example-repo</span>.
            For information only - not investment advice.
          </div>
        </td></tr>"""
    )

    return f"""<div style="margin:0;padding:0;background:{BG};">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="background:{BG};border-collapse:collapse;padding:0;margin:0;">
    <tr><td align="center" style="padding:16px 8px;">
      <table role="presentation" cellpadding="0" cellspacing="0" width="640"
             style="max-width:640px;width:100%;border-collapse:collapse;background:{BG};">
        {''.join(body)}
      </table>
    </td></tr>
  </table>
</div>"""


def render_text(output: ModelOutput, accuracy: list[Accuracy] | None = None) -> str:
    """Plaintext alternative part, and what gets logged in CI."""
    accuracy = accuracy or []
    lines: list[str] = []
    add = lines.append

    add(f"DAILY PRICE MODEL - {output.run_date:%A, %d %B %Y}")
    add("=" * 62)

    if output.warnings:
        add("")
        add("DATA NOTES")
        for w in output.warnings:
            add(f"  ! {w}")

    add("")
    add("PERFORMANCE")
    add(f"  {'Asset':<6}{'Price':>13}{'1D':>9}{'7D':>9}{'30D':>9}{'1Y':>10}")
    for s in output.signals.values():
        add(
            f"  {s.key:<6}{fmt_price(s.price):>13}{fmt_pct(s.change_1d):>9}"
            f"{fmt_pct(s.change_7d):>9}{fmt_pct(s.change_30d):>9}"
            f"{fmt_pct(s.change_365d):>10}"
        )

    add("")
    add("TECHNICAL POSITION")
    for s in output.signals.values():
        add(
            f"  {s.key:<6} regime={s.regime:<12} RSI={fmt_num(s.rsi, 1):>5} "
            f"({s.rsi_label})  vol={fmt_pct(s.ewma_vol, 1, signed=False)}  "
            f"vs200D={fmt_pct(s.price_vs_sma.get(200))}  dd={fmt_pct(s.drawdown)}"
        )

    add("")
    add("FORWARD DISTRIBUTION (Monte Carlo, fat-tailed)")
    for s in output.signals.values():
        add(f"  {s.key}")
        for f in s.forecasts:
            add(
                f"    {f.horizon_days:>3}D  "
                f"5%={fmt_price(f.percentiles.get(5))}  "
                f"med={fmt_price(f.percentiles.get(50))}  "
                f"95%={fmt_price(f.percentiles.get(95))}  "
                f"P(up)={f.prob_up * 100:.0f}%"
            )

    if output.mnav:
        m = output.mnav
        add("")
        add("MSTR mNAV")
        add(f"  mNAV                {m.mnav:.3f}x  ({fmt_pct_points(m.premium_pct)})")
        add(f"  BTC held            {m.btc_holdings:,.0f} BTC")
        add(f"  BTC NAV             {fmt_big(m.btc_nav)}")
        add(f"  BTC NAV / share     {fmt_price(m.nav_per_share)}")
        add(f"  MSTR price          {fmt_price(m.mstr_price)}")
        add(f"  Treasury as of      {m.as_of} ({m.source})")
        if not m.verified:
            add("  ! treasury inputs are UNVERIFIED placeholders")

    if output.cross.correlations:
        add("")
        add("CROSS-ASSET")
        for pair, windows in output.cross.correlations.items():
            add(
                f"  {pair:<10} corr30={fmt_num(windows.get(30), 2):>6}  "
                f"corr90={fmt_num(windows.get(90), 2):>6}  "
                f"beta={fmt_num(output.cross.betas.get(pair), 2):>6}"
            )

    if accuracy:
        add("")
        add("FORECAST SCORECARD")
        for a in accuracy:
            add(
                f"  {a.asset:<6}{a.horizon_days:>3}D  n={a.n:<4} "
                f"medAbsErr={a.median_abs_error_pct:>6.2f}%  "
                f"band={a.band_hit_rate:>5.0f}%  dir={a.direction_hit_rate:>5.0f}%"
            )

    add("")
    add("Sources: " + ", ".join(f"{k} via {s.source}" for k, s in output.signals.items()))
    add("For information only - not investment advice.")
    return "\n".join(lines)


def subject_line(output: ModelOutput, today: date | None = None) -> str:
    parts = []
    for key in ("BTC", "ETH", "MSTR"):
        s = output.signals.get(key)
        if s:
            parts.append(f"{key} {fmt_price(s.price)} ({fmt_pct(s.change_1d, 1)})")
    day = (today or output.run_date).strftime("%d %b")
    return f"Price model {day}: " + "  ".join(parts)
