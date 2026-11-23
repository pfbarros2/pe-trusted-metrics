"""Decimal helpers. Currency is rounded half away from zero, matching DuckDB CAST."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")
RATIO_Q = Decimal("0.000001")
HUNDRED = Decimal("100")
PCT_Q = Decimal("0.0001")
PP_Q = Decimal("0.0001")

MONEY_TOLERANCE = Decimal("0.01")
RATIO_TOLERANCE = Decimal("0.0000005")
PP_TOLERANCE = Decimal("0.00005")


def D(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q2(value: object) -> Decimal:
    return D(value).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def quant_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        raise ZeroDivisionError("denominator is zero")
    return (numerator / denominator).quantize(RATIO_Q, rounding=ROUND_HALF_UP)


def pack_money(amount: Decimal, currency: str) -> tuple[str, str]:
    amount = q2(amount)
    return f"{amount:.2f}", f"{currency} {amount:,.2f}"


def pack_ratio(numerator: Decimal, denominator: Decimal) -> tuple[str, str, Decimal]:
    ratio = quant_ratio(numerator, denominator)
    percent = (ratio * HUNDRED).quantize(PCT_Q, rounding=ROUND_HALF_UP)
    return f"{ratio:.6f}", f"{percent:.4f}%", ratio


def pack_growth(earlier: Decimal, later: Decimal) -> tuple[str, str, Decimal]:
    if earlier == 0:
        raise ZeroDivisionError("earlier period is zero")
    growth = (later / earlier - 1).quantize(RATIO_Q, rounding=ROUND_HALF_UP)
    percent = (growth * HUNDRED).quantize(PCT_Q, rounding=ROUND_HALF_UP)
    return f"{growth:.6f}", f"{percent:.4f}%", growth


def pack_pp(earlier_ratio: Decimal, later_ratio: Decimal) -> tuple[str, str]:
    points = ((later_ratio - earlier_ratio) * HUNDRED).quantize(
        PP_Q, rounding=ROUND_HALF_UP
    )
    return f"{points:.4f}", f"{points:.4f} pp"


def choose_entity(scored: list[tuple[str, Decimal]], names: dict[str, str]) -> str:
    ordered = sorted(scored, key=lambda item: item[1], reverse=True)
    if len(ordered) >= 2 and ordered[0][1] == ordered[1][1]:
        return "tie"
    return names[ordered[0][0]]
