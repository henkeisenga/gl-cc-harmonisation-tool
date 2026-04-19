"""CLI entrypoint with YAML + Pydantic config validation + Merge planner."""

from __future__ import annotations

import argparse
import sys

import yaml
from pydantic import ValidationError
from config import Config, create_config, load_yaml_config
from core import (
    run_pipeline,
    load_gl,
    filter_gl,
    gl_presence,
    gl_actions,
    gl_number_mismatches,
    gl_type_mismatches,
    load_cc,
    filter_cc,
    cc_presence,
    cc_actions,
    cc_number_mismatches,
    cc_type_mismatches,
)
from merge import plan_merge_from_cleaned


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Universal Ledger/Cost Center Pipeline")
    p.add_argument(
        "--config", type=str, help="Pad naar YAML-configbestand", default=None
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Voer alles uit behalve het schrijven van Excel-exports",
    )
    p.add_argument("--verbose", action="store_true", help="Meer logdetails (DEBUG)")
    p.add_argument(
        "--lastused-days",
        type=int,
        default=None,
        help="Filter op laatst-gebruikt binnen X dagen",
    )
    p.add_argument(
        "--fislib",
        nargs="*",
        default=None,
        help="Whitelist DATALIB waarden (override config/YAML)",
    )
    p.add_argument(
        "--companies",
        nargs="*",
        default=None,
        help="Whitelist BEDRIJF waarden (override config/YAML)",
    )

    p.add_argument("--input-file-gl", type=str, default=None)
    p.add_argument("--input-file-cc", type=str, default=None)
    p.add_argument("--sheet-gl", type=str, default=None)
    p.add_argument("--sheet-cc", type=str, default=None)

    p.add_argument(
        "--merge-spec",
        type=str,
        default=None,
        help="Pad naar YAML met merge-spec (sources/target/scope/numbering_strategy)",
    )

    return p.parse_args()


def apply_yaml_and_cli(args: argparse.Namespace, cfg: Config) -> None:
    yaml_map = load_yaml_config(args.config, cfg)
    if yaml_map:
        cfg.update_from_mapping(yaml_map)

    cli_map = {}

    if args.dry_run:
        cli_map["dry_run"] = True
    if args.verbose:
        cli_map["verbose"] = True
        cli_map["log_level"] = "DEBUG"
    if args.lastused_days is not None:
        cli_map["lastused_days"] = args.lastused_days

    if args.fislib is not None:
        cli_map["filter_fislib"] = [str(x) for x in args.fislib]
    if args.companies is not None:
        cli_map["filter_companies"] = [str(x) for x in args.companies]

    if args.input_file_gl is not None:
        cli_map["input_file_gl"] = args.input_file_gl
    if args.input_file_cc is not None:
        cli_map["input_file_cc"] = args.input_file_cc
    if args.sheet_gl is not None:
        cli_map["sheet_gl"] = args.sheet_gl
    if args.sheet_cc is not None:
        cli_map["sheet_cc"] = args.sheet_cc

    if cli_map:
        cfg.update_from_mapping(cli_map)


def main() -> None:
    args = parse_args()
    cfg = create_config(init_runtime_dirs=False)
    try:
        apply_yaml_and_cli(args, cfg)
        cfg.init_dirs()
    except (ValueError, FileNotFoundError, ValidationError) as e:
        print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    gl_result = run_pipeline(
        component="GL",
        loader=load_gl,
        filter_func=filter_gl,
        presence_func=gl_presence,
        action_func=gl_actions,
        num_mismatch_func=gl_number_mismatches,
        type_mismatch_func=gl_type_mismatches,
        log_filename=cfg.log_file_gl,
        cfg=cfg,
    )

    cc_result = run_pipeline(
        component="CC",
        loader=load_cc,
        filter_func=filter_cc,
        presence_func=cc_presence,
        action_func=cc_actions,
        num_mismatch_func=cc_number_mismatches,
        type_mismatch_func=cc_type_mismatches,
        log_filename=cfg.log_file_cc,
        cfg=cfg,
    )

    if args.merge_spec:
        try:
            with open(args.merge_spec, "r", encoding="utf-8") as f:
                merge_spec = yaml.safe_load(f) or {}
            if not isinstance(merge_spec, dict):
                raise ValueError("merge-spec moet een mapping/dict zijn")

            out_path = plan_merge_from_cleaned(
                gl_result["cleaned"],
                cc_result["cleaned"],
                merge_spec,
                cfg=cfg,
            )
            print(f"[MERGE PLAN] Written: {out_path}")
        except (ValueError, FileNotFoundError, ValidationError, yaml.YAMLError) as e:
            print(f"[MERGE ERROR] {e}", file=sys.stderr)
            sys.exit(3)


if __name__ == "__main__":
    main()
