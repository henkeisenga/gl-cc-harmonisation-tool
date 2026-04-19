import pandas as pd

from merge import (
    MergeSpec,
    _build_mappings_cc,
    _build_mappings_gl,
    _build_unified_cc,
    _build_unified_gl,
)


def _spec(strategy="pick_majority"):
    return MergeSpec.model_validate(
        {
            "sources": [
                {"datalib": "L1", "bedrijf": "1"},
                {"datalib": "L2", "bedrijf": "2"},
            ],
            "target": {"datalib": "L3", "bedrijf": "3"},
            "scope": ["GL", "CC"],
            "numbering_strategy": strategy,
        }
    )


def test_unified_gl_pick_majority():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A", "A"],
            "DESC_KEY": ["a", "a", "a"],
            "ENV": ["L1-1", "L2-2", "L2-2"],
            "ACCOUNT": [100, 200, 200],
            "TYPE": ["T", "T", "T"],
            "DESCRIPTION_LANG": ["A", "A", "A"],
        }
    )

    uni, conf, act = _build_unified_gl(df, _spec("pick_majority"))

    assert not uni.empty
    assert not act.empty
    assert "ACCOUNT_CANONICAL" in uni.columns


def test_unified_cc_pick_majority():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A", "A"],
            "DESC_KEY": ["a", "a"],
            "ENV": ["L1-1", "L2-2"],
            "COSTCENTER": [10, 10],
            "DESCRIPTION_LANG": ["A", "A"],
        }
    )

    uni, conf, act = _build_unified_cc(df, _spec())

    assert not uni.empty
    assert not act.empty
    assert "COSTCENTER_CANONICAL" in uni.columns


def test_mappings_gl_columns():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A"],
            "DESC_KEY": ["a"],
            "ENV": ["L1-1"],
            "DATALIB": ["L1"],
            "COMPANY": ["1"],
            "ACCOUNT": [100],
            "TYPE": ["T"],
            "DESCRIPTION_LANG": ["A"],
        }
    )

    uni, _, _ = _build_unified_gl(df, _spec())
    mp = _build_mappings_gl(df, uni, _spec())

    assert "Source_ENV" in mp.columns
    assert "Suggested_Action" in mp.columns


def test_mappings_cc_columns():
    df = pd.DataFrame(
        {
            "DESCRIPTION": ["A"],
            "DESC_KEY": ["a"],
            "ENV": ["L1-1"],
            "DATALIB": ["L1"],
            "COMPANY": ["1"],
            "COSTCENTER": [10],
            "DESCRIPTION_LANG": ["A"],
        }
    )

    uni, _, _ = _build_unified_cc(df, _spec())
    mp = _build_mappings_cc(df, uni, _spec())

    assert "Source_ENV" in mp.columns
    assert "Suggested_Action" in mp.columns
