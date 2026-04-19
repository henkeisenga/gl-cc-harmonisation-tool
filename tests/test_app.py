from argparse import Namespace

from app import apply_yaml_and_cli
from config import Config


def test_apply_cli_overrides_basic():
    cfg = Config()
    args = Namespace(
        config=None,
        dry_run=True,
        verbose=True,
        lastused_days=30,
        fislib=["X"],
        companies=["1"],
        input_file=None,
        input_file_gl=None,
        input_file_cc=None,
        sheet_gl=None,
        sheet_cc=None,
        merge_spec=None,
    )

    apply_yaml_and_cli(args, cfg)

    assert cfg.dry_run is True
    assert cfg.verbose is True
    assert cfg.log_level == "DEBUG"
    assert cfg.lastused_days == 30
    assert cfg.filter_fislib == ["X"]
    assert cfg.filter_companies == ["1"]


def test_apply_yaml_invalid_path_raises():
    cfg = Config()
    args = Namespace(
        config="does_not_exist.yaml",
        dry_run=False,
        verbose=False,
        lastused_days=None,
        fislib=None,
        companies=None,
        input_file=None,
        input_file_gl=None,
        input_file_cc=None,
        sheet_gl=None,
        sheet_cc=None,
        merge_spec=None,
    )

    try:
        apply_yaml_and_cli(args, cfg)
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass
