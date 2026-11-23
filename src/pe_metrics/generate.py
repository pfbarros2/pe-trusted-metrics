"""Build the two fictional company ledgers and the fund reference database.

The accounting policies are the point of the demo. Madeup North bills gross,
keeps rent and one-offs inside EBITDA, and ignores recognition timing.
Madeup West recognises net sales, reports post-IFRS 16 EBITDA, and books a
management add-back the fund mostly declines.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from pe_metrics.definitions import load_definitions
from pe_metrics.money import q2

NORTH_TP_BASE = Decimal("4000000")
NORTH_REBATE_RATE = Decimal("0.08")
NORTH_DEFER_RATE = Decimal("0.045")
NORTH_RELEASE_RATE = Decimal("0.55")
NORTH_OPEX_RATE = Decimal("0.725")
NORTH_RENT_BASE = Decimal("400000")
NORTH_RENT_STEP = Decimal("0.002")
NORTH_PROPOSED_BASE = Decimal("75000")
NORTH_APPROVAL_RATE = Decimal("0.40")
NORTH_IC_BASE = Decimal("120000")

WEST_GROSS_BASE = Decimal("6800000")
WEST_REBATE_RATE = Decimal("0.09")
WEST_BILLED_NOT_REC_RATE = Decimal("0.035")
WEST_UNBILLED_RATE = Decimal("0.018")
WEST_CORE_RATE = Decimal("0.86")
WEST_MGMT_BASE = Decimal("480000")
WEST_APPROVAL_RATE = Decimal("0.12")
WEST_LEASE_DEPRECIATION = Decimal("95000.00")
WEST_LEASE_INTEREST = Decimal("42000.00")
GROWTH_STEP = Decimal("0.005")

SEASON = {
    1: Decimal("0.92"),
    2: Decimal("0.94"),
    3: Decimal("0.98"),
    4: Decimal("1.01"),
    5: Decimal("1.04"),
    6: Decimal("1.07"),
    7: Decimal("1.05"),
    8: Decimal("1.03"),
    9: Decimal("1.00"),
    10: Decimal("0.99"),
    11: Decimal("0.97"),
    12: Decimal("1.00"),
}

NORTH_ONE_OFFS = {
    date(2024, 3, 31): Decimal("350000.00"),
    date(2024, 11, 30): Decimal("180000.00"),
    date(2025, 3, 31): Decimal("700000.00"),
    date(2025, 9, 30): Decimal("420000.00"),
}
WEST_ONE_OFFS = {
    date(2024, 6, 30): Decimal("250000.00"),
    date(2025, 2, 28): Decimal("190000.00"),
    date(2025, 8, 31): Decimal("310000.00"),
}

FICTION = (
    "FICTIONAL DEMO LEDGER. Madeup North Logistics Ltd, Madeup West "
    "Assemblies Inc, and Madeup Holdco LP are not real companies. "
    "No client data."
)


def build_scenario(definitions: dict | None = None) -> dict:
    definitions = definitions or load_definitions()
    rates = definitions["reference"]["rates"]
    north_rows = []
    west_rows = []
    policy_rows = []
    previous_deferred = Decimal("0.00")
    for index, rate in enumerate(rates):
        period = rate["period_end"]
        fx = rate["gbp_per_usd"]
        season = SEASON[period.month]
        growth = Decimal(1) + GROWTH_STEP * index

        third_party = q2(NORTH_TP_BASE * season * growth)
        ic_target = q2(NORTH_IC_BASE * season)
        intercompany_usd = q2(ic_target / fx)
        intercompany_gbp = q2(intercompany_usd * fx)
        rebate = q2(third_party * NORTH_REBATE_RATE)
        deferred = q2(third_party * NORTH_DEFER_RATE)
        release = q2(previous_deferred * NORTH_RELEASE_RATE)
        previous_deferred = deferred
        opex = q2(third_party * NORTH_OPEX_RATE)
        rent = q2(NORTH_RENT_BASE * (Decimal(1) + NORTH_RENT_STEP * index))
        one_off = NORTH_ONE_OFFS.get(period, Decimal("0.00"))
        proposed = q2(NORTH_PROPOSED_BASE * season)
        approved = q2(proposed * NORTH_APPROVAL_RATE)
        rejected = proposed - approved
        billed = third_party + intercompany_gbp
        reported = billed - rebate - opex - rent - one_off
        north_rows.append(
            {
                "period_end": period,
                "third_party_billed_gross": third_party,
                "intercompany_billed_gross": intercompany_gbp,
                "billed_turnover_gross": billed,
                "rebate_accrual": rebate,
                "deferred_unearned": deferred,
                "prior_period_release": release,
                "cash_opex_excl_rent": opex,
                "property_rent": rent,
                "one_off_costs": one_off,
                "mgmt_addback_proposed": proposed,
                "reported_ebitda": reported,
            }
        )
        policy_rows.append(
            {
                "entity_code": "madeup_north",
                "period_end": period,
                "proposed_addback_functional": proposed,
                "ic_approved_addback_functional": approved,
                "rejected_addback_functional": rejected,
            }
        )

        gross = q2(WEST_GROSS_BASE * season * growth)
        west_rebate = q2(gross * WEST_REBATE_RATE)
        billed_not = q2(gross * WEST_BILLED_NOT_REC_RATE)
        unbilled = q2(gross * WEST_UNBILLED_RATE)
        recognized = gross - west_rebate - billed_not + unbilled
        west_one_off = WEST_ONE_OFFS.get(period, Decimal("0.00"))
        core = q2(recognized * WEST_CORE_RATE)
        cash = core + west_one_off + intercompany_usd
        booked = q2(WEST_MGMT_BASE * season)
        accepted = q2(booked * WEST_APPROVAL_RATE)
        west_rejected = booked - accepted
        local_ebitda = recognized - cash + west_one_off + booked
        west_rows.append(
            {
                "MonthEnding": period,
                "RecognizedNetSales": recognized,
                "GrossBillings": gross,
                "RebateContra": west_rebate,
                "BilledNotRecognized": billed_not,
                "UnbilledRecognised": unbilled,
                "CashOperatingCost": cash,
                "IntercompanyPurchasesUsd": intercompany_usd,
                "OneOffCharges": west_one_off,
                "MgmtAddbackBooked": booked,
                "LeaseDepreciation": WEST_LEASE_DEPRECIATION,
                "LeaseInterest": WEST_LEASE_INTEREST,
                "EbitdaAfterAddbacks": local_ebitda,
            }
        )
        policy_rows.append(
            {
                "entity_code": "madeup_west",
                "period_end": period,
                "proposed_addback_functional": booked,
                "ic_approved_addback_functional": accepted,
                "rejected_addback_functional": west_rejected,
            }
        )
    return {
        "north": north_rows,
        "west": west_rows,
        "policy": policy_rows,
        "rates": rates,
    }


def generate_portfolio(out_dir: Path, definitions: dict | None = None) -> dict[str, Path]:
    definitions = definitions or load_definitions()
    scenario = build_scenario(definitions)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "north": out_dir / definitions["entities"]["madeup_north"]["file_name"],
        "west": out_dir / definitions["entities"]["madeup_west"]["file_name"],
        "holdco": out_dir / definitions["reference"]["file_name"],
    }
    for path in paths.values():
        if path.exists():
            path.unlink()
    _write_north(paths["north"], scenario["north"], definitions)
    _write_west(paths["west"], scenario["west"], definitions)
    _write_holdco(paths["holdco"], scenario, definitions)
    return paths


def _write_north(path: Path, rows: list[dict], definitions: dict) -> None:
    entity = definitions["entities"]["madeup_north"]
    connection = duckdb.connect(str(path))
    connection.execute("CREATE SCHEMA ledger")
    connection.execute(
        """
        CREATE TABLE ledger.about (
            legal_name VARCHAR,
            fiction_notice VARCHAR,
            functional_currency VARCHAR,
            local_revenue_definition VARCHAR,
            local_ebitda_definition VARCHAR
        )
        """
    )
    connection.execute(
        "INSERT INTO ledger.about VALUES (?, ?, ?, ?, ?)",
        [
            entity["legal_name"],
            FICTION,
            entity["functional_currency"],
            " ".join(entity["local_revenue"].split()),
            " ".join(entity["local_ebitda"].split()),
        ],
    )
    connection.execute(
        """
        CREATE TABLE ledger.monthly_pack (
            period_end DATE PRIMARY KEY,
            third_party_billed_gross DECIMAL(18,2) NOT NULL,
            intercompany_billed_gross DECIMAL(18,2) NOT NULL,
            billed_turnover_gross DECIMAL(18,2) NOT NULL,
            rebate_accrual DECIMAL(18,2) NOT NULL,
            deferred_unearned DECIMAL(18,2) NOT NULL,
            prior_period_release DECIMAL(18,2) NOT NULL,
            cash_opex_excl_rent DECIMAL(18,2) NOT NULL,
            property_rent DECIMAL(18,2) NOT NULL,
            one_off_costs DECIMAL(18,2) NOT NULL,
            mgmt_addback_proposed DECIMAL(18,2) NOT NULL,
            reported_ebitda DECIMAL(18,2) NOT NULL
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO ledger.monthly_pack VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [
                row["period_end"],
                row["third_party_billed_gross"],
                row["intercompany_billed_gross"],
                row["billed_turnover_gross"],
                row["rebate_accrual"],
                row["deferred_unearned"],
                row["prior_period_release"],
                row["cash_opex_excl_rent"],
                row["property_rent"],
                row["one_off_costs"],
                row["mgmt_addback_proposed"],
                row["reported_ebitda"],
            ]
            for row in rows
        ],
    )
    connection.execute(
        """
        CREATE VIEW ledger.v_management_headline AS
        SELECT period_end,
               billed_turnover_gross AS turnover,
               reported_ebitda AS ebitda
        FROM ledger.monthly_pack
        """
    )
    connection.close()


def _write_west(path: Path, rows: list[dict], definitions: dict) -> None:
    entity = definitions["entities"]["madeup_west"]
    connection = duckdb.connect(str(path))
    connection.execute("CREATE SCHEMA ops")
    connection.execute(
        """
        CREATE TABLE ops.About (
            LegalName VARCHAR,
            FictionNotice VARCHAR,
            FunctionalCurrency VARCHAR,
            LocalRevenueDefinition VARCHAR,
            LocalEbitdaDefinition VARCHAR
        )
        """
    )
    connection.execute(
        "INSERT INTO ops.About VALUES (?, ?, ?, ?, ?)",
        [
            entity["legal_name"],
            FICTION,
            entity["functional_currency"],
            " ".join(entity["local_revenue"].split()),
            " ".join(entity["local_ebitda"].split()),
        ],
    )
    connection.execute(
        """
        CREATE TABLE ops.MonthlyPnl (
            MonthEnding DATE PRIMARY KEY,
            RecognizedNetSales DECIMAL(18,2) NOT NULL,
            GrossBillings DECIMAL(18,2) NOT NULL,
            RebateContra DECIMAL(18,2) NOT NULL,
            BilledNotRecognized DECIMAL(18,2) NOT NULL,
            UnbilledRecognised DECIMAL(18,2) NOT NULL,
            CashOperatingCost DECIMAL(18,2) NOT NULL,
            IntercompanyPurchasesUsd DECIMAL(18,2) NOT NULL,
            OneOffCharges DECIMAL(18,2) NOT NULL,
            MgmtAddbackBooked DECIMAL(18,2) NOT NULL,
            LeaseDepreciation DECIMAL(18,2) NOT NULL,
            LeaseInterest DECIMAL(18,2) NOT NULL,
            EbitdaAfterAddbacks DECIMAL(18,2) NOT NULL
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO ops.MonthlyPnl VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            [
                row["MonthEnding"],
                row["RecognizedNetSales"],
                row["GrossBillings"],
                row["RebateContra"],
                row["BilledNotRecognized"],
                row["UnbilledRecognised"],
                row["CashOperatingCost"],
                row["IntercompanyPurchasesUsd"],
                row["OneOffCharges"],
                row["MgmtAddbackBooked"],
                row["LeaseDepreciation"],
                row["LeaseInterest"],
                row["EbitdaAfterAddbacks"],
            ]
            for row in rows
        ],
    )
    connection.execute(
        """
        CREATE VIEW ops.v_BoardPack AS
        SELECT MonthEnding, GrossBillings, EbitdaAfterAddbacks
        FROM ops.MonthlyPnl
        """
    )
    connection.close()


def _write_holdco(path: Path, scenario: dict, definitions: dict) -> None:
    connection = duckdb.connect(str(path))
    connection.execute("CREATE SCHEMA ref")
    connection.execute(
        """
        CREATE TABLE ref.about (
            legal_name VARCHAR,
            fiction_notice VARCHAR,
            reporting_currency VARCHAR
        )
        """
    )
    connection.execute(
        "INSERT INTO ref.about VALUES (?, ?, ?)",
        [
            definitions["fund"]["legal_name"],
            FICTION,
            definitions["fund"]["reporting_currency"],
        ],
    )
    connection.execute(
        """
        CREATE TABLE ref.entity (
            entity_code VARCHAR PRIMARY KEY,
            legal_name VARCHAR NOT NULL,
            functional_currency VARCHAR NOT NULL,
            country VARCHAR NOT NULL,
            ownership_note VARCHAR NOT NULL
        )
        """
    )
    for code, entity in definitions["entities"].items():
        connection.execute(
            "INSERT INTO ref.entity VALUES (?, ?, ?, ?, ?)",
            [
                code,
                entity["legal_name"],
                entity["functional_currency"],
                entity["country"],
                definitions["ownership"]["share"],
            ],
        )
    connection.execute(
        """
        CREATE TABLE ref.fx_monthly (
            period_end DATE PRIMARY KEY,
            gbp_per_usd DECIMAL(18,6) NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO ref.fx_monthly VALUES (?, ?)",
        [[row["period_end"], row["gbp_per_usd"]] for row in scenario["rates"]],
    )
    connection.execute(
        """
        CREATE TABLE ref.policy_adjustment (
            entity_code VARCHAR NOT NULL,
            period_end DATE NOT NULL,
            proposed_addback_functional DECIMAL(18,2) NOT NULL,
            ic_approved_addback_functional DECIMAL(18,2) NOT NULL,
            rejected_addback_functional DECIMAL(18,2) NOT NULL,
            PRIMARY KEY (entity_code, period_end)
        )
        """
    )
    connection.executemany(
        "INSERT INTO ref.policy_adjustment VALUES (?, ?, ?, ?, ?)",
        [
            [
                row["entity_code"],
                row["period_end"],
                row["proposed_addback_functional"],
                row["ic_approved_addback_functional"],
                row["rejected_addback_functional"],
            ]
            for row in scenario["policy"]
        ],
    )
    connection.close()
