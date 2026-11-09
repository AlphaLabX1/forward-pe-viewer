"""Daily rule outputs for v2 and U1, replayed from committed VX settles and UVXY closes."""

from __future__ import annotations

import csv
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from functools import cache
from pathlib import Path

ARM, DROP, TARGET, STOP, HOLD, MIN_DAYS, COST = 1.50, 0.60, -1.00, 3.00, 21, 10, 0.20
V2_NEAR = 0.30
U1_SIZE, U1_TARGET, U1_STOP, U1_HOLD, U1_NEAR = 0.05, 0.20, 1.00, 126, 0.05


def standard_expiry(y, m):
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    d = date(ny, nm, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7) - timedelta(days=30)


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    h = (19 * a + b - b // 4 - (b - f + 1) // 3 + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, dy = divmod(h + l - 7 * m + 114, 31)
    return date(y, mo, dy + 1)


@cache
def holidays(y):
    def nth(m, wd, n):
        d = date(y, m, 1) + timedelta(days=(wd - date(y, m, 1).weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def observed(d):
        return d + timedelta(days={5: -1, 6: 1}.get(d.weekday(), 0))

    memorial = date(y, 5, 31) - timedelta(days=date(y, 5, 31).weekday())
    out = {nth(1, 0, 3), nth(2, 0, 3), easter(y) - timedelta(days=2), memorial,
           nth(9, 0, 1), nth(11, 3, 4), observed(date(y, 7, 4)), observed(date(y, 12, 25))}
    if date(y, 1, 1).weekday() != 5:
        out.add(observed(date(y, 1, 1)))
    if y >= 2022:
        out.add(observed(date(y, 6, 19)))
    return out


@cache
def monthly_expiry(y, m):
    expiry = standard_expiry(y, m)
    friday = expiry + timedelta(days=30)
    if expiry in holidays(expiry.year) or friday in holidays(friday.year):
        expiry -= timedelta(days=1)
    return expiry


def upcoming_monthlies(day, count=4):
    expiries = [monthly_expiry(day.year + (day.month - 1 + k) // 12,
                               (day.month - 1 + k) % 12 + 1)
                for k in range(count + 1)]
    return [e for e in expiries if e > day][:count]


def sessions_between(a, b):
    n, d = 0, a + timedelta(days=1)
    while d < b:
        n += d.weekday() < 5 and d not in holidays(d.year)
        d += timedelta(days=1)
    return n


def session_after(d, n=1):
    for _ in range(n):
        d += timedelta(days=1)
        while d.weekday() >= 5 or d in holidays(d.year):
            d += timedelta(days=1)
    return d


def load_vx(path):
    px = {}
    if Path(path).exists():
        with Path(path).open() as f:
            for row in csv.DictReader(f):
                d, e = (date.fromisoformat(row[k]) for k in ("date", "expiry"))
                settle = float(row["settle"])
                if settle > 0:
                    px.setdefault(d, {})[e] = settle
    px = {d: prices for d, prices in px.items()
          if all(e in prices for e in upcoming_monthlies(d))}
    days = sorted(px)
    return days, px


def load_uvxy(path):
    if not Path(path).exists():
        return {}
    with Path(path).open() as f:
        return {date.fromisoformat(r["date"]): float(r["price"]) for r in csv.DictReader(f)}


class State(str, Enum):
    FLAT = "FLAT"
    ARMED = "ARMED"
    NEAR_TRIGGER = "NEAR_TRIGGER"
    WATCH = "WATCH"
    NEAR_TARGET = "NEAR_TARGET"
    SIGNAL = "SIGNAL"
    IN_TRADE = "IN_TRADE"
    EXIT_DUE = "EXIT_DUE"
    SHORT = "SHORT"
    COVER_DUE = "COVER_DUE"


@dataclass(frozen=True)
class Snapshot:
    tag: State
    as_of: date | None
    values: dict
    action: str


@dataclass(frozen=True)
class Transition:
    date: date
    state: State


@dataclass(frozen=True)
class V2Result:
    trades: list[dict]
    state: Snapshot
    transitions: list[Transition]


@dataclass(frozen=True)
class U1Result:
    trades: list[dict]
    state: Snapshot
    transitions: list[Transition]


def leg_prices(px, day, pair):
    """The two contracts a rule compares on `day`, with their settles."""
    return dict(leg1=str(pair[0]), leg1_price=px[day][pair[0]], leg2=str(pair[1]), leg2_price=px[day][pair[1]])


def v2_replay(days, px) -> V2Result:
    days = list(days)
    if not days:
        return V2Result([], Snapshot(State.FLAT, None, {}, "No VX settles available."), [])

    def ladder(i):
        return sorted(e for e in px[days[i]] if e > days[i])

    def remaining(i, expiry):
        return max(0, bisect_left(days, expiry) - i) + sessions_between(days[-1], expiry)

    def pair(i):
        lad = ladder(i)
        return tuple(lad[1:3] if remaining(i, lad[0]) < MIN_DAYS else lad[:2])

    def spread(i, legs):
        return px[days[i]][legs[0]] - px[days[i]][legs[1]]

    def high(i, legs):
        return max(spread(a, legs) for a in range(arm_i, i + 1)
                   if all(e in px[days[a]] for e in legs))

    trades, transitions = [], []
    legs = lambda day, pair: leg_prices(px, day, pair)
    tag, armed, arm_i, peak = State.FLAT, False, 0, 0.0
    position, pending, values = None, None, {}
    action = "Wait for S to rise above 1.50."

    def close(j, why):
        nonlocal position, armed, tag, pending, values, action
        prices = px[days[j]]
        nr, fr, third = (date.fromisoformat(position[k]) for k in ("near", "far", "third"))
        s1 = spread(j, (nr, fr))
        net = -(prices[nr] - entry_prices[nr]) + (prices[third] - entry_prices[third]) - COST
        trades.append(dict(position, exit=str(days[j]), s_entry=round(position["s_entry"], 4),
                           s_exit=round(s1, 4), reason=why, net=round(net, 4),
                           net_b1legs=round(position["s_entry"] - s1 - COST, 4)))
        position, armed, tag, pending = None, False, State.FLAT, None
        values = dict(last_exit=str(days[j]), reason=why)
        action = "Stay flat; resume checking for S above 1.50 at the next settle."
        transitions.append(Transition(days[j], tag))

    for i, day in enumerate(days):
        before = tag
        if i > 0 and session_after(days[i - 1]) < day:
            if position is not None:
                raise ValueError(f"Missing VX sessions during an open trade before {day}")
            armed, pending, tag = False, None, State.FLAT
        if pending == "entry":
            nr, fr = pair(i)
            lad = ladder(i)
            if lad.index(fr) + 1 == len(lad):
                raise ValueError(f"No priced contract after {fr} on {day}")
            third = lad[lad.index(fr) + 1]
            position = dict(signal=signal, entry=str(day), near=str(nr), far=str(fr),
                            third=str(third), s_entry=spread(i, (nr, fr)))
            entry_i, entry_prices, zero = i, px[day], False
            tag, pending = State.IN_TRADE, None
        elif pending == "exit":
            close(i, reason)
            continue

        if position is not None:
            nr, fr, third = (date.fromisoformat(position[k]) for k in ("near", "far", "third"))
            s = spread(i, (nr, fr))
            reason = ""
            if i > entry_i:
                next_day = days[i + 1] if i + 1 < len(days) else session_after(day)
                if next_day >= nr:
                    reason = "expiry"
                elif s <= TARGET:
                    reason = "target"
                elif s >= position["s_entry"] + STOP:
                    reason = "stop"
                elif zero and s >= position["s_entry"]:
                    reason = "trail"
                elif i + 1 - entry_i >= HOLD:
                    reason = "time"
                zero = zero or s <= 0.0
            unrealised = -(px[day][nr] - entry_prices[nr]) + (px[day][third] - entry_prices[third])
            values = dict(position, **legs(day, (nr, fr)), current_s=s, target=TARGET,
                          stop_level=position["s_entry"] if zero else position["s_entry"] + STOP,
                          trail_live=zero, sessions_held=i - entry_i, hold=HOLD,
                          unrealised=unrealised, reason=reason)
            if reason == "expiry":
                # The reference closes at the last pre-expiry settle, not the expiry settle.
                close(i, reason)
                continue
            if reason:
                tag, pending = State.EXIT_DUE, "exit"
                action = f"Cover short {nr} and sell long {third} at the next settle ({reason})."
            elif nth_session(days, i, 2) >= nr:
                # Display only: the expiry rule itself closes at the next settle, as in the reference.
                tag = State.EXIT_DUE
                values["reason"] = "expiry"
                action = f"Cover short {nr} and sell long {third} at the next settle, the last before {nr} expires."
            else:
                tag = State.IN_TRADE
                action = f"Hold short {nr} / long {third}; check exits at the next settle."
        else:
            active = pair(i)
            s = spread(i, active)
            fire = False
            if not armed:
                if s > ARM:
                    armed, arm_i, peak = True, i, s
            elif i > 0 and active != pair(i - 1):
                old = pair(i - 1)
                fire = all(e in px[day] for e in old) and spread(i, old) <= high(i, old) - DROP
                peak = high(i, active)
            else:
                peak = max(peak, s)
                fire = s <= peak - DROP
            lad = ladder(i)
            n = remaining(i, lad[0])
            switch = n - MIN_DAYS + 1 if n >= MIN_DAYS else remaining(i, lad[1]) - MIN_DAYS + 1
            values = dict(near=str(active[0]), far=str(active[1]), **legs(day, active), current_s=s,
                          sessions_to_switch=max(0, switch))
            tag = State.ARMED if armed else State.FLAT
            if armed:
                values.update(arm_date=str(days[arm_i]), high=peak, trigger=peak - DROP)
                if s - (peak - DROP) <= V2_NEAR:
                    tag = State.NEAR_TRIGGER
            action = ("Wait for S to rise above 1.50." if not armed else
                      f"Signal fires on a settle with S at or below {peak - DROP:.2f}; S is {s - (peak - DROP):.2f} above it."
                      if tag == State.NEAR_TRIGGER else
                      "Wait for S to fall 0.60 from its own high since arming.")
            if fire:
                signal = str(day)
                tag, pending = State.SIGNAL, "entry"
                projected_day = session_after(day)
                projected = [e for e in lad if e > projected_day]
                count = sessions_between(day, projected[0])
                offset = 1 if count < MIN_DAYS else 0
                if len(projected) <= offset + 2:
                    raise ValueError(f"No priced L1 legs for next session on {day}")
                values.update(signal=signal, near=str(projected[offset]),
                              far=str(projected[offset + 1]), third=str(projected[offset + 2]))
                action = (f"Enter short {values['near']} / long {values['third']} at the next settle; "
                          "select the pair again on the fill date.")
        if tag != before:
            transitions.append(Transition(day, tag))

    return V2Result(trades, Snapshot(tag, days[-1], values, action), transitions)


def nth_session(days, i, k):
    """The k-th session after days[i], from stored days first and the holiday calendar after them."""
    if i + k < len(days):
        return days[i + k]
    return session_after(days[-1], i + k - len(days) + 1)


def u1_replay(days, px, uvxy) -> U1Result:
    """Short UVXY at the close after the first VX contango close that follows backwardation.
    Backwardation compares the first two unexpired monthlies, with no switch before expiry."""
    days = [d for d in days if d in uvxy]
    trades, transitions = [], []
    legs = lambda day, pair: leg_prices(px, day, pair)
    tag, position, pending, streak, values = State.FLAT, None, None, 0, {}
    action = "Wait for VX backwardation."
    if not days:
        return U1Result([], Snapshot(tag, None, {}, "No VX settles with UVXY closes available."), [])

    def close(day, price, why):
        nonlocal position
        trades.append(dict(position, exit=str(day), exit_price=price, reason=why,
                           return_pct=(1 - price / position["entry_price"]) * 100))
        position = None

    for i, day in enumerate(days):
        before, price = tag, uvxy[day]
        front = sorted(e for e in px[day] if e > day)[:2]
        c1, c2 = (px[day][e] for e in front)
        if pending == "entry":
            position = dict(signal=signal, entry=str(day), entry_price=price, size=U1_SIZE)
            entry_i = i
        elif pending:
            close(day, price, pending)
        pending = None
        backwardation = c1 > c2
        values = dict(**legs(day, front), backwardation=backwardation, uvxy=price)
        tag = State.FLAT
        if position is not None and i > entry_i:
            target, stop = position["entry_price"] * (1 - U1_TARGET), position["entry_price"] * (1 + U1_STOP)
            if i + 1 - entry_i >= U1_HOLD:
                pending = "time"
            elif price <= target:
                close(day, price, "target")
                values.update(last_exit=str(day), reason="target")
            elif price >= stop:
                pending = "stop"
        if position is not None:
            target, stop = position["entry_price"] * (1 - U1_TARGET), position["entry_price"] * (1 + U1_STOP)
            held = i - entry_i
            values.update(position, target_price=target, stop_price=stop, sessions_held=held, hold=U1_HOLD,
                          change_pct=(price / position["entry_price"] - 1) * 100,
                          time_exit=str(nth_session(days, i, U1_HOLD - held)))
            if pending:
                tag = State.COVER_DUE
                values["reason"] = pending
                action = f"Cover the UVXY short at the next close ({pending})."
            else:
                tag = State.NEAR_TARGET if price <= target * (1 + U1_NEAR) else State.SHORT
                action = (f"Hold. If UVXY is at or below {target:.2f} near the close, cover at that close; "
                          f"a close at or above {stop:.2f} means cover at the following close.")
        elif not backwardation and streak >= 1:
            signal = str(day)
            tag, pending = State.SIGNAL, "entry"
            values.update(signal=signal, size=U1_SIZE)
            action = "Short UVXY at the next close, notional 5% of equity."
        elif backwardation:
            tag = State.WATCH
            action = "VX is in backwardation. The first settle with C1 at or below C2 signals a short at the close after it."
        else:
            action = ("Covered at today's close on the 20% target; wait for VX backwardation."
                      if values.get("reason") == "target" else "Wait for VX backwardation.")
        streak = streak + 1 if backwardation else 0
        if tag != before:
            transitions.append(Transition(day, tag))
    return U1Result(trades, Snapshot(tag, days[-1], values, action), transitions)


def format_state(name, result):
    state = result.state
    lines = [f"{name} | {state.as_of or 'unavailable'} | {state.tag.value}"]
    for key, value in state.values.items():
        if isinstance(value, float):
            value = f"{value:.4f}"
        lines.append(f"  {key}: {value}")
    lines.append(f"Next session: {state.action}")
    return "\n".join(lines)


def main():
    data = Path(__file__).parent / "data"
    print(format_state("v2", v2_replay(*load_vx(data / "vx_settle.csv"))))
    print(format_state("U1", u1_replay(*load_vx(data / "vx_settle.csv"), load_uvxy(data / "uvxy_price.csv"))))
    print("Rule outputs, not advice.")


if __name__ == "__main__":
    main()
