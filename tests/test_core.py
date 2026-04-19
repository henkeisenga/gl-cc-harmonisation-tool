from pathlib import Path
import logging

import pandas as pd
import pytest

from config import Config
from core import (
    _canonical_description_key,
    _normalize_text,
    cc_actions,
    cc_number_mismatches,
    filter_gl,
    gl_actions,
    gl_number_mismatches,
    gl_presence,
    gl_type_mismatches,
    run_pipeline,
)


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.base_output_dir = str(tmp_path / "out")
    c.base_log_dir = str(tmp_path / "logs")
    c.output_dir = c.base_output_dir
    c.log_dir = c.base_log_dir
    c.dry_run = True
    c.verbose = False
    c.log_level = "INFO"
    return c


def _logger():
    return logging.getLogger("test")


def test_canonical_description_key_basic():
    s = pd.Series(["  Foo   Bar ", "foo bar", "FOO  BAR"])
    out = _canonical_description_key(s)
    assert out.nunique() == 1
    assert out.iloc[0] == "foo bar"


def test_normalize_text_keeps_missing_values():
    s = pd.Series(["  Foo   Bar ", None, pd.NA, ""])
    out = _normalize_text(s)

    assert out.iloc[0] == "Foo Bar"
    assert pd.isna(out.iloc[1])
    assert pd.isna(out.iloc[2])
    assert out.iloc[3] == ""


def test_gl_actions_create_vs_review(cfg):
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A"],
            "DESC_KEY": ["a", "a"],
            "ENV": ["L1-1", "L2-2"],
            "DATALIB": ["L1", "L2"],
            "COMPANY": ["1", "2"],
            "ACCOUNT": [1000, 1000],
            "TYPE": ["T", "T"],
            "DESCRIPTION_LANG": ["A long", "A long"],
        }
    )

    bin_mat = pd.DataFrame(
        {"L1-1": [1], "L2-2": [0]},
        index=pd.Index(["a"], name="DESC_KEY"),
    )

    actions = gl_actions(df[df["ENV"] == "L1-1"].copy(), bin_mat, _logger(), cfg)

    assert not actions.empty
    assert set(actions["ActionType"]).issubset({"Create", "Review"})
    assert "Action" in actions.columns


def test_cc_actions_basic(cfg):
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["X"],
            "DESC_KEY": ["x"],
            "ENV": ["L1-1"],
            "DATALIB": ["L1"],
            "COMPANY": ["1"],
            "COSTCENTER": [200],
            "DESCRIPTION_LANG": ["X long"],
        }
    )

    bin_mat = pd.DataFrame(
        {"L1-1": [1], "L2-2": [0]},
        index=pd.Index(["x"], name="DESC_KEY"),
    )

    actions = cc_actions(df.copy(), bin_mat, _logger(), cfg)

    assert not actions.empty
    assert "ActionType" in actions.columns
    assert "Action" in actions.columns


def test_gl_number_and_type_mismatches():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A", "B"],
            "DESC_KEY": ["a", "a", "b"],
            "ENV": ["E1", "E2", "E1"],
            "ACCOUNT": [100, 200, 300],
            "TYPE": ["T1", "T2", "T1"],
        }
    )

    nm = gl_number_mismatches(df)
    tm = gl_type_mismatches(df)

    assert not nm.empty
    assert not tm.empty


def test_gl_type_mismatches_ignores_stringified_missing_values():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A", "A", "B"],
            "DESC_KEY": ["a", "a", "a", "b"],
            "ENV": ["E1", "E2", "E3", "E1"],
            "ACCOUNT": [100, 100, 100, 200],
            "TYPE": ["T1", "nan", "None", ""],
        }
    )

    tm = gl_type_mismatches(df)

    assert tm.empty


def test_cc_number_mismatches():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A", "B"],
            "DESC_KEY": ["a", "a", "b"],
            "ENV": ["E1", "E2", "E1"],
            "COSTCENTER": [10, 20, 10],
        }
    )

    nm = cc_number_mismatches(df)
    assert not nm.empty


def test_filter_gl_applies_doortrek_verdrek_and_lastused_days(cfg):
    now = pd.Timestamp.now().normalize()

    df = pd.DataFrame(
        {
            "DATALIB": ["ERPOMGEVING", "ERPOMGEVING", "ERPOMGEVING", "ERPOMGEVING"],
            "COMPANY": ["1", "1", "1", "1"],
            "DESCRIPTION": ["KEEP", "DROP_DOORTREK", "DROP_VERDREK", "DROP_OLD"],
            "ACCOUNT": [1000, 1001, 1002, 1003],
            "TYPE": ["T", "T", "T", "T"],
            "DOORTREK_JN": ["N", "J", "N", "N"],
            "VERDREK_JN": ["N", "N", "J", "N"],
            "LASTUSED_DATE": [
                now - pd.Timedelta(days=5),
                now - pd.Timedelta(days=5),
                now - pd.Timedelta(days=5),
                now - pd.Timedelta(days=90),
            ],
        }
    )

    cfg.filter_fislib = ["ERPOMGEVING"]
    cfg.filter_companies = ["1"]
    cfg.filter_doortrek = True
    cfg.filter_verdrek = True
    cfg.lastused_days = 30

    cleaned, filtered, counts = filter_gl(df, _logger(), cfg)

    assert counts["Kept"] == 1
    assert counts["Removed"] == 3
    assert counts["Removed_DOORTREK"] == 1
    assert counts["Removed_VERDREK"] == 1
    assert cleaned["DESCRIPTION"].tolist() == ["KEEP"]
    assert set(filtered["DESCRIPTION"]) == {"DROP_DOORTREK", "DROP_VERDREK", "DROP_OLD"}


def test_gl_presence_builds_expected_binary_and_enriched_matrix(cfg):
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["Revenue", "Revenue", "Cost"],
            "DESC_KEY": ["revenue", "revenue", "cost"],
            "ENV": ["ERPOMGEVING-9", "ERPOMGEVING-15", "ERPOMGEVING-15"],
            "ACCOUNT": [8000, 8000, 4000],
            "TYPE": ["W", "W", "K"],
        }
    )

    bin_mat, enr = gl_presence(df, _logger(), cfg)

    assert bin_mat.loc["revenue", "ERPOMGEVING-9"] == 1
    assert bin_mat.loc["revenue", "ERPOMGEVING-15"] == 1
    assert bin_mat.loc["cost", "ERPOMGEVING-15"] == 1
    assert bin_mat.loc["cost", "ERPOMGEVING-9"] == 0

    assert enr.loc["revenue", "Count_Present"] == 2
    assert enr.loc["revenue", "Pct_Present"] == 100.0
    assert enr.loc["cost", "Count_Present"] == 1
    assert enr.loc["cost", "Pct_Present"] == 50.0

    assert enr.loc["revenue", "Acct_ERPOMGEVING-9"] == 8000
    assert enr.loc["revenue", "Type_ERPOMGEVING-15"] == "W"
    assert enr.loc["cost", "Acct_ERPOMGEVING-15"] == 4000
    assert pd.isna(enr.loc["cost", "Acct_ERPOMGEVING-9"])


def test_run_pipeline_gl_dry_run_with_mock_loader(cfg):
    out_dir = Path(cfg.output_dir)
    log_dir = Path(cfg.log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    cfg.output_dir = str(out_dir)
    cfg.log_dir = str(log_dir)
    cfg.dry_run = True
    cfg.verbose = False
    cfg.log_level = "INFO"
    cfg.filter_fislib = ["ERPOMGEVING"]
    cfg.filter_companies = ["1", "2"]
    cfg.filter_doortrek = True
    cfg.filter_verdrek = True
    cfg.lastused_days = None
    cfg.output_file_gl = "GL_output_test"

    def fake_loader(logger, cfg):
        return pd.DataFrame(
            {
                "DATALIB": ["ERPOMGEVING", "ERPOMGEVING", "ERPOMGEVING"],
                "COMPANY": ["1", "2", "1"],
                "DESCRIPTION": ["Revenue", "Revenue", "Cost"],
                "DESC_KEY": ["revenue", "revenue", "cost"],
                "DESCRIPTION_LANG": ["Revenue long", "Revenue long", "Cost long"],
                "ACCOUNT": [8000, 8000, 4000],
                "TYPE": ["W", "W", "K"],
                "DOORTREK_JN": ["N", "N", "N"],
                "VERDREK_JN": ["N", "N", "N"],
                "LASTUSED_DATE": [
                    pd.Timestamp.now().normalize(),
                    pd.Timestamp.now().normalize(),
                    pd.Timestamp.now().normalize(),
                ],
                "ENV": ["ERPOMGEVING-9", "ERPOMGEVING-15", "ERPOMGEVING-9"],
            }
        )

    run_pipeline(
        component="GL",
        loader=fake_loader,
        filter_func=filter_gl,
        presence_func=gl_presence,
        action_func=gl_actions,
        num_mismatch_func=gl_number_mismatches,
        type_mismatch_func=gl_type_mismatches,
        log_filename="test_pipeline.log",
        cfg=cfg,
    )

    assert not list(out_dir.glob("*.xlsx"))
    assert (log_dir / "test_pipeline.log").exists()


def test_filter_gl_empty_fislib_means_no_datalib_filter(cfg):
    now = pd.Timestamp.now().normalize()

    df = pd.DataFrame(
        {
            "DATALIB": ["ERPOMGEVING", "OTHERLIB"],
            "COMPANY": ["1", "1"],
            "DESCRIPTION": ["KEEP_A", "KEEP_B"],
            "ACCOUNT": [1000, 1001],
            "TYPE": ["T", "T"],
            "DOORTREK_JN": ["N", "N"],
            "VERDREK_JN": ["N", "N"],
            "LASTUSED_DATE": [now, now],
        }
    )

    cfg.filter_fislib = []
    cfg.filter_companies = ["1"]
    cfg.filter_doortrek = True
    cfg.filter_verdrek = True
    cfg.lastused_days = None

    cleaned, filtered, counts = filter_gl(df, _logger(), cfg)

    assert counts["Kept"] == 2
    assert counts["Removed"] == 0
    assert set(cleaned["DESCRIPTION"]) == {"KEEP_A", "KEEP_B"}
    assert filtered.empty


def test_filter_gl_empty_companies_means_no_company_filter(cfg):
    now = pd.Timestamp.now().normalize()

    df = pd.DataFrame(
        {
            "DATALIB": ["ERPOMGEVING", "ERPOMGEVING"],
            "COMPANY": ["1", "2"],
            "DESCRIPTION": ["KEEP_A", "KEEP_B"],
            "ACCOUNT": [1000, 1001],
            "TYPE": ["T", "T"],
            "DOORTREK_JN": ["N", "N"],
            "VERDREK_JN": ["N", "N"],
            "LASTUSED_DATE": [now, now],
        }
    )

    cfg.filter_fislib = ["ERPOMGEVING"]
    cfg.filter_companies = []
    cfg.filter_doortrek = True
    cfg.filter_verdrek = True
    cfg.lastused_days = None

    cleaned, filtered, counts = filter_gl(df, _logger(), cfg)

    assert counts["Kept"] == 2
    assert counts["Removed"] == 0
    assert set(cleaned["DESCRIPTION"]) == {"KEEP_A", "KEEP_B"}
    assert filtered.empty
