"""Core logic for the Universal Ledger/Cost Center pipeline with schema validation.

Adds:
- Pandera DataFrame validation for GL/CC inputs after normalization.
- Vectorized actions (no row loops), presence matrices, export, and runner.
- Summary logging in console + Summary sheet in Excel export.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Tuple

import pandas as pd

# --- Prefer the pandas-backend import; fallback to legacy import if unavailable ---
try:
    import pandera.pandas as pa  # recommended
    from pandera.pandas import Column, Check
except Exception:
    try:
        import pandera as pa  # type: ignore
        from pandera import Column, Check  # type: ignore
    except Exception:  # pragma: no cover

        class _DummySchemaErrors(Exception):
            pass

        class _DummyCheck:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

        class _DummyColumn:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

        class _DummyDataFrameSchema:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def validate(self, df, lazy: bool = True):
                return df

        class _DummyErrors:
            SchemaErrors = _DummySchemaErrors

        class _DummyPA:
            DataFrameSchema = _DummyDataFrameSchema
            errors = _DummyErrors()

        pa = _DummyPA()  # type: ignore
        Column = _DummyColumn  # type: ignore
        Check = _DummyCheck  # type: ignore

from config import Config

# ---- Type aliases ------------------------------------------------------------
DataFrame = pd.DataFrame
Series = pd.Series

FilterFunc = Callable[
    [DataFrame, logging.Logger, Config], Tuple[DataFrame, DataFrame, Dict[str, int]]
]
PresenceFunc = Callable[
    [DataFrame, logging.Logger, Config], Tuple[DataFrame, DataFrame]
]
ActionFunc = Callable[[DataFrame, DataFrame, logging.Logger, Config], DataFrame]
MismatchFunc = Callable[[DataFrame], DataFrame]

# ---------------------------- Pandera schemas --------------------------------
# Gebruik lambda-checks (geen str_length), om backend-dispatch issues te vermijden.
GL_SCHEMA = pa.DataFrameSchema(
    {
        "DATALIB": Column(
            object,
            nullable=False,
            checks=Check(lambda s: s.astype(str).str.strip().str.len() >= 1),
        ),
        "COMPANY": Column(object, nullable=False),
        "ACCOUNT": Column(object, nullable=True),
        "DESCRIPTION": Column(
            object,
            nullable=False,
            checks=Check(lambda s: s.astype(str).str.strip().str.len() >= 1),
        ),
        "TYPE": Column(object, nullable=True),
        "LASTUSED_DATE": Column(object, nullable=True),
        "DOORTREK_JN": Column(
            object,
            nullable=True,
            checks=Check(
                lambda s: s.isna() | s.astype(str).str.upper().isin({"J", "N"})
            ),
        ),
        "VERDREK_JN": Column(
            object,
            nullable=True,
            checks=Check(
                lambda s: s.isna() | s.astype(str).str.upper().isin({"J", "N"})
            ),
        ),
    },
    coerce=True,
    strict=False,
)

CC_SCHEMA = pa.DataFrameSchema(
    {
        "DATALIB": Column(
            object,
            nullable=False,
            checks=Check(lambda s: s.astype(str).str.strip().str.len() >= 1),
        ),
        "COMPANY": Column(object, nullable=False),
        "COSTCENTER": Column(object, nullable=True),
        "DESCRIPTION": Column(
            object,
            nullable=False,
            checks=Check(lambda s: s.astype(str).str.strip().str.len() >= 1),
        ),
        "LASTUSED_DATE": Column(object, nullable=True),
    },
    coerce=True,
    strict=False,
)


# ---------------------------- Logging ----------------------------------------
def _cfg(cfg: Optional[Config]) -> Config:
    if cfg is None:
        raise ValueError("cfg is verplicht")
    return cfg


def _new_logger(name: str, logfile: str, cfg: Config) -> logging.Logger:
    runtime_cfg = _cfg(cfg)
    level = getattr(logging, runtime_cfg.log_level.upper(), logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    path = os.path.join(runtime_cfg.log_dir, logfile)
    os.makedirs(runtime_cfg.log_dir, exist_ok=True)

    fh = logging.FileHandler(path, mode="w", encoding="utf-8")
    sh = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def log_step(logger: logging.Logger, msg: str, cfg: Config) -> None:
    if cfg.verbose:
        logger.debug(f"VERBOSE: {msg}")
    logger.info(msg)


# ---------------------------- Utilities --------------------------------------
def _normalize_columns(df: DataFrame) -> DataFrame:
    out = df.copy()
    out.columns = out.columns.astype(str).str.strip().str.upper()
    return out


def _normalize_text(s: Series) -> Series:
    out = s.astype("string")
    out = out.str.replace(r"\s+", " ", regex=True).str.strip()
    return out


def _canonical_description_key(series: Series) -> Series:
    def _canon(value: object) -> str:
        text = "" if pd.isna(value) else str(value)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.casefold()
        text = re.sub(r"[^0-9a-z]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    return series.map(_canon)


def _env_col_from(df: DataFrame, lib_col: str, comp_col: str) -> Series:
    return (
        df[lib_col].astype(str).str.strip() + "-" + df[comp_col].astype(str).str.strip()
    )


def _autofit_xlsx(writer, sheet_name: str, df: DataFrame) -> None:
    sheet = writer.sheets[sheet_name]
    rows, cols = df.shape
    if cols == 0:
        return
    sheet.autofilter(0, 0, rows, max(cols - 1, 0))
    for idx, col in enumerate(df.columns):
        try:
            max_len = (
                int(max(df[col].astype(str).map(len).max(), len(str(col))) or 0) + 2
            )
        except Exception:
            max_len = len(str(col)) + 2
        sheet.set_column(idx, idx, min(max_len, 60))


def _as_int_or_str_no_decimal(val: object) -> object:
    if pd.isna(val):
        return None
    if isinstance(val, int):
        return val
    try:
        f = float(val)  # type: ignore[arg-type]
        if f.is_integer():
            return int(f)
    except Exception:
        pass
    s = str(val)
    if s.endswith(".0"):
        try:
            return int(float(s))
        except Exception:
            return s
    return s


# ---------------------------- Date parsing -----------------------------------
def _parse_lastused(series: Series) -> Series:
    if series is None:
        return pd.Series(pd.NaT, index=[])
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    s_str = series.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    mask_empty = (s_str == "") | s_str.str.lower().isin({"nan", "none", "nat", "null"})
    mask_digits = ~mask_empty & s_str.str.fullmatch(r"\d+").fillna(False)
    mask6 = mask_digits & (s_str.str.len() == 6)
    mask8 = mask_digits & (s_str.str.len() == 8)
    mask_serial = mask_digits & ~(mask6 | mask8)

    out.loc[mask6] = pd.to_datetime(s_str[mask6], format="%y%m%d", errors="coerce")
    out.loc[mask8] = pd.to_datetime(s_str[mask8], format="%Y%m%d", errors="coerce")
    if mask_serial.any():
        nums = pd.to_numeric(s_str[mask_serial], errors="coerce")
        valid = nums.between(1, 60000, inclusive="both")
        out.loc[nums.index[valid]] = pd.to_datetime(
            nums[valid], unit="D", origin="1899-12-30", errors="coerce"
        )
    mask_other = ~mask_empty & ~(mask6 | mask8 | mask_serial)
    out.loc[mask_other] = pd.to_datetime(s_str[mask_other], errors="coerce")
    return out


# ---------------------------- Loaders (with Pandera) -------------------------
def _resolve_path_for(component: str, cfg: Config) -> str:
    runtime_cfg = _cfg(cfg)
    if component == "GL":
        return runtime_cfg.input_file_gl
    if component == "CC":
        return runtime_cfg.input_file_cc
    raise ValueError(f"Unknown component: {component}")


def _add_description_keys(df: DataFrame) -> DataFrame:
    out = df.copy()
    out["DESC_KEY"] = _canonical_description_key(out["DESCRIPTION"])
    if "DESCRIPTION_LANG" in out.columns:
        out["DESCRIPTION_LANG"] = _normalize_text(out["DESCRIPTION_LANG"])
    else:
        out["DESCRIPTION_LANG"] = pd.NA
    return out


def load_gl(logger: logging.Logger, cfg: Config) -> DataFrame:
    runtime_cfg = _cfg(cfg)
    path = _resolve_path_for("GL", runtime_cfg)
    log_step(
        logger,
        f"Loading GL sheet '{runtime_cfg.sheet_gl}' from {path} ...",
        runtime_cfg,
    )
    df = pd.read_excel(path, sheet_name=runtime_cfg.sheet_gl)
    df = _normalize_columns(df)

    try:
        df = GL_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        msg = f"GL schema validation failed with {len(e.failure_cases)} errors. Top rows:\n{e.failure_cases.head(10)}"
        logger.error(msg)
        raise

    for c in ["DESCRIPTION", "TYPE", "DATALIB", "COMPANY", "DESCRIPTION_LANG"]:
        if c in df.columns:
            df[c] = _normalize_text(df[c])

    df = _add_description_keys(df)
    df["LASTUSED_DATE"] = _parse_lastused(df["LASTUSED_DATE"])
    df["ENV"] = _env_col_from(df, "DATALIB", "COMPANY")
    log_step(logger, f"Loaded {len(df):,} GL rows", runtime_cfg)
    return df


def load_cc(logger: logging.Logger, cfg: Config) -> DataFrame:
    runtime_cfg = _cfg(cfg)
    path = _resolve_path_for("CC", runtime_cfg)
    log_step(
        logger,
        f"Loading CC sheet '{runtime_cfg.sheet_cc}' from {path} ...",
        runtime_cfg,
    )
    df = pd.read_excel(path, sheet_name=runtime_cfg.sheet_cc)
    df = _normalize_columns(df)

    try:
        df = CC_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        msg = f"CC schema validation failed with {len(e.failure_cases)} errors. Top rows:\n{e.failure_cases.head(10)}"
        logger.error(msg)
        raise

    for c in ["DESCRIPTION", "DATALIB", "COMPANY", "DESCRIPTION_LANG"]:
        if c in df.columns:
            df[c] = _normalize_text(df[c])

    df = _add_description_keys(df)
    df["LASTUSED_DATE"] = _parse_lastused(df["LASTUSED_DATE"])
    df["ENV"] = _env_col_from(df, "DATALIB", "COMPANY")
    log_step(logger, f"Loaded {len(df):,} CC rows", runtime_cfg)
    return df


# ---------------------------- Filters ----------------------------------------
def _apply_common_filters(df: DataFrame, cfg: Config) -> Series:
    runtime_cfg = _cfg(cfg)

    if runtime_cfg.filter_fislib:
        fislib_keep = df["DATALIB"].isin(runtime_cfg.filter_fislib)
    else:
        fislib_keep = pd.Series(True, index=df.index)

    if runtime_cfg.filter_companies:
        company_keep = df["COMPANY"].astype(str).isin(runtime_cfg.filter_companies)
    else:
        company_keep = pd.Series(True, index=df.index)

    keep = (
        fislib_keep
        & company_keep
        & (~df["DESCRIPTION"].fillna("").str.lower().isin(["", "leeg"]))
    )

    if runtime_cfg.lastused_days is not None and "LASTUSED_DATE" in df.columns:
        cutoff = datetime.now() - timedelta(days=runtime_cfg.lastused_days)
        keep &= df["LASTUSED_DATE"].notna() & (df["LASTUSED_DATE"] >= cutoff)

    return keep


def filter_gl(
    df: DataFrame, logger: logging.Logger, cfg: Config
) -> Tuple[DataFrame, DataFrame, Dict[str, int]]:
    runtime_cfg = _cfg(cfg)
    log_step(logger, "Filtering GL data ...", runtime_cfg)
    m = pd.Series(True, index=df.index)
    if "DOORTREK_JN" in df.columns and runtime_cfg.filter_doortrek:
        m &= df["DOORTREK_JN"].astype(str).str.upper().ne("J")
    if "VERDREK_JN" in df.columns and runtime_cfg.filter_verdrek:
        m &= df["VERDREK_JN"].astype(str).str.upper().ne("J")
    keep = m & _apply_common_filters(df, runtime_cfg)
    cleaned = df[keep].reset_index(drop=True)
    filtered = df[~keep].reset_index(drop=True)
    counts = {
        "Removed": int((~keep).sum()),
        "Kept": int(keep.sum()),
        "Removed_DOORTREK": (
            int((df["DOORTREK_JN"].astype(str).str.upper() == "J").sum())
            if "DOORTREK_JN" in df.columns
            else 0
        ),
        "Removed_VERDREK": (
            int((df["VERDREK_JN"].astype(str).str.upper() == "J").sum())
            if "VERDREK_JN" in df.columns
            else 0
        ),
    }
    log_step(
        logger,
        f"GL filtered: removed {counts['Removed']:,} rows; kept {counts['Kept']:,}.",
        runtime_cfg,
    )
    return cleaned, filtered, counts


def filter_cc(
    df: DataFrame, logger: logging.Logger, cfg: Config
) -> Tuple[DataFrame, DataFrame, Dict[str, int]]:
    runtime_cfg = _cfg(cfg)
    log_step(logger, "Filtering CC data ...", runtime_cfg)
    keep = _apply_common_filters(df, runtime_cfg)
    cleaned = df[keep].reset_index(drop=True)
    filtered = df[~keep].reset_index(drop=True)
    counts = {"Removed": int((~keep).sum()), "Kept": int(keep.sum())}
    log_step(
        logger,
        f"CC filtered: removed {counts['Removed']:,} rows; kept {counts['Kept']:,}.",
        runtime_cfg,
    )
    return cleaned, filtered, counts


# ---------------------------- Presence ----------------------------------------
def gl_presence(
    df: DataFrame, logger: logging.Logger, cfg: Config
) -> Tuple[DataFrame, DataFrame]:
    log_step(logger, "Building GL presence matrices ...", cfg)
    display_descr = (
        df.groupby("DESC_KEY", as_index=True)["DESCRIPTION"]
        .first()
        .rename("DESCRIPTION")
    )
    bin_mat = pd.crosstab(df["DESC_KEY"], df["ENV"]).gt(0).astype(int)
    acct = df.pivot_table(
        index="DESC_KEY", columns="ENV", values="ACCOUNT", aggfunc="first"
    )
    typ = df.pivot_table(
        index="DESC_KEY", columns="ENV", values="TYPE", aggfunc="first"
    )
    enr = bin_mat.copy()
    enr = enr.join(display_descr, how="left")
    for env in bin_mat.columns:
        enr[f"Acct_{env}"] = acct.get(env)
        enr[f"Type_{env}"] = typ.get(env)
    total_envs = bin_mat.shape[1]
    enr["Count_Present"] = bin_mat.sum(axis=1)
    enr["Pct_Present"] = (enr["Count_Present"] / max(total_envs, 1) * 100).round(1)
    cols = ["DESCRIPTION"] + [c for c in enr.columns if c != "DESCRIPTION"]
    enr = enr[cols]
    log_step(
        logger,
        f"Processed {enr.shape[0]:,} GL descriptions across {total_envs} envs.",
        cfg,
    )
    return bin_mat, enr


def cc_presence(
    df: DataFrame, logger: logging.Logger, cfg: Config
) -> Tuple[DataFrame, DataFrame]:
    log_step(logger, "Building CC presence matrices ...", cfg)
    display_descr = (
        df.groupby("DESC_KEY", as_index=True)["DESCRIPTION"]
        .first()
        .rename("DESCRIPTION")
    )
    bin_mat = pd.crosstab(df["DESC_KEY"], df["ENV"]).gt(0).astype(int)
    cc_num = df.pivot_table(
        index="DESC_KEY", columns="ENV", values="COSTCENTER", aggfunc="first"
    )
    enr = bin_mat.copy()
    enr = enr.join(display_descr, how="left")
    total_envs = bin_mat.shape[1]
    enr["Count_Present"] = bin_mat.sum(axis=1)
    enr["Pct_Present"] = (enr["Count_Present"] / max(total_envs, 1) * 100).round(1)
    if "DESCRIPTION_LANG" in df.columns:
        long_desc = df.drop_duplicates(["DESC_KEY", "DESCRIPTION_LANG"]).set_index(
            "DESC_KEY"
        )["DESCRIPTION_LANG"]
        if not long_desc.empty:
            enr = enr.join(long_desc)
    for env in bin_mat.columns:
        enr[f"CC_{env}"] = cc_num.get(env)
    cols = ["DESCRIPTION"] + [c for c in enr.columns if c != "DESCRIPTION"]
    enr = enr[cols]
    log_step(
        logger,
        f"Processed {enr.shape[0]:,} CC descriptions across {total_envs} envs.",
        cfg,
    )
    return bin_mat, enr


# ---------------------------- Vectorized Actions ------------------------------
def gl_actions(
    df: DataFrame, bin_mat: DataFrame, logger: logging.Logger, cfg: Config
) -> DataFrame:
    log_step(logger, "Generating GL actions (vectorized) ...", cfg)
    pres = bin_mat.stack().reset_index()
    pres.columns = ["DESC_KEY", "ENV", "Present"]
    pres = pres[pres["Present"] == 0].drop(columns="Present")
    if pres.empty:
        return pd.DataFrame(
            columns=[
                "ActionType",
                "Reason",
                "ENV",
                "DATALIB",
                "COMPANY",
                "DESCRIPTION",
                "DESCRIPTION_LANG",
                "ACCOUNT",
                "TYPE",
                "Action",
            ]
        )

    fallback = (
        df.sort_values(["DESC_KEY", "ENV"])
        .groupby("DESC_KEY", as_index=False)
        .agg(
            {
                "DESCRIPTION": "first",
                "ACCOUNT": "first",
                "TYPE": "first",
                "DESCRIPTION_LANG": "first",
            }
        )
        .rename(
            columns={
                "ACCOUNT": "Fallback_ACCOUNT",
                "TYPE": "Fallback_TYPE",
                "DESCRIPTION_LANG": "Fallback_LANG",
            }
        )
    )

    out = pres.merge(fallback, on="DESC_KEY", how="left")
    out[["DATALIB", "COMPANY"]] = out["ENV"].str.split("-", n=1, expand=True)

    df_check = df[["ENV", "ACCOUNT", "DESC_KEY"]].dropna().drop_duplicates()
    tmp = out.merge(
        df_check,
        left_on=["ENV", "Fallback_ACCOUNT"],
        right_on=["ENV", "ACCOUNT"],
        how="left",
        suffixes=("", "_TARGET"),
    )
    same_desc = tmp["DESC_KEY_TARGET"].eq(tmp["DESC_KEY"])
    has_same_number = tmp["ACCOUNT"].notna()
    collision = has_same_number & ~same_desc.fillna(False)

    out["ActionType"] = "Create"
    out.loc[has_same_number, "ActionType"] = "Review"
    out["Reason"] = "Target mist nummer"
    out.loc[has_same_number & same_desc.fillna(False), "Reason"] = (
        "Nummer bestaat al in target voor dezelfde omschrijving"
    )
    out.loc[collision, "Reason"] = (
        "Nummer bestaat al in target voor andere omschrijving"
    )

    out["Action"] = (
        out["ActionType"]
        + " in "
        + out["ENV"]
        + " account with "
        + out["Fallback_ACCOUNT"].astype(str)
        + " and "
        + out["DESCRIPTION"].astype(str)
        + " and "
        + out["Fallback_LANG"].astype(str)
    )

    out = out.rename(
        columns={
            "Fallback_ACCOUNT": "ACCOUNT",
            "Fallback_TYPE": "TYPE",
            "Fallback_LANG": "DESCRIPTION_LANG",
        }
    )
    if "ACCOUNT" in out.columns:
        out["ACCOUNT"] = out["ACCOUNT"].map(_as_int_or_str_no_decimal)

    return out[
        [
            "ActionType",
            "Reason",
            "ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "DESCRIPTION_LANG",
            "ACCOUNT",
            "TYPE",
            "Action",
        ]
    ]


def cc_actions(
    df: DataFrame, bin_mat: DataFrame, logger: logging.Logger, cfg: Config
) -> DataFrame:
    log_step(logger, "Generating CC actions (vectorized) ...", cfg)
    pres = bin_mat.stack().reset_index()
    pres.columns = ["DESC_KEY", "ENV", "Present"]
    pres = pres[pres["Present"] == 0].drop(columns="Present")
    if pres.empty:
        return pd.DataFrame(
            columns=[
                "ActionType",
                "Reason",
                "ENV",
                "DATALIB",
                "COMPANY",
                "DESCRIPTION",
                "DESCRIPTION_LANG",
                "COSTCENTER",
                "Action",
            ]
        )

    fallback = (
        df.sort_values(["DESC_KEY", "ENV"])
        .groupby("DESC_KEY", as_index=False)
        .agg(
            {"DESCRIPTION": "first", "COSTCENTER": "first", "DESCRIPTION_LANG": "first"}
        )
        .rename(
            columns={
                "COSTCENTER": "Fallback_COSTCENTER",
                "DESCRIPTION_LANG": "Fallback_LANG",
            }
        )
    )

    out = pres.merge(fallback, on="DESC_KEY", how="left")
    out[["DATALIB", "COMPANY"]] = out["ENV"].str.split("-", n=1, expand=True)

    df_check = df[["ENV", "COSTCENTER", "DESC_KEY"]].dropna().drop_duplicates()
    tmp = out.merge(
        df_check,
        left_on=["ENV", "Fallback_COSTCENTER"],
        right_on=["ENV", "COSTCENTER"],
        how="left",
        suffixes=("", "_TARGET"),
    )
    same_desc = tmp["DESC_KEY_TARGET"].eq(tmp["DESC_KEY"])
    has_same_number = tmp["COSTCENTER"].notna()
    collision = has_same_number & ~same_desc.fillna(False)

    out["ActionType"] = "Create"
    out.loc[has_same_number, "ActionType"] = "Review"
    out["Reason"] = "Target mist nummer"
    out.loc[has_same_number & same_desc.fillna(False), "Reason"] = (
        "Nummer bestaat al in target voor dezelfde omschrijving"
    )
    out.loc[collision, "Reason"] = (
        "Nummer bestaat al in target voor andere omschrijving"
    )

    out["Action"] = (
        out["ActionType"]
        + " in "
        + out["ENV"]
        + " cost center with "
        + out["Fallback_COSTCENTER"].astype(str)
        + " and "
        + out["DESCRIPTION"].astype(str)
    )

    out = out.rename(
        columns={
            "Fallback_COSTCENTER": "COSTCENTER",
            "Fallback_LANG": "DESCRIPTION_LANG",
        }
    )
    if "COSTCENTER" in out.columns:
        out["COSTCENTER"] = out["COSTCENTER"].map(_as_int_or_str_no_decimal)

    return out[
        [
            "ActionType",
            "Reason",
            "ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "DESCRIPTION_LANG",
            "COSTCENTER",
            "Action",
        ]
    ]


# ---------------------------- Mismatches --------------------------------------
def gl_number_mismatches(df: DataFrame) -> DataFrame:
    if df.empty:
        return pd.DataFrame()
    details = (
        df.dropna(subset=["ACCOUNT"])
        .assign(
            ACCOUNT_FMT=lambda x: x["ACCOUNT"]
            .map(_as_int_or_str_no_decimal)
            .astype(str),
            DETAIL=lambda x: x["ACCOUNT"].map(_as_int_or_str_no_decimal).astype(str)
            + " ("
            + x["ENV"].astype(str)
            + ")",
        )
        .groupby("DESC_KEY", as_index=False)
        .agg(
            DESCRIPTION=("DESCRIPTION", "first"),
            Unique_Numbers=("ACCOUNT_FMT", "nunique"),
            Details=("DETAIL", lambda s: ", ".join(pd.unique(s))),
        )
    )
    return details.loc[
        details["Unique_Numbers"] > 1, ["DESCRIPTION", "Details"]
    ].reset_index(drop=True)


def gl_type_mismatches(df: DataFrame) -> DataFrame:
    if df.empty:
        return pd.DataFrame()

    df2 = df.copy()
    df2["TYPE"] = df2["TYPE"].replace(
        {"nan": pd.NA, "None": pd.NA, "": pd.NA, "<NA>": pd.NA}
    )

    details = (
        df2.dropna(subset=["TYPE"])
        .assign(
            TYPE_FMT=lambda x: x["TYPE"].astype("string").str.strip(),
            DETAIL=lambda x: x["TYPE"].astype("string").str.strip()
            + ": "
            + x["ENV"].astype(str),
        )
        .groupby("DESC_KEY", as_index=False)
        .agg(
            DESCRIPTION=("DESCRIPTION", "first"),
            Unique_Types=("TYPE_FMT", "nunique"),
            Details=("DETAIL", lambda s: " | ".join(pd.unique(s))),
        )
    )
    return details.loc[
        details["Unique_Types"] > 1, ["DESCRIPTION", "Details"]
    ].reset_index(drop=True)


def cc_number_mismatches(df: DataFrame) -> DataFrame:
    if df.empty:
        return pd.DataFrame()
    details = (
        df.dropna(subset=["COSTCENTER"])
        .assign(
            COSTCENTER_FMT=lambda x: x["COSTCENTER"]
            .map(_as_int_or_str_no_decimal)
            .astype(str),
            DETAIL=lambda x: x["COSTCENTER"].map(_as_int_or_str_no_decimal).astype(str)
            + " ("
            + x["ENV"].astype(str)
            + ")",
        )
        .groupby("DESC_KEY", as_index=False)
        .agg(
            DESCRIPTION=("DESCRIPTION", "first"),
            Unique_Numbers=("COSTCENTER_FMT", "nunique"),
            Details=("DETAIL", lambda s: ", ".join(pd.unique(s))),
        )
    )
    return details.loc[
        details["Unique_Numbers"] > 1, ["DESCRIPTION", "Details"]
    ].reset_index(drop=True)


def cc_type_mismatches(_: DataFrame) -> DataFrame:
    return pd.DataFrame()


# ---------------------------- Export helpers ---------------------------------
def _filter_summary(counts: Dict[str, int]) -> DataFrame:
    return pd.DataFrame(list(counts.items()), columns=["Filter", "Count"])


def _config_df(component: str, cfg: Config) -> DataFrame:
    d = asdict(_cfg(cfg)).copy()
    d["component"] = component
    d["run_timestamp"] = datetime.now().isoformat()
    return pd.DataFrame([d])


def _summary_df(
    component: str,
    counts: Dict[str, int],
    pres_bin: DataFrame,
    actions: DataFrame,
    num_ms: DataFrame,
    type_ms: DataFrame,
) -> DataFrame:
    kept = int(counts.get("Kept", 0))
    removed = int(counts.get("Removed", 0))
    loaded = kept + removed
    total_envs = int(pres_bin.shape[1])
    total_descr = int(pres_bin.shape[0])
    n_create = (
        int((actions["ActionType"] == "Create").sum()) if not actions.empty else 0
    )
    n_review = (
        int((actions["ActionType"] == "Review").sum()) if not actions.empty else 0
    )
    n_num_mismatch = int(len(num_ms))
    n_type_mismatch = int(len(type_ms))

    rows = [
        ("Component", component),
        ("Run Timestamp", datetime.now().isoformat()),
        ("Rows Loaded", f"{loaded:,}"),
        ("Rows Kept", f"{kept:,}"),
        ("Rows Removed", f"{removed:,}"),
        ("Environments", f"{total_envs:,}"),
        ("Unique Descriptions", f"{total_descr:,}"),
        ("Create Actions", f"{n_create:,}"),
        ("Review Actions", f"{n_review:,}"),
        ("Number Mismatches", f"{n_num_mismatch:,}"),
        ("Type Mismatches", f"{n_type_mismatch:,}"),
    ]
    if "Removed_DOORTREK" in counts or "Removed_VERDREK" in counts:
        rows.insert(
            5, ("Removed DOORTREK", f"{int(counts.get('Removed_DOORTREK', 0)):,}")
        )
        rows.insert(
            6, ("Removed VERDREK", f"{int(counts.get('Removed_VERDREK', 0)):,}")
        )
    return pd.DataFrame(rows, columns=["Metric", "Value"])


# ---------------------------- Export -----------------------------------------
def export_excel(
    component: str,
    cleaned: DataFrame,
    filtered: DataFrame,
    counts: Dict[str, int],
    pres_bin: DataFrame,
    pres_enr: DataFrame,
    actions: DataFrame,
    num_ms: DataFrame,
    type_ms: DataFrame,
    logger: logging.Logger,
    cfg: Config,
) -> None:
    runtime_cfg = _cfg(cfg)
    base = (
        runtime_cfg.output_file_gl if component == "GL" else runtime_cfg.output_file_cc
    )
    filename = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    out = os.path.join(runtime_cfg.output_dir, filename)
    log_step(logger, f"Exporting {component} results to {out}", runtime_cfg)

    if runtime_cfg.dry_run:
        log_step(logger, f"Dry run enabled, skipped {component} export", runtime_cfg)
        return

    mapping = (
        runtime_cfg.sheet_names_gl if component == "GL" else runtime_cfg.sheet_names_cc
    )
    with pd.ExcelWriter(
        out,
        engine=runtime_cfg.excel_engine,
        datetime_format="dd-mm-yyyy",
        date_format="dd-mm-yyyy",
    ) as w:

        def ws(name: str, dfw: DataFrame, index: bool = False) -> None:
            sheet = mapping.get(name, name)
            dfw.to_excel(w, sheet_name=sheet, index=index)
            _autofit_xlsx(w, sheet, dfw.reset_index() if index else dfw)

        ws(
            "Summary",
            _summary_df(component, counts, pres_bin, actions, num_ms, type_ms),
            index=False,
        )
        ws("Cleaned Data", cleaned)
        ws("Filtered Out", filtered)
        ws("Filter Summary", _filter_summary(counts))
        ws("Presence Matrix (binary)", pres_bin.reset_index(), index=False)
        ws("Presence Matrix (enriched)", pres_enr.reset_index(), index=False)
        ws(
            "Create",
            (
                actions[actions["ActionType"] == "Create"]
                if not actions.empty
                else pd.DataFrame()
            ),
        )
        ws(
            "Review",
            (
                actions[actions["ActionType"] == "Review"]
                if not actions.empty
                else pd.DataFrame()
            ),
        )
        ws("Number Mismatches", num_ms)
        ws("Type Mismatches", type_ms)
        ws("Config", _config_df(component, runtime_cfg))

    log_step(logger, f"{component} export complete", runtime_cfg)


# ---------------------------- Summary Logging ---------------------------------
def _log_summary(
    component: str,
    logger: logging.Logger,
    counts: Dict[str, int],
    pres_bin: DataFrame,
    actions: DataFrame,
    num_ms: DataFrame,
    type_ms: DataFrame,
    cfg: Config,
) -> None:
    runtime_cfg = _cfg(cfg)
    total_envs = pres_bin.shape[1]
    total_descr = pres_bin.shape[0]
    n_create = (
        int((actions["ActionType"] == "Create").sum()) if not actions.empty else 0
    )
    n_review = (
        int((actions["ActionType"] == "Review").sum()) if not actions.empty else 0
    )
    n_num_mismatch = len(num_ms)
    n_type_mismatch = len(type_ms)

    logger.info("─" * 70)
    logger.info(f"📊 {component} SUMMARY")
    logger.info(f"Kept rows after filtering : {counts.get('Kept', 0):,}")
    logger.info(f"Removed rows              : {counts.get('Removed', 0):,}")
    if "Removed_DOORTREK" in counts or "Removed_VERDREK" in counts:
        logger.info(f"Removed DOORTREK         : {counts.get('Removed_DOORTREK', 0):,}")
        logger.info(f"Removed VERDREK          : {counts.get('Removed_VERDREK', 0):,}")
    logger.info(f"Unique descriptions       : {total_descr:,}")
    logger.info(f"Environments analyzed     : {total_envs:,}")
    logger.info(f"Create actions            : {n_create:,}")
    logger.info(f"Review actions            : {n_review:,}")
    logger.info(f"Number mismatches         : {n_num_mismatch:,}")
    logger.info(f"Type mismatches           : {n_type_mismatch:,}")
    logger.info("─" * 70)
    if runtime_cfg.verbose:
        logger.debug(f"First few actions:\n{actions.head(10).to_string(index=False)}")


# ---------------------------- Generic Runner (with summary) ------------------
def run_pipeline(
    component: str,
    loader: Callable[[logging.Logger, Config], DataFrame],
    filter_func: FilterFunc,
    presence_func: PresenceFunc,
    action_func: ActionFunc,
    num_mismatch_func: MismatchFunc,
    type_mismatch_func: MismatchFunc,
    log_filename: str,
    cfg: Config,
) -> Dict[str, object]:
    runtime_cfg = _cfg(cfg)
    logger = _new_logger(component, log_filename, runtime_cfg)
    log_step(logger, f"Start {component}", runtime_cfg)

    df = loader(logger, runtime_cfg)
    cleaned, filtered, counts = filter_func(df, logger, runtime_cfg)
    pres_bin, pres_enr = presence_func(cleaned, logger, runtime_cfg)
    actions = action_func(cleaned, pres_bin, logger, runtime_cfg)
    nm = num_mismatch_func(cleaned)
    tm = type_mismatch_func(cleaned)

    export_excel(
        component,
        cleaned,
        filtered,
        counts,
        pres_bin,
        pres_enr,
        actions,
        nm,
        tm,
        logger,
        runtime_cfg,
    )
    _log_summary(component, logger, counts, pres_bin, actions, nm, tm, runtime_cfg)

    log_step(logger, f"{component} Done", runtime_cfg)

    return {
        "raw": df,
        "cleaned": cleaned,
        "filtered": filtered,
        "counts": counts,
        "presence_binary": pres_bin,
        "presence_enriched": pres_enr,
        "actions": actions,
        "number_mismatches": nm,
        "type_mismatches": tm,
    }
