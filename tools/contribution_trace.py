#!/usr/bin/env python3
"""Render a source-bound GitHub contribution trace as deterministic SVG."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          firstDay
          contributionDays {
            date
            contributionCount
            contributionLevel
            weekday
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class ContributionDay:
    day: date
    count: int
    level: str
    weekday: int
    week: int


@dataclass(frozen=True)
class ContributionCalendar:
    username: str
    total: int
    days: tuple[ContributionDay, ...]

    @property
    def first_day(self) -> date:
        return self.days[0].day

    @property
    def last_day(self) -> date:
        return self.days[-1].day

    @property
    def active_days(self) -> int:
        return sum(day.count > 0 for day in self.days)

    @property
    def peak(self) -> int:
        return max(day.count for day in self.days)

    @property
    def week_count(self) -> int:
        return max(day.week for day in self.days) + 1

    @property
    def digest(self) -> str:
        canonical = [
            (item.day.isoformat(), item.count, item.level, item.weekday, item.week)
            for item in self.days
        ]
        encoded = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    surface: str
    border: str
    grid: str
    text: str
    muted: str
    faint: str
    cyan: str
    magenta: str
    green: str
    cells: tuple[str, str, str, str, str]


THEMES = {
    "dark": Theme(
        name="dark",
        background="#05080d",
        surface="#0a1018",
        border="#253140",
        grid="#17212c",
        text="#e5e9f0",
        muted="#98a4b4",
        faint="#596779",
        cyan="#62d8f7",
        magenta="#ff2f7d",
        green="#a4e77c",
        cells=("#111a24", "#223543", "#31566a", "#4c8399", "#ff2f7d"),
    ),
    "light": Theme(
        name="light",
        background="#f7f8fb",
        surface="#ffffff",
        border="#ccd5e0",
        grid="#e5eaf0",
        text="#17202c",
        muted="#566477",
        faint="#8995a4",
        cyan="#087f9c",
        magenta="#ca155c",
        green="#3b7d2b",
        cells=("#edf1f5", "#d3e1e8", "#9bc5d3", "#4e96ab", "#ca155c"),
    ),
}


def fetch_payload(
    username: str,
    token: str,
    *,
    endpoint: str = GRAPHQL_ENDPOINT,
) -> Mapping[str, Any]:
    if not token:
        raise ValueError("A GitHub token is required for a live capture")
    body = json.dumps(
        {
            "query": QUERY,
            "variables": {"login": username},
        }
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "f3d1-contribution-trace/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub contribution query failed: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("GitHub returned a non-object GraphQL payload")
    return payload


def parse_calendar(payload: Mapping[str, Any], username: str) -> ContributionCalendar:
    if payload.get("errors"):
        raise ValueError(f"GitHub GraphQL returned errors: {payload['errors']!r}")
    try:
        calendar = payload["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]
        weeks = calendar["weeks"]
        declared_total = int(calendar["totalContributions"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "GitHub contribution payload does not match the declared schema"
        ) from exc

    if not isinstance(weeks, Sequence) or not 52 <= len(weeks) <= 54:
        raise ValueError("Expected 52 to 54 contribution weeks")

    days: list[ContributionDay] = []
    for week_index, week in enumerate(weeks):
        if not isinstance(week, Mapping) or not isinstance(
            week.get("contributionDays"), Sequence
        ):
            raise ValueError(f"Week {week_index} is missing contributionDays")
        for raw in week["contributionDays"]:
            try:
                parsed = ContributionDay(
                    day=date.fromisoformat(str(raw["date"])),
                    count=int(raw["contributionCount"]),
                    level=str(raw["contributionLevel"]),
                    weekday=int(raw["weekday"]),
                    week=week_index,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid contribution day in week {week_index}"
                ) from exc
            if parsed.count < 0:
                raise ValueError("Contribution counts cannot be negative")
            if parsed.level not in LEVELS:
                raise ValueError(f"Unknown contribution level: {parsed.level}")
            if not 0 <= parsed.weekday <= 6:
                raise ValueError("GitHub weekday must be between 0 and 6")
            days.append(parsed)

    days.sort(key=lambda item: item.day)
    if not 350 <= len(days) <= 371:
        raise ValueError("Expected a complete one-year contribution calendar")
    if len({item.day for item in days}) != len(days):
        raise ValueError("Contribution calendar contains duplicate dates")
    observed_total = sum(item.count for item in days)
    if observed_total != declared_total:
        raise ValueError(
            f"Contribution total mismatch: declared {declared_total}, observed {observed_total}"
        )
    return ContributionCalendar(
        username=username, total=declared_total, days=tuple(days)
    )


def trace_checkpoints(calendar: ContributionCalendar) -> tuple[ContributionDay, ...]:
    checkpoints: list[ContributionDay] = []
    for week in range(calendar.week_count):
        active = [
            item for item in calendar.days if item.week == week and item.count > 0
        ]
        if active:
            checkpoints.append(max(active, key=lambda item: (item.count, item.day)))
    if len(checkpoints) < 2:
        checkpoints = [item for item in calendar.days if item.count > 0]
    return tuple(checkpoints)


def _trace_path(points: Sequence[tuple[float, float]]) -> str:
    if not points:
        return "M48 154 H738"
    commands = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    for previous, current in zip(points, points[1:]):
        mid_x = (previous[0] + current[0]) / 2
        commands.append(
            f"C{mid_x:.1f} {previous[1]:.1f} {mid_x:.1f} {current[1]:.1f} "
            f"{current[0]:.1f} {current[1]:.1f}"
        )
    return " ".join(commands)


def _render_cells(calendar: ContributionCalendar, theme: Theme) -> str:
    parts: list[str] = []
    for index, item in enumerate(calendar.days):
        x = 48 + item.week * 13
        y = 96 + item.weekday * 13
        level = LEVELS[item.level]
        classes = "cell hot" if level == 4 else "cell"
        delay = (index % 17) * 0.17
        title = f"{item.day.isoformat()}: {item.count} contributions"
        parts.append(
            f'<g><title>{html.escape(title)}</title><rect class="{classes}" '
            f'x="{x}" y="{y}" width="9" height="9" rx="2" '
            f'fill="{theme.cells[level]}" style="animation-delay:-{delay:.2f}s"/></g>'
        )
    return "\n      ".join(parts)


def render_svg(
    calendar: ContributionCalendar,
    *,
    theme_name: str,
    generated_on: date,
) -> str:
    theme = THEMES[theme_name]
    checkpoints = trace_checkpoints(calendar)
    points = tuple(
        (48 + item.week * 13 + 4.5, 96 + item.weekday * 13 + 4.5)
        for item in checkpoints
    )
    path = _trace_path(points)
    username = html.escape(calendar.username)
    title = (
        f"{username} contribution trace: {calendar.total} contributions across "
        f"{calendar.active_days} active days"
    )
    description = (
        f"A source-bound replay of the GitHub contribution calendar from "
        f"{calendar.first_day.isoformat()} through {calendar.last_day.isoformat()}."
    )
    cells = _render_cells(calendar, theme)
    digest = calendar.digest[:10]
    hot_days = sum(item.level == "FOURTH_QUARTILE" for item in calendar.days)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="320" viewBox="0 0 1200 320" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">{description}</desc>
  <metadata>source=github-graphql; snapshot={digest}; generated={generated_on.isoformat()}</metadata>
  <defs>
    <linearGradient id="surface" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{theme.surface}"/>
      <stop offset="1" stop-color="{theme.background}"/>
    </linearGradient>
    <linearGradient id="signal" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{theme.magenta}"/>
      <stop offset=".54" stop-color="#8a77c9"/>
      <stop offset="1" stop-color="{theme.cyan}"/>
    </linearGradient>
    <linearGradient id="scan" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{theme.cyan}" stop-opacity="0"/>
      <stop offset=".5" stop-color="{theme.cyan}" stop-opacity=".36"/>
      <stop offset="1" stop-color="{theme.cyan}" stop-opacity="0"/>
    </linearGradient>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="{theme.grid}" stroke-opacity=".42"/>
    </pattern>
    <filter id="glow" x="-300%" y="-300%" width="600%" height="600%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="clip"><rect x="1" y="1" width="1198" height="318" rx="15"/></clipPath>
    <path id="activity-route" d="{path}"/>
    <style>
      .sans {{ font-family: "Segoe UI", Arial, sans-serif; }}
      .mono {{ font-family: Consolas, "Liberation Mono", monospace; }}
      .meta {{ fill: {theme.muted}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }}
      .cell {{ transition: opacity .2s ease; }}
      .hot {{ animation: breathe 4.8s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
      .trace-flow {{ animation: flow 7s linear infinite; }}
      .scanner {{ animation: scan-grid 10s ease-in-out infinite; }}
      .status {{ animation: status 7s steps(1) infinite; }}
      @keyframes breathe {{ 0%, 100% {{ opacity: .72; }} 50% {{ opacity: 1; transform: scale(1.08); }} }}
      @keyframes flow {{ to {{ stroke-dashoffset: -76; }} }}
      @keyframes scan-grid {{ 0%, 12% {{ opacity: 0; transform: translateX(0); }} 26% {{ opacity: .75; }} 70% {{ opacity: .14; transform: translateX(640px); }} 76%, 100% {{ opacity: 0; transform: translateX(640px); }} }}
      @keyframes status {{ 0%, 24% {{ opacity: .45; }} 25%, 73% {{ opacity: 1; }} 74%, 100% {{ opacity: .65; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .hot, .trace-flow, .scanner, .status {{ animation: none; }}
        .crawler, .scanner {{ display: none; }}
        .trace-flow {{ stroke-dasharray: none; opacity: .58; }}
      }}
    </style>
  </defs>
  <g clip-path="url(#clip)">
    <rect width="1200" height="320" fill="url(#surface)"/>
    <rect width="1200" height="320" fill="url(#grid)"/>
    <rect x="0" y="55" width="1200" height="1" fill="{theme.border}"/>
    <rect x="775" y="76" width="1" height="185" fill="{theme.border}"/>
    <rect x="0" y="278" width="1200" height="1" fill="{theme.border}"/>

    <g class="mono">
      <text x="30" y="35" fill="{theme.magenta}" font-size="16" font-weight="700">f3d1__</text>
      <text x="105" y="34" class="meta">ACTIVITY TRACE / ROLLING YEAR</text>
      <circle cx="802" cy="30" r="3" fill="{theme.green}" filter="url(#glow)"/>
      <text x="816" y="34" class="meta">SOURCE GITHUB GRAPHQL</text>
      <text x="1168" y="34" class="meta" text-anchor="end">SNAPSHOT {digest.upper()}</text>
    </g>

    <g aria-label="GitHub contribution calendar">
      {cells}
      <use href="#activity-route" fill="none" stroke="{theme.cyan}" stroke-opacity=".16" stroke-width="7" stroke-linecap="round"/>
      <use class="trace-flow" href="#activity-route" fill="none" stroke="url(#signal)" stroke-width="2.2" stroke-linecap="round" stroke-dasharray="3 11"/>
      <g class="crawler" aria-hidden="true">
        <circle r="5.8" fill="{theme.cyan}" filter="url(#glow)"><animateMotion dur="12s" repeatCount="indefinite"><mpath href="#activity-route"/></animateMotion></circle>
        <circle r="3.8" fill="#8a77c9" opacity=".82" filter="url(#glow)"><animateMotion dur="12s" begin="-0.22s" repeatCount="indefinite"><mpath href="#activity-route"/></animateMotion></circle>
        <circle r="2.4" fill="{theme.magenta}" opacity=".7"><animateMotion dur="12s" begin="-0.42s" repeatCount="indefinite"><mpath href="#activity-route"/></animateMotion></circle>
      </g>
      <rect class="scanner" x="42" y="88" width="48" height="108" fill="url(#scan)" opacity="0"/>
    </g>

    <g class="mono">
      <text x="48" y="220" class="meta">{calendar.first_day.strftime("%Y.%m.%d")}</text>
      <line x1="153" y1="216" x2="690" y2="216" stroke="{theme.border}"/>
      <circle cx="303" cy="216" r="2" fill="{theme.magenta}"/>
      <circle cx="496" cy="216" r="2" fill="#8a77c9"/>
      <circle cx="690" cy="216" r="2" fill="{theme.cyan}"/>
      <text x="738" y="220" class="meta" text-anchor="end">{calendar.last_day.strftime("%Y.%m.%d")}</text>
      <text x="48" y="249" fill="{theme.text}" font-size="11" font-weight="700" letter-spacing="1">OBSERVED ACTIVITY / REPLAYED DAILY</text>
      <text x="738" y="249" class="meta" text-anchor="end">{len(checkpoints):02d} WEEKLY CHECKPOINTS / {hot_days:02d} PEAK DAYS</text>
    </g>

    <g transform="translate(814 80)">
      <text class="mono meta" fill="{theme.cyan}">SOURCE-BOUND SIGNAL</text>
      <text y="60" class="sans" fill="{theme.text}" font-size="46" font-weight="700" letter-spacing="-2">{calendar.total:,}</text>
      <text y="81" class="mono meta">CONTRIBUTIONS</text>

      <line y1="104" x2="332" y2="104" stroke="{theme.border}"/>
      <text y="132" class="mono meta">ACTIVE DAYS</text>
      <text x="332" y="132" class="mono" fill="{theme.text}" font-size="13" font-weight="700" text-anchor="end">{calendar.active_days} / {len(calendar.days)}</text>
      <text y="160" class="mono meta">PEAK DAY</text>
      <text x="332" y="160" class="mono" fill="{theme.text}" font-size="13" font-weight="700" text-anchor="end">{calendar.peak}</text>
      <text y="188" class="mono meta">CAPTURE</text>
      <g class="status">
        <circle cx="269" cy="184" r="3" fill="{theme.magenta}"/>
        <line x1="278" y1="184" x2="300" y2="184" stroke="{theme.border}"/>
        <circle cx="309" cy="184" r="3" fill="#8a77c9"/>
        <line x1="318" y1="184" x2="326" y2="184" stroke="{theme.border}"/>
        <circle cx="332" cy="184" r="4" fill="{theme.cyan}" filter="url(#glow)"/>
      </g>
    </g>

    <g class="mono" transform="translate(30 302)">
      <text class="meta">OBSERVE</text>
      <text x="86" class="meta" fill="{theme.magenta}">/</text>
      <text x="104" class="meta">ORDER</text>
      <text x="178" class="meta" fill="#8a77c9">/</text>
      <text x="196" class="meta">REPLAY</text>
      <text x="1168" class="meta" text-anchor="end">GENERATED {generated_on.isoformat()} / NO THIRD-PARTY RENDERER</text>
    </g>
  </g>
  <rect x=".5" y=".5" width="1199" height="319" rx="15" fill="none" stroke="{theme.border}"/>
</svg>
'''


def write_outputs(
    calendar: ContributionCalendar,
    output_dir: Path,
    *,
    generated_on: date,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dark = output_dir / "contribution-trace-dark.svg"
    light = output_dir / "contribution-trace-light.svg"
    dark.write_text(
        render_svg(calendar, theme_name="dark", generated_on=generated_on),
        encoding="utf-8",
        newline="\n",
    )
    light.write_text(
        render_svg(calendar, theme_name="light", generated_on=generated_on),
        encoding="utf-8",
        newline="\n",
    )
    return dark, light


def _load_payload(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Input fixture must contain a JSON object")
    return payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="smigolsmigol")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--input", type=Path, help="Replay a retained GraphQL JSON payload"
    )
    parser.add_argument(
        "--generated-on",
        type=date.fromisoformat,
        help="Override the rendered YYYY-MM-DD date for deterministic replay",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = _utc_now()
    payload = (
        _load_payload(args.input)
        if args.input
        else fetch_payload(
            args.username,
            os.environ.get("GITHUB_TOKEN", ""),
        )
    )
    calendar = parse_calendar(payload, args.username)
    generated_on = args.generated_on or now.date()
    dark, light = write_outputs(calendar, args.output_dir, generated_on=generated_on)
    print(
        json.dumps(
            {
                "active_days": calendar.active_days,
                "dark": str(dark),
                "days": len(calendar.days),
                "digest": calendar.digest,
                "light": str(light),
                "peak": calendar.peak,
                "total": calendar.total,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
