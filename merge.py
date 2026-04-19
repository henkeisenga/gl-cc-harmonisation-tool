"""
Merge planner voor uniformeren van grootboek- en kostenplaatsschema's
over meerdere ENV's (DATALIB-COMPANY) naar één doel-ENV.

Nieuw:
- Extra tabs: Mappings_GL en Mappings_CC (oud -> canonical per bron-ENV),
  met Suggested_Action en Same_Number flags.
"""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config import Config
from core import DataFrame


class EnvModel(BaseModel):
    datalib: str
    bedrijf: str

    @property
    def env(self) -> str:
        return f"{self.datalib.strip()}-{self.bedrijf.strip()}"


class MergeSpec(BaseModel):
    sources: list[EnvModel] = Field(..., description="Bron-ENV's (DATALIB-COMPANY)")
    target: EnvModel = Field(..., description="Doel-ENV (DATALIB-COMPANY)")
    scope: list[str] = Field(default_factory=lambda: ["GL", "CC"])
    numbering_strategy: str = Field(default="keep_preferred")
    preferred_env: str | None = Field(
        default=None,
        description="ENV-string DATALIB-COMPANY, alleen voor keep_preferred",
    )

    @field_validator("scope")
    @classmethod
    def _scope_ok(cls, v: list[str]) -> list[str]:
        allowed = {"GL", "CC"}
        vv = [s.strip().upper() for s in v]
        for s in vv:
            if s not in allowed:
                raise ValueError(f"scope contains invalid entry: {s}")
        return vv

    @field_validator("numbering_strategy")
    @classmethod
    def _ns_ok(cls, v: str) -> str:
        vv = v.strip().lower()
        allowed = {"keep_preferred", "pick_majority", "first", "new_range"}
        if vv not in allowed:
            raise ValueError(f"numbering_strategy must be one of {sorted(allowed)}")
        return vv

    @model_validator(mode="after")
    def _validate_preferred_env(self) -> MergeSpec:
        if self.numbering_strategy == "keep_preferred" and not self.preferred_env:
            raise ValueError(
                "preferred_env is verplicht wanneer numbering_strategy='keep_preferred'"
            )
        return self


def _cfg(cfg: Config | None) -> Config:
    if cfg is None:
        raise ValueError("cfg is verplicht")
    return cfg


def _pick_number(
    grp: DataFrame,
    col_number: str,
    strategy: str,
    preferred_env: str | None,
) -> tuple[object | None, str]:
    nums = grp[col_number].dropna().tolist()
    if not nums:
        return None, "no_number"

    if strategy == "new_range":
        return None, "new_range"

    if strategy == "keep_preferred" and preferred_env:
        row = grp.loc[grp["ENV"] == preferred_env, col_number]
        if not row.empty and pd.notna(row.iloc[0]):
            return row.iloc[0], f"keep_preferred({preferred_env})"
        strategy = "pick_majority"

    if strategy == "pick_majority":
        s = grp[col_number].dropna().astype(str).value_counts()
        top = s.index[0]
        return top, "pick_majority"

    return grp[col_number].dropna().iloc[0], "first"


def _first_nonnull(s: pd.Series) -> str | None:
    x = s.dropna()
    return x.iloc[0] if not x.empty else None


def _same_value(a: object, b: object) -> bool:
    return pd.notna(a) and pd.notna(b) and str(a) == str(b)


def _target_collision_reason(
    unified: DataFrame,
    target_df: DataFrame,
    number_col: str,
) -> DataFrame:
    if unified.empty:
        return unified.assign(
            Target_Collision=False,
            Target_Collision_With="",
            Target_Existing_Number=pd.NA,
        )

    target_lookup = (
        target_df.dropna(subset=[number_col])[[number_col, "DESC_KEY", "DESCRIPTION"]]
        .drop_duplicates()
        .rename(
            columns={"DESC_KEY": "TARGET_DESC_KEY", "DESCRIPTION": "TARGET_DESCRIPTION"}
        )
    )
    out = unified.merge(
        target_lookup,
        left_on=[number_col],
        right_on=[number_col],
        how="left",
    )
    out["Target_Collision"] = out["TARGET_DESC_KEY"].notna() & out[
        "TARGET_DESC_KEY"
    ].ne(out["DESC_KEY"])
    out["Target_Collision_With"] = out["TARGET_DESCRIPTION"].fillna("")
    out["Target_Existing_Number"] = out[number_col]
    return out


def _build_unified_gl(
    df_gl: DataFrame, spec: MergeSpec
) -> tuple[DataFrame, DataFrame, DataFrame]:
    src_envs = {e.env for e in spec.sources}
    df = df_gl[df_gl["ENV"].isin(src_envs)].copy()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    records: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []

    for desc_key, grp in df.groupby("DESC_KEY", sort=True):
        display_descr = _first_nonnull(grp["DESCRIPTION"]) or str(desc_key)
        cn, why = _pick_number(
            grp, "ACCOUNT", spec.numbering_strategy, spec.preferred_env
        )
        ctype = _first_nonnull(grp["TYPE"])
        clang = _first_nonnull(grp["DESCRIPTION_LANG"])

        gl_nums = sorted({str(x) for x in grp["ACCOUNT"].dropna().unique()})
        types = sorted({str(x) for x in grp["TYPE"].dropna().unique()})

        if len(gl_nums) > 1 or len(types) > 1:
            conflicts.append(
                {
                    "DESC_KEY": desc_key,
                    "DESCRIPTION": display_descr,
                    "Numbers": ", ".join(gl_nums) or "",
                    "Types": ", ".join(types) or "",
                    "Why_Canonical": why,
                }
            )

        records.append(
            {
                "DESC_KEY": desc_key,
                "DESCRIPTION": display_descr,
                "ACCOUNT_CANONICAL": cn,
                "TYPE_CANONICAL": ctype,
                "DESCRIPTION_LANG_CANONICAL": clang,
                "Why_Canonical": why,
            }
        )

    unified = pd.DataFrame(records).sort_values("DESCRIPTION").reset_index(drop=True)
    conflicts_df = pd.DataFrame(conflicts)
    if not conflicts_df.empty:
        conflicts_df = conflicts_df.sort_values("DESCRIPTION").reset_index(drop=True)

    target_env = spec.target.env
    tgt = df_gl[df_gl["ENV"] == target_env][
        ["DESC_KEY", "DESCRIPTION", "ACCOUNT", "TYPE"]
    ].copy()
    tgt.rename(
        columns={
            "ACCOUNT": "ACCOUNT_TGT",
            "TYPE": "TYPE_TGT",
            "DESCRIPTION": "DESCRIPTION_TGT",
        },
        inplace=True,
    )

    act = unified.merge(tgt, on="DESC_KEY", how="left")
    act = _target_collision_reason(
        act.rename(columns={"ACCOUNT_CANONICAL": "ACCOUNT"}),
        df_gl[df_gl["ENV"] == target_env],
        "ACCOUNT",
    ).rename(columns={"ACCOUNT": "ACCOUNT_CANONICAL"})

    act["ActionType"] = "Review"
    act.loc[act["ACCOUNT_CANONICAL"].isna(), "ActionType"] = "Create"
    missing_in_tgt = act["ACCOUNT_TGT"].isna() & act["ACCOUNT_CANONICAL"].notna()
    exists_same = act.apply(
        lambda r: _same_value(r["ACCOUNT_TGT"], r["ACCOUNT_CANONICAL"]), axis=1
    )
    act.loc[missing_in_tgt, "ActionType"] = "Create"
    act.loc[act["Target_Collision"], "ActionType"] = "Review"

    act["ENV"] = target_env
    act["DATALIB"] = spec.target.datalib
    act["COMPANY"] = spec.target.bedrijf
    act["Reason"] = "Afstemmen/cross-check met canonical"
    act.loc[missing_in_tgt, "Reason"] = "Target mist nummer"
    act.loc[act["ACCOUNT_CANONICAL"].isna(), "Reason"] = (
        "Canonical is leeg; kies nieuw nummer"
    )
    act.loc[exists_same, "Reason"] = "Target heeft al hetzelfde canonical nummer"
    act.loc[act["Target_Collision"], "Reason"] = (
        "Canonical nummer bestaat in target voor andere omschrijving: "
        + act["Target_Collision_With"].astype(str)
    )

    actions = act[
        [
            "ActionType",
            "Reason",
            "ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "DESCRIPTION_LANG_CANONICAL",
            "ACCOUNT_CANONICAL",
            "TYPE_CANONICAL",
        ]
    ].rename(
        columns={
            "DESCRIPTION_LANG_CANONICAL": "DESCRIPTION_LANG",
            "ACCOUNT_CANONICAL": "ACCOUNT",
            "TYPE_CANONICAL": "TYPE",
        }
    )

    return unified, conflicts_df, actions


def _build_mappings_gl(
    df_gl: DataFrame, unified_gl: DataFrame, spec: MergeSpec
) -> DataFrame:
    if unified_gl.empty:
        return pd.DataFrame()

    src_envs = {e.env for e in spec.sources}
    gl_src = (
        df_gl[df_gl["ENV"].isin(src_envs)][
            ["ENV", "DATALIB", "COMPANY", "DESC_KEY", "DESCRIPTION", "ACCOUNT"]
        ]
        .dropna(subset=["DESC_KEY"])
        .copy()
    ).drop_duplicates()

    uni = unified_gl[["DESC_KEY", "ACCOUNT_CANONICAL", "Why_Canonical"]].copy()

    mp = gl_src.merge(uni, on="DESC_KEY", how="left")
    mp.rename(columns={"ENV": "Source_ENV", "ACCOUNT": "ACCOUNT_SOURCE"}, inplace=True)

    mp["Same_Number"] = mp.apply(
        lambda r: _same_value(r["ACCOUNT_SOURCE"], r["ACCOUNT_CANONICAL"]), axis=1
    )
    mp["Target_ENV"] = spec.target.env

    def _suggest(row) -> str:
        if pd.isna(row["ACCOUNT_CANONICAL"]):
            return "Create (new range/choose number)"
        if row["Same_Number"]:
            return "Keep (same number)"
        return "Map to canonical"

    mp["Suggested_Action"] = mp.apply(_suggest, axis=1)

    mp = mp[
        [
            "Source_ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "ACCOUNT_SOURCE",
            "ACCOUNT_CANONICAL",
            "Same_Number",
            "Suggested_Action",
            "Target_ENV",
            "Why_Canonical",
        ]
    ]
    mp = mp.drop_duplicates(
        subset=["Source_ENV", "DESCRIPTION", "ACCOUNT_SOURCE"]
    ).sort_values(["Source_ENV", "DESCRIPTION", "ACCOUNT_SOURCE"])
    return mp


def _build_unified_cc(
    df_cc: DataFrame, spec: MergeSpec
) -> tuple[DataFrame, DataFrame, DataFrame]:
    src_envs = {e.env for e in spec.sources}
    df = df_cc[df_cc["ENV"].isin(src_envs)].copy()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    records: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []

    for desc_key, grp in df.groupby("DESC_KEY", sort=True):
        display_descr = _first_nonnull(grp["DESCRIPTION"]) or str(desc_key)
        cn, why = _pick_number(
            grp, "COSTCENTER", spec.numbering_strategy, spec.preferred_env
        )
        clang = _first_nonnull(grp["DESCRIPTION_LANG"])
        nums = sorted({str(x) for x in grp["COSTCENTER"].dropna().unique()})
        if len(nums) > 1:
            conflicts.append(
                {
                    "DESC_KEY": desc_key,
                    "DESCRIPTION": display_descr,
                    "Numbers": ", ".join(nums),
                    "Why_Canonical": why,
                }
            )
        records.append(
            {
                "DESC_KEY": desc_key,
                "DESCRIPTION": display_descr,
                "COSTCENTER_CANONICAL": cn,
                "DESCRIPTION_LANG_CANONICAL": clang,
                "Why_Canonical": why,
            }
        )

    unified = pd.DataFrame(records).sort_values("DESCRIPTION").reset_index(drop=True)
    conflicts_df = pd.DataFrame(conflicts)
    if not conflicts_df.empty:
        conflicts_df = conflicts_df.sort_values("DESCRIPTION").reset_index(drop=True)

    target_env = spec.target.env
    tgt = df_cc[df_cc["ENV"] == target_env][
        ["DESC_KEY", "DESCRIPTION", "COSTCENTER"]
    ].copy()
    tgt.rename(
        columns={"COSTCENTER": "COSTCENTER_TGT", "DESCRIPTION": "DESCRIPTION_TGT"},
        inplace=True,
    )

    act = unified.merge(tgt, on="DESC_KEY", how="left")
    act = _target_collision_reason(
        act.rename(columns={"COSTCENTER_CANONICAL": "COSTCENTER"}),
        df_cc[df_cc["ENV"] == target_env],
        "COSTCENTER",
    ).rename(columns={"COSTCENTER": "COSTCENTER_CANONICAL"})

    act["ActionType"] = "Review"
    act.loc[act["COSTCENTER_CANONICAL"].isna(), "ActionType"] = "Create"
    missing_in_tgt = act["COSTCENTER_TGT"].isna() & act["COSTCENTER_CANONICAL"].notna()
    exists_same = act.apply(
        lambda r: _same_value(r["COSTCENTER_TGT"], r["COSTCENTER_CANONICAL"]), axis=1
    )
    act.loc[missing_in_tgt, "ActionType"] = "Create"
    act.loc[act["Target_Collision"], "ActionType"] = "Review"

    act["ENV"] = target_env
    act["DATALIB"] = spec.target.datalib
    act["COMPANY"] = spec.target.bedrijf
    act["Reason"] = "Afstemmen/cross-check met canonical"
    act.loc[missing_in_tgt, "Reason"] = "Target mist nummer"
    act.loc[act["COSTCENTER_CANONICAL"].isna(), "Reason"] = (
        "Canonical is leeg; kies nieuw nummer"
    )
    act.loc[exists_same, "Reason"] = "Target heeft al hetzelfde canonical nummer"
    act.loc[act["Target_Collision"], "Reason"] = (
        "Canonical nummer bestaat in target voor andere omschrijving: "
        + act["Target_Collision_With"].astype(str)
    )

    actions = act[
        [
            "ActionType",
            "Reason",
            "ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "DESCRIPTION_LANG_CANONICAL",
            "COSTCENTER_CANONICAL",
        ]
    ].rename(
        columns={
            "DESCRIPTION_LANG_CANONICAL": "DESCRIPTION_LANG",
            "COSTCENTER_CANONICAL": "COSTCENTER",
        }
    )
    return unified, conflicts_df, actions


def _build_mappings_cc(
    df_cc: DataFrame, unified_cc: DataFrame, spec: MergeSpec
) -> DataFrame:
    if unified_cc.empty:
        return pd.DataFrame()

    src_envs = {e.env for e in spec.sources}
    cc_src = (
        df_cc[df_cc["ENV"].isin(src_envs)][
            ["ENV", "DATALIB", "COMPANY", "DESC_KEY", "DESCRIPTION", "COSTCENTER"]
        ]
        .dropna(subset=["DESC_KEY"])
        .copy()
    ).drop_duplicates()

    uni = unified_cc[["DESC_KEY", "COSTCENTER_CANONICAL", "Why_Canonical"]].copy()

    mp = cc_src.merge(uni, on="DESC_KEY", how="left")
    mp.rename(
        columns={"ENV": "Source_ENV", "COSTCENTER": "COSTCENTER_SOURCE"}, inplace=True
    )

    mp["Same_Number"] = mp.apply(
        lambda r: _same_value(r["COSTCENTER_SOURCE"], r["COSTCENTER_CANONICAL"]), axis=1
    )
    mp["Target_ENV"] = spec.target.env

    def _suggest(row) -> str:
        if pd.isna(row["COSTCENTER_CANONICAL"]):
            return "Create (new range/choose number)"
        if row["Same_Number"]:
            return "Keep (same number)"
        return "Map to canonical"

    mp["Suggested_Action"] = mp.apply(_suggest, axis=1)

    mp = mp[
        [
            "Source_ENV",
            "DATALIB",
            "COMPANY",
            "DESCRIPTION",
            "COSTCENTER_SOURCE",
            "COSTCENTER_CANONICAL",
            "Same_Number",
            "Suggested_Action",
            "Target_ENV",
            "Why_Canonical",
        ]
    ]

    mp = mp.drop_duplicates(
        subset=["Source_ENV", "DESCRIPTION", "COSTCENTER_SOURCE"]
    ).sort_values(["Source_ENV", "DESCRIPTION", "COSTCENTER_SOURCE"])
    return mp


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


def export_merge_plan(
    spec: MergeSpec,
    df_gl_clean: DataFrame,
    df_cc_clean: DataFrame,
    out_dir: str,
    cfg: Config,
) -> str:
    runtime_cfg = _cfg(cfg)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"Merge_Plan_{ts}.xlsx")

    with pd.ExcelWriter(
        path,
        engine=runtime_cfg.excel_engine,
        datetime_format="dd-mm-yyyy",
        date_format="dd-mm-yyyy",
    ) as w:
        cfg_df = pd.DataFrame(
            [
                {
                    "Target_ENV": spec.target.env,
                    "Scope": ",".join(spec.scope),
                    "Numbering_Strategy": spec.numbering_strategy,
                    "Preferred_ENV": spec.preferred_env or "",
                    "Created_At": datetime.now().isoformat(),
                }
            ]
        )
        srcs = pd.DataFrame(
            [
                {"Source_ENV": s.env, "DATALIB": s.datalib, "COMPANY": s.bedrijf}
                for s in spec.sources
            ]
        )

        cfg_df.to_excel(w, sheet_name="Config", index=False)
        _autofit_xlsx(w, "Config", cfg_df)
        srcs.to_excel(w, sheet_name="Sources", index=False)
        _autofit_xlsx(w, "Sources", srcs)

        if "GL" in spec.scope:
            uni_gl, conf_gl, act_gl = _build_unified_gl(df_gl_clean, spec)
            uni_gl.to_excel(w, sheet_name="Unified_GL", index=False)
            _autofit_xlsx(w, "Unified_GL", uni_gl)
            conf_gl.to_excel(w, sheet_name="Conflicts_GL", index=False)
            _autofit_xlsx(w, "Conflicts_GL", conf_gl)
            act_gl.to_excel(w, sheet_name="Actions_GL", index=False)
            _autofit_xlsx(w, "Actions_GL", act_gl)
            map_gl = _build_mappings_gl(df_gl_clean, uni_gl, spec)
            map_gl.to_excel(w, sheet_name="Mappings_GL", index=False)
            _autofit_xlsx(w, "Mappings_GL", map_gl)

        if "CC" in spec.scope:
            uni_cc, conf_cc, act_cc = _build_unified_cc(df_cc_clean, spec)
            uni_cc.to_excel(w, sheet_name="Unified_CC", index=False)
            _autofit_xlsx(w, "Unified_CC", uni_cc)
            conf_cc.to_excel(w, sheet_name="Conflicts_CC", index=False)
            _autofit_xlsx(w, "Conflicts_CC", conf_cc)
            act_cc.to_excel(w, sheet_name="Actions_CC", index=False)
            _autofit_xlsx(w, "Actions_CC", act_cc)
            map_cc = _build_mappings_cc(df_cc_clean, uni_cc, spec)
            map_cc.to_excel(w, sheet_name="Mappings_CC", index=False)
            _autofit_xlsx(w, "Mappings_CC", map_cc)

    return path


def plan_merge_from_cleaned(
    df_gl_clean: DataFrame,
    df_cc_clean: DataFrame,
    merge_spec: dict,
    cfg: Config,
) -> str:
    runtime_cfg = _cfg(cfg)
    try:
        spec = MergeSpec.model_validate(merge_spec)
    except ValidationError as e:
        raise ValueError(f"Invalid merge-spec: {e}") from e

    return export_merge_plan(
        spec, df_gl_clean, df_cc_clean, runtime_cfg.output_dir, cfg=runtime_cfg
    )
