"""Configuration module for the Universal Ledger/Cost Center pipeline.

Adds:
- Pydantic validation for configuration (YAML + CLI).
- Dataclass remains the runtime config store (for minimal code churn).
- Priority: CLI > YAML > defaults.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigModel(BaseModel):
    """Strict config validator using Pydantic."""

    input_file_gl: str = Field(default="General_ledger_masterdata.xlsx")
    input_file_cc: str = Field(default="Costcenter_masterdata.xlsx")

    sheet_gl: str = Field(default="Accounts", min_length=1)
    sheet_cc: str = Field(default="CostCenters", min_length=1)

    base_output_dir: str = Field(default="output", min_length=1)
    base_log_dir: str = Field(default="logs", min_length=1)

    output_dir: str = Field(default="")
    log_dir: str = Field(default="")

    output_file_gl: str = Field(default="GL_output", min_length=1)
    output_file_cc: str = Field(default="CC_output", min_length=1)

    log_file_gl: str = Field(default="gl_log.txt", min_length=1)
    log_file_cc: str = Field(default="cc_log.txt", min_length=1)
    log_level: str = Field(default="INFO")

    filter_fislib: list[str] = Field(default_factory=list)
    filter_companies: list[str] = Field(default_factory=list)
    filter_doortrek: bool = True
    filter_verdrek: bool = True
    lastused_days: int | None = Field(default=None, ge=1)

    excel_engine: str = Field(default="xlsxwriter")
    dry_run: bool = False
    verbose: bool = False

    sheet_names_gl: dict[str, str] = Field(
        default_factory=lambda: {
            "Summary": "GL_RunSummary",
            "Cleaned Data": "GL_Cleaned",
            "Filtered Out": "GL_Filtered",
            "Filter Summary": "GL_Summary",
            "Presence Matrix (binary)": "GL_PresenceBin",
            "Presence Matrix (enriched)": "GL_Presence",
            "Create": "GL_Create",
            "Review": "GL_Review",
            "Number Mismatches": "GL_NumMismatch",
            "Type Mismatches": "GL_TypeMismatch",
            "Config": "GL_Config",
        }
    )
    sheet_names_cc: dict[str, str] = Field(
        default_factory=lambda: {
            "Summary": "CC_RunSummary",
            "Cleaned Data": "CC_Cleaned",
            "Filtered Out": "CC_Filtered",
            "Filter Summary": "CC_Summary",
            "Presence Matrix (binary)": "CC_PresenceBin",
            "Presence Matrix (enriched)": "CC_Presence",
            "Create": "CC_Create",
            "Review": "CC_Review",
            "Number Mismatches": "CC_NumMismatch",
            "Type Mismatches": "CC_TypeMismatch",
            "Config": "CC_Config",
        }
    )

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        lv = v.upper()
        if lv not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return lv

    @field_validator("excel_engine")
    @classmethod
    def _check_engine(cls, v: str) -> str:
        allowed = {"xlsxwriter", "openpyxl"}
        if v not in allowed:
            raise ValueError(f"excel_engine must be one of {sorted(allowed)}")
        return v

    @field_validator("filter_fislib", "filter_companies")
    @classmethod
    def _list_of_str(cls, v: list[str]) -> list[str]:
        return [str(x) for x in v]


@dataclass
class Config:
    input_file_gl: str = "General_ledger_masterdata.xlsx"
    input_file_cc: str = "Costcenter_masterdata.xlsx"

    sheet_gl: str = "Accounts"
    sheet_cc: str = "CostCenters"

    base_output_dir: str = "output"
    base_log_dir: str = "logs"

    output_dir: str = ""
    log_dir: str = ""

    output_file_gl: str = "GL_output"
    output_file_cc: str = "CC_output"

    log_file_gl: str = "gl_log.txt"
    log_file_cc: str = "cc_log.txt"
    log_level: str = "INFO"

    filter_fislib: list[str] = field(default_factory=list)
    filter_companies: list[str] = field(default_factory=list)
    filter_doortrek: bool = True
    filter_verdrek: bool = True
    lastused_days: int | None = None

    excel_engine: str = "xlsxwriter"
    dry_run: bool = False
    verbose: bool = False

    sheet_names_gl: dict[str, str] = field(
        default_factory=lambda: {
            "Summary": "GL_RunSummary",
            "Cleaned Data": "GL_Cleaned",
            "Filtered Out": "GL_Filtered",
            "Filter Summary": "GL_Summary",
            "Presence Matrix (binary)": "GL_PresenceBin",
            "Presence Matrix (enriched)": "GL_Presence",
            "Create": "GL_Create",
            "Review": "GL_Review",
            "Number Mismatches": "GL_NumMismatch",
            "Type Mismatches": "GL_TypeMismatch",
            "Config": "GL_Config",
        }
    )
    sheet_names_cc: dict[str, str] = field(
        default_factory=lambda: {
            "Summary": "CC_RunSummary",
            "Cleaned Data": "CC_Cleaned",
            "Filtered Out": "CC_Filtered",
            "Filter Summary": "CC_Summary",
            "Presence Matrix (binary)": "CC_PresenceBin",
            "Presence Matrix (enriched)": "CC_Presence",
            "Create": "CC_Create",
            "Review": "CC_Review",
            "Number Mismatches": "CC_NumMismatch",
            "Type Mismatches": "CC_TypeMismatch",
            "Config": "CC_Config",
        }
    )

    def init_dirs(self, timestamp: str | None = None) -> None:
        ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(self.base_output_dir, ts)
        self.log_dir = os.path.join(self.base_log_dir, ts)
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

    def update_from_mapping(self, m: dict[str, Any]) -> None:
        """Apply overrides via Pydantic validation (safe & typed)."""
        if not m:
            return
        merged = {**asdict(self), **m}
        try:
            validated = ConfigModel.model_validate(merged)
        except ValidationError as exc:
            raise ValueError(f"Invalid configuration: {exc}") from exc

        for name, value in validated.model_dump().items():
            setattr(self, name, value)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_config(
    initial: dict[str, Any] | None = None, *, init_runtime_dirs: bool = False
) -> Config:
    cfg = Config()
    if initial:
        cfg.update_from_mapping(initial)
    if init_runtime_dirs:
        cfg.init_dirs()
    return cfg


# Default config object without runtime side effects on import.
config = create_config(init_runtime_dirs=False)


def load_yaml_config(
    path: str | None, base_config: Config | None = None
) -> dict[str, Any]:
    """Load YAML configuration file into a dict ({} if no path)."""
    if not path:
        return {}
    if yaml is None:
        raise RuntimeError(
            "PyYAML is niet geïnstalleerd, maar --config werd opgegeven."
        )
    if not os.path.exists(path):
        raise FileNotFoundError(f"Configbestand niet gevonden: {path}")

    runtime_cfg = base_config if base_config is not None else Config()

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML-config moet een mapping (dict) zijn.")
        try:
            merged = {**runtime_cfg.as_dict(), **data}
            ConfigModel.model_validate(merged)
        except ValidationError as exc:
            raise ValueError(f"YAML-config ongeldig: {exc}") from exc
        return data
