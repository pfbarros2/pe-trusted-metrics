from pe_metrics.evaluate import _example, evaluate, is_correct
from pe_metrics.oracle import Ledger
from pe_metrics.plans import numeric_columns, rank_columns


def test_heuristic_picks_the_headline_columns(connection, definitions):
    north_columns = numeric_columns(connection, "north.ledger.monthly_pack")
    west_columns = numeric_columns(connection, "west.ops.MonthlyPnl")
    assert rank_columns(north_columns, "net_revenue")[0]["column"] == "billed_turnover_gross"
    assert rank_columns(north_columns, "adjusted_ebitda")[0]["column"] == "reported_ebitda"
    assert rank_columns(north_columns, "ifrs16_lease_addback")[0]["column"] == "property_rent"
    assert rank_columns(north_columns, "declined_management_addback")[0]["column"] == "mgmt_addback_proposed"
    assert rank_columns(north_columns, "intercompany_revenue_eliminated")[0]["column"] == "intercompany_billed_gross"
    assert rank_columns(west_columns, "net_revenue")[0]["column"] == "GrossBillings"
    assert rank_columns(west_columns, "adjusted_ebitda")[0]["column"] == "EbitdaAfterAddbacks"
    assert rank_columns(west_columns, "ifrs16_lease_addback")[0]["column"] == "LeaseInterest"
    assert rank_columns(west_columns, "declined_management_addback")[0]["column"] == "MgmtAddbackBooked"
    assert rank_columns(west_columns, "intercompany_revenue_eliminated")[0]["column"] == "IntercompanyPurchasesUsd"


def test_fund_and_local_margin_rankings_differ(connection, definitions):
    example = _example(Ledger(connection, definitions), definitions)
    assert example["ltm_dec_2025_fund_margin_leader"] == "Madeup North Logistics Ltd"
    assert example["ltm_dec_2025_local_margin_leader"] == "Madeup West Assemblies Inc"
    assert example["ltm_dec_2025_fund_margin_leader"] != example["ltm_dec_2025_local_margin_leader"]


def test_grounded_matches_oracle_and_beats_baseline(connection, definitions, questions):
    result = evaluate(connection, definitions, questions)
    board = result["scoreboard"]
    assert board["n_questions"] == 22
    assert board["grounded_correct"] == 22
    assert board["baseline_correct"] < board["grounded_correct"]
    by_id = {row["id"]: row for row in board["per_question"]}
    assert by_id["Q01"]["baseline_within_tolerance"] is False
    assert by_id["Q04"]["baseline_within_tolerance"] is False
    assert by_id["Q08"]["baseline_within_tolerance"] is False
    assert by_id["Q21"]["baseline_within_tolerance"] is True
    assert by_id["Q22"]["baseline_within_tolerance"] is True
    assert by_id["Q16"]["baseline_within_tolerance"] is True
    assert by_id["Q16"]["baseline_guard_passed"] is False
    assert by_id["Q20"]["ground_truth"].startswith("0.") or by_id["Q20"]["ground_truth"].startswith("-")
    assert by_id["Q20"]["baseline"].startswith("-")
    assert not by_id["Q20"]["ground_truth"].startswith("-")
    assert by_id["Q21"]["ground_truth"] == "unanswerable"
    for row in result["grounded"]:
        if row["status"] == "answered":
            assert row["provenance"]["definition_guard"]["passed"] is True
            assert row["sql"]
    for truth, grounded in zip(result["ground_truth"], result["grounded"]):
        assert is_correct(truth, grounded)


def test_guard_rejects_baseline_revenue_sql(connection, definitions, questions):
    result = evaluate(connection, definitions, questions)
    by_id = {row["question_id"]: row for row in result["baseline"]}
    guard = by_id["Q01"]["provenance"]["definition_guard"]
    assert guard["passed"] is False
    assert "billed_turnover_gross" in guard["rejected_columns"]
    assert by_id["Q01"]["status"] == "answered"


def test_definitions_differ_from_local_headline(connection, definitions):
    ledger = Ledger(connection, definitions)
    january = ledger.months[0]
    fund_ebitda = ledger.functional("adjusted_ebitda", "madeup_north", january)
    reported = ledger.north[january]["reported_ebitda"]
    assert fund_ebitda != reported
    west_fund = ledger.functional("adjusted_ebitda", "madeup_west", january)
    west_local = ledger.west[january]["EbitdaAfterAddbacks"]
    assert west_fund != west_local
