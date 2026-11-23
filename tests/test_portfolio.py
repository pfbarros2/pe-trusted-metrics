from datetime import date
from decimal import Decimal

from pe_metrics.calendar import resolve_window
from pe_metrics.db import fetch_dicts
from pe_metrics.generate import (
    NORTH_APPROVAL_RATE,
    WEST_APPROVAL_RATE,
    WEST_CORE_RATE,
    build_scenario,
)
from pe_metrics.money import q2


def test_ledgers_are_labelled_fictional(connection):
    notices = []
    notices.extend(fetch_dicts(connection, "SELECT fiction_notice FROM north.ledger.about"))
    notices.extend(fetch_dicts(connection, "SELECT FictionNotice AS fiction_notice FROM west.ops.About"))
    notices.extend(fetch_dicts(connection, "SELECT fiction_notice FROM holdco.ref.about"))
    assert len(notices) == 3
    for row in notices:
        text = row["fiction_notice"].lower()
        assert "fictional" in text
        assert "not real" in text or "not a real" in text or "are not real" in text


def test_each_fact_table_has_24_month_ends(connection, definitions):
    north = fetch_dicts(connection, "SELECT period_end FROM north.ledger.monthly_pack ORDER BY 1")
    west = fetch_dicts(connection, "SELECT MonthEnding FROM west.ops.MonthlyPnl ORDER BY 1")
    rates = definitions["reference"]["rates"]
    assert [row["period_end"] for row in north] == [row["period_end"] for row in rates]
    assert [row["MonthEnding"] for row in west] == [row["period_end"] for row in rates]
    assert len(north) == 24


def test_local_formulas_hold_on_every_row(connection):
    for row in fetch_dicts(connection, "SELECT * FROM north.ledger.monthly_pack"):
        assert row["billed_turnover_gross"] == (
            row["third_party_billed_gross"] + row["intercompany_billed_gross"]
        )
        assert row["reported_ebitda"] == (
            row["billed_turnover_gross"]
            - row["rebate_accrual"]
            - row["cash_opex_excl_rent"]
            - row["property_rent"]
            - row["one_off_costs"]
        )
    for row in fetch_dicts(connection, "SELECT * FROM west.ops.MonthlyPnl"):
        assert row["RecognizedNetSales"] == (
            row["GrossBillings"]
            - row["RebateContra"]
            - row["BilledNotRecognized"]
            + row["UnbilledRecognised"]
        )
        assert row["EbitdaAfterAddbacks"] == (
            row["RecognizedNetSales"]
            - row["CashOperatingCost"]
            + row["OneOffCharges"]
            + row["MgmtAddbackBooked"]
        )


def test_policy_schedule_uses_the_disclosed_rates(definitions):
    scenario = build_scenario(definitions)
    for row in scenario["policy"]:
        approved = q2(row["proposed_addback_functional"] * (
            NORTH_APPROVAL_RATE if row["entity_code"] == "madeup_north" else WEST_APPROVAL_RATE
        ))
        assert row["ic_approved_addback_functional"] == approved
        assert row["rejected_addback_functional"] == row["proposed_addback_functional"] - approved


def test_january_2024_anchor(definitions):
    scenario = build_scenario(definitions)
    north = scenario["north"][0]
    west = scenario["west"][0]
    policy = {(row["entity_code"], row["period_end"]): row for row in scenario["policy"]}
    assert north["period_end"] == date(2024, 1, 31)
    assert north["third_party_billed_gross"] == Decimal("3680000.00")
    assert north["rebate_accrual"] == Decimal("294400.00")
    assert north["deferred_unearned"] == Decimal("165600.00")
    assert north["prior_period_release"] == Decimal("0.00")
    net_revenue = (
        north["third_party_billed_gross"]
        - north["rebate_accrual"]
        - north["deferred_unearned"]
        + north["prior_period_release"]
    )
    assert net_revenue == Decimal("3220000.00")
    approved = policy[("madeup_north", date(2024, 1, 31))]["ic_approved_addback_functional"]
    assert approved == Decimal("27600.00")
    adjusted = net_revenue - north["cash_opex_excl_rent"] + approved
    assert adjusted == Decimal("579600.00")

    assert west["RecognizedNetSales"] == Decimal("5586608.00")
    core = q2(west["RecognizedNetSales"] * WEST_CORE_RATE)
    assert core == Decimal("4804482.88")
    west_approved = policy[("madeup_west", date(2024, 1, 31))]["ic_approved_addback_functional"]
    assert west_approved == Decimal("52992.00")
    west_adjusted = west["RecognizedNetSales"] - core + west_approved
    assert west_adjusted == Decimal("835117.12")


def test_ltm_december_2025_is_twelve_months(definitions):
    months = [row["period_end"] for row in definitions["reference"]["rates"]]
    chosen = resolve_window({"kind": "ltm", "end": "2025-12-31"}, months, 12)
    assert len(chosen) == 12
    assert chosen[0] == date(2025, 1, 31)
    assert chosen[-1] == date(2025, 12, 31)
    september = resolve_window({"kind": "ltm", "end": "2025-09-30"}, months, 12)
    assert september[0] == date(2024, 10, 31)
    assert september[-1] == date(2025, 9, 30)


def test_question_count_and_names(questions):
    assert len(questions) == 22
    texts = [question["text"] for question in questions]
    assert len(set(texts)) == 22
    joined = "\n".join(texts)
    assert "Madeup North Logistics Ltd" in joined
    assert "Madeup West Assemblies Inc" in joined
    assert ("Delo" + "itte") not in joined
    assert any(question["text"].startswith("What was consolidated LTM Adjusted EBITDA at Dec 2025") for question in questions)
