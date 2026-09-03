#!/usr/bin/env python3
"""
Convert between local wall-clock schedules and UTC cron expressions.

  tz.py to-cron --tz Asia/Taipei --at 09:00 --days mon-fri
  tz.py to-cron --tz Asia/Taipei --at 02:00 --days daily
  tz.py explain --tz Asia/Taipei "0 19 * * 6"

--tz defaults to $CRON_TZ, then the host timezone.
--days accepts: daily | mon-fri | sat,sun | a cron dow field (0-6, 1-5, etc.)

Only standard library (Python 3.9+ for zoneinfo). No cron daemon involved — this
just does the arithmetic so you don't shift the weekday by hand.
"""
import os
import sys
import argparse
import datetime as dt

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("need Python 3.9+ (zoneinfo)")

_DOW_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]  # cron: 0=Sun
_DAYS_ALIASES = {
    "daily": "*",
    "weekdays": "1-5",
    "mon-fri": "1-5",
    "everyday": "*",
    "weekend": "0,6",
    "sat,sun": "0,6",
}


def _resolve_tz(name: str | None) -> ZoneInfo:
    name = name or os.environ.get("CRON_TZ") or _host_tz()
    return ZoneInfo(name)


def _host_tz() -> str:
    tz = dt.datetime.now().astimezone().tzinfo
    return getattr(tz, "key", None) or "UTC"


def _parse_dow(days: str) -> set[int]:
    """Return the set of cron dow ints (0=Sun) the local schedule covers."""
    days = days.strip().lower()
    days = _DAYS_ALIASES.get(days, days)
    if days == "*":
        return set(range(7))
    out: set[int] = set()
    for part in days.split(","):
        part = part.strip()
        if part in _DOW_NAMES:
            out.add(_DOW_NAMES.index(part))
        elif "-" in part:
            a, b = part.split("-")
            a_i = _DOW_NAMES.index(a) if a in _DOW_NAMES else int(a)
            b_i = _DOW_NAMES.index(b) if b in _DOW_NAMES else int(b)
            rng = list(range(a_i, b_i + 1)) if a_i <= b_i else \
                list(range(a_i, 7)) + list(range(0, b_i + 1))
            out.update(rng)
        else:
            out.add(int(part))
    return out


def _fmt_dow(dows: set[int]) -> str:
    if dows == set(range(7)):
        return "*"
    s = sorted(dows)
    # collapse contiguous runs
    runs, start = [], s[0]
    for prev, cur in zip(s, s[1:] + [None]):
        if cur is None or cur != prev + 1:
            runs.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = cur
    return ",".join(runs)


def to_cron(args):
    tz = _resolve_tz(args.tz)
    hh, mm = map(int, args.at.split(":"))
    local_dows = _parse_dow(args.days)

    # pick a reference date (a Sunday) and walk 7 days, mapping each local
    # firing to its UTC hour/minute/weekday
    ref = dt.date(2001, 1, 7)  # a Sunday
    utc_minute = utc_hour = None
    utc_dows: set[int] = set()
    for offset in range(7):
        d = ref + dt.timedelta(days=offset)
        cron_dow_local = (d.weekday() + 1) % 7  # python Mon=0 -> cron Sun=0
        if cron_dow_local not in local_dows:
            continue
        local_dt = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
        u = local_dt.astimezone(ZoneInfo("UTC"))
        utc_minute, utc_hour = u.minute, u.hour
        utc_dows.add((u.weekday() + 1) % 7)

    expr = f"{utc_minute} {utc_hour} * * {_fmt_dow(utc_dows)}"
    note = f"{args.at} {tz.key} = {utc_hour:02d}:{utc_minute:02d} UTC"
    if utc_dows != local_dows and args.days not in ("daily", "*", "everyday"):
        note += " (weekday shifts across midnight UTC)"
    print(f"{expr}      # {note}")


def explain(args):
    tz = _resolve_tz(args.tz)
    parts = args.expr.split()
    if len(parts) != 5:
        sys.exit("expected a 5-field cron expression")
    mm, hh, _dom, _mon, dow = parts
    if "/" in hh or "," in hh or "-" in hh or "*" in hh:
        sys.exit("this helper only explains fixed hour/minute expressions")
    mm_i, hh_i = int(mm), int(hh)

    ref = dt.date(2001, 1, 7)  # Sunday
    lines = []
    want = _parse_dow(dow if dow != "*" else "daily")
    for offset in range(7):
        d = ref + dt.timedelta(days=offset)
        cron_dow = (d.weekday() + 1) % 7
        if cron_dow not in want:
            continue
        u = dt.datetime(d.year, d.month, d.day, hh_i, mm_i, tzinfo=ZoneInfo("UTC"))
        loc = u.astimezone(tz)
        lines.append(f"  {u:%A} {u:%H:%M} UTC  =  {loc:%A} {loc:%H:%M} {tz.key}")
    print("\n".join(sorted(set(lines))))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("to-cron", help="local wall-clock -> UTC cron expr")
    c.add_argument("--tz")
    c.add_argument("--at", required=True, help="HH:MM local, 24h")
    c.add_argument("--days", default="daily")
    c.set_defaults(func=to_cron)

    e = sub.add_parser("explain", help="UTC cron expr -> local time")
    e.add_argument("--tz")
    e.add_argument("expr")
    e.set_defaults(func=explain)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
