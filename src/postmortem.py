"""Signal postmortem: where the bot's edge actually comes from.

Win rate alone hides everything that matters. This breaks closed signals
down by symbol, direction, confluence strength, trading session and
confirming factor, in R (risk units), so weak pockets are visible, and
derives a Kelly-based risk suggestion from the realised results.

Pure functions over simple records; the repository converts DB rows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

MIN_SAMPLE = 30          # below this, numbers are noise: no sizing advice
MIN_BUCKET = 5           # buckets smaller than this are not ranked
KELLY_FRACTION = 0.25    # quarter Kelly: full Kelly is far too violent
KELLY_CAP_PCT = 2.0      # never suggest risking more than this per trade


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    outcome: str                 # TP / SL
    r: float
    fired_at: datetime
    strength: int | None = None
    votes: dict = field(default_factory=dict)


@dataclass
class Bucket:
    name: str
    trades: int = 0
    wins: int = 0
    r_total: float = 0.0

    @property
    def win_rate(self) -> float:
        return 100 * self.wins / self.trades if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        return self.r_total / self.trades if self.trades else 0.0

    def add(self, record: TradeRecord) -> None:
        self.trades += 1
        self.wins += 1 if record.r > 0 else 0
        self.r_total += record.r


def session_of(fired_at: datetime) -> str:
    """Trading session by UTC hour."""
    when = fired_at if fired_at.tzinfo else fired_at.replace(tzinfo=timezone.utc)
    hour = when.astimezone(timezone.utc).hour
    if 7 <= hour < 12:
        return "London"
    if 12 <= hour < 16:
        return "London/NY overlap"
    if 16 <= hour < 21:
        return "New York"
    return "Asia"


def kelly_fraction(win_rate: float, avg_win_r: float, avg_loss_r: float = 1.0) -> float:
    """Full-Kelly fraction of the account to risk. win_rate is 0..1."""
    if avg_win_r <= 0 or avg_loss_r <= 0:
        return 0.0
    payoff = avg_win_r / avg_loss_r
    return win_rate - (1 - win_rate) / payoff


def max_losing_streak(records: list[TradeRecord]) -> int:
    longest = current = 0
    for record in sorted(records, key=lambda r: r.fired_at):
        current = current + 1 if record.r <= 0 else 0
        longest = max(longest, current)
    return longest


def _group(records: list[TradeRecord], key) -> list[Bucket]:
    buckets: dict[str, Bucket] = {}
    for record in records:
        for name in key(record):
            buckets.setdefault(name, Bucket(name)).add(record)
    return sorted(buckets.values(), key=lambda b: b.expectancy, reverse=True)


def build_postmortem(records: list[TradeRecord]) -> dict:
    """Aggregate closed signals into the numbers the report shows."""
    closed = [r for r in records if r.outcome in ("TP", "SL")]
    overall = Bucket("All")
    for record in closed:
        overall.add(record)

    wins = [r.r for r in closed if r.r > 0]
    losses = [-r.r for r in closed if r.r <= 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None

    kelly_pct = None
    suggested_risk_pct = None
    if len(closed) >= MIN_SAMPLE and wins and losses:
        full = kelly_fraction(
            len(wins) / len(closed),
            gross_win / len(wins),
            gross_loss / len(losses),
        )
        kelly_pct = round(full * 100, 1)
        suggested_risk_pct = round(min(max(full * KELLY_FRACTION * 100, 0.0), KELLY_CAP_PCT), 2)

    confirm_factors = ("Momentum", "RSI zone")

    def factor_key(record: TradeRecord) -> list[str]:
        sign = 1 if record.direction == "BUY" else -1
        votes = record.votes or {}
        return [
            f"{name} {'confirmed' if votes.get(name) == sign else 'absent'}"
            for name in confirm_factors
            if name in votes
        ]

    return {
        "trades": overall.trades,
        "win_rate": overall.win_rate,
        "expectancy": overall.expectancy,
        "total_r": overall.r_total,
        "profit_factor": profit_factor,
        "max_losing_streak": max_losing_streak(closed),
        "kelly_pct": kelly_pct,
        "suggested_risk_pct": suggested_risk_pct,
        "min_sample": MIN_SAMPLE,
        "by_symbol": _group(closed, lambda r: [r.symbol]),
        "by_direction": _group(closed, lambda r: [r.direction]),
        "by_strength": _group(closed, lambda r: [f"{r.strength}/4"] if r.strength else []),
        "by_session": _group(closed, lambda r: [session_of(r.fired_at)]),
        "by_factor": _group(closed, factor_key),
    }


def format_postmortem(report: dict) -> str:
    """Telegram HTML for the owner."""
    if not report["trades"]:
        return "🔬 <b>Postmortem</b>\n\nNo closed signals yet."

    pf = report["profit_factor"]
    lines = [
        "🔬 <b>Postmortem</b>",
        f"Closed signals: <b>{report['trades']}</b>",
        f"Win rate: <b>{report['win_rate']:.1f}%</b>",
        f"Expectancy: <b>{report['expectancy']:+.2f}R</b> per signal",
        f"Total: <b>{report['total_r']:+.1f}R</b>",
        f"Profit factor: <b>{pf:.2f}</b>" if pf is not None else "Profit factor: <b>n/a</b> (no losses yet)",
        f"Longest losing streak: <b>{report['max_losing_streak']}</b>",
    ]

    sections = [
        ("By symbol", report["by_symbol"]),
        ("By direction", report["by_direction"]),
        ("By confluence", report["by_strength"]),
        ("By session (UTC)", report["by_session"]),
        ("By confirming factor", report["by_factor"]),
    ]
    for title, buckets in sections:
        if not buckets:
            continue
        lines.extend(["", f"<b>{title}</b>"])
        for b in buckets[:8]:
            thin = " (small sample)" if b.trades < MIN_BUCKET else ""
            lines.append(
                f"{b.name}: {b.trades} trades, {b.win_rate:.0f}% win, "
                f"{b.expectancy:+.2f}R{thin}"
            )

    lines.append("")
    if report["suggested_risk_pct"] is None:
        lines.append(
            f"<b>Risk sizing</b>\nNeeds {report['min_sample']} closed signals "
            "with both wins and losses before a Kelly suggestion means anything."
        )
    elif report["kelly_pct"] <= 0:
        lines.append(
            "<b>Risk sizing</b>\nKelly is negative: these results show no edge. "
            "Reduce size until expectancy turns positive."
        )
    else:
        lines.append(
            f"<b>Risk sizing</b>\nFull Kelly: {report['kelly_pct']:.1f}% per trade. "
            f"Suggested (quarter Kelly, capped): <b>{report['suggested_risk_pct']:.2f}%</b>"
        )
    return "\n".join(lines)
