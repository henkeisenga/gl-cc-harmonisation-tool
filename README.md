# 🧾 Universal Ledger & Cost Center Pipeline

[![CI](https://github.com/henkeisenga/gl-cc-harmonisation-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/henkeisenga/gl-cc-harmonisation-tool/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/henkeisenga/gl-cc-harmonisation-tool/branch/main/graph/badge.svg)](https://codecov.io/gh/henkeisenga/gl-cc-harmonisation-tool)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)]()
[![Status](https://img.shields.io/badge/status-active-blue.svg)]()
[![Lint](https://img.shields.io/badge/lint-ruff-purple.svg)]()
[![Format](https://img.shields.io/badge/code%20style-black-000000.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A pipeline for analysing, validating, and harmonising financial master data across multiple ERP environments.

Supports:
- General Ledger (GL)
- Cost Centers (CC)

across multiple **ENVs (DATALIB–COMPANY)**.

---

## 📈 Status

Stable internal tooling for financial master data harmonisation.  
This project is actively evolving; refactoring and improvements are ongoing.

---

## 📚 Table of Contents

- Overview
- Features
- Use Cases
- Quick Start
- Repository Structure
- Architecture
- Merge
- Expected Input Format
- Configuration
- CLI Usage
- Output
- Development
- Testing
- Notes
- Common Pitfalls
- Limitations
- License

---

## 📌 Overview

This pipeline standardizes and harmonizes financial master data across ERP environments by:

- normalizing and validating input
- aligning entities via canonical descriptions
- detecting inconsistencies
- generating actionable outputs
- supporting structured merge planning

---

## 🚀 Features

- Schema validation (Pandera)
- Canonical matching via `DESC_KEY`
- Presence matrices (binary + enriched)
- Vectorized action generation
- Mismatch detection
- Excel reporting
- Merge planner with configurable strategies
- YAML + CLI configuration

---

## Use Cases

- ERP harmonisation
- Data migration
- Chart of accounts alignment

---

## ▶️ Quick Start

### 1. Clone and setup environment

```bash
git clone <your-repo>
cd <your-repo>

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Prepare input data

Place your Excel files in `data/`. Expected files:

- `General_ledger_masterdata_demo.xlsx` — sheet: `Accounts`
- `Costcenter_masterdata_demo.xlsx` — sheet: `CostCenters`

### 3. Configure the pipeline

```yaml
input_file_gl: data/General_ledger_masterdata_demo.xlsx
input_file_cc: data/Costcenter_masterdata_demo.xlsx
```

### 4. Run

```bash
python app.py --config config.yaml
```

### 5. Optional: merge plan

```bash
python app.py --config config.yaml --merge-spec merge-spec.yaml
```

---

## 📁 Repository Structure

```text
app.py          # CLI entrypoint
core.py         # Pipeline logic
merge.py        # Merge planner
config.py       # Configuration model
tests/          # Test suite
data/           # Input files
output/         # Generated output
logs/           # Log files
```

---

## 🧱 Architecture

```mermaid
flowchart LR
    A[Excel Input GL/CC] --> B[Load + Normalize]
    B --> C[Schema Validation]
    C --> D["Canonical Key (DESC_KEY)"]
    D --> E[Filtering]
    E --> F[Presence Matrix]
    F --> G[Actions (Create / Review)]
    G --> H[Mismatch Analysis]
    H --> I[Excel Export]

    E --> J[Merge Planner]
    J --> K[Unified Schema]
    K --> L[Merge Plan Excel]
```

---

## 🔀 Merge

The merge planner combines multiple source ENVs into a single unified structure.

### Strategy options

- keep_preferred
- pick_majority
- first
- new_range

### Example

```yaml
sources:
  - datalib: ERP1
    company: 1
  - datalib: ERP2
    company: 1

target:
  datalib: ERP_TARGET
  company: 1

scope: [GL, CC]
numbering_strategy: keep_preferred
preferred_env: ERP1-1
```

### CLI

```bash
python app.py --config config.yaml --merge-spec merge-spec.yaml
```

---

## 📥 Expected Input Format

### GL

| Column | Required |
|--------|----------|
| DATALIB | ✅ |
| COMPANY | ✅ |
| ACCOUNT | ⚠️ |
| DESCRIPTION | ✅ |
| TYPE | ⚠️ |
| LASTUSED_DATE | ⚠️ |

### CC

| Column | Required |
|--------|----------|
| DATALIB | ✅ |
| COMPANY | ✅ |
| COSTCENTER | ⚠️ |
| DESCRIPTION | ✅ |

---

## ⚙️ Configuration

```yaml
input_file_gl: data/General_ledger_masterdata_demo.xlsx
input_file_cc: data/Costcenter_masterdata_demo.xlsx

filter_fislib: []
filter_companies: []
filter_doortrek: true
filter_verdrek: true
lastused_days: null
```

Priority: CLI > YAML > defaults  
Runtime directories are created at execution time.

---

## ▶️ CLI Usage

```bash
python app.py --config config.yaml
```

---

## 📊 Output

Generated in `output/`:

- Cleaned Data
- Presence Matrix
- Actions
- Mismatches
- Summary

---

## 🧪 Development

```bash
ruff check . --fix
black .
pytest -v
```

---

## 🧪 Testing

```bash
pytest -v
pytest -v --cov=. --cov-report=xml
```

---

## 📌 Notes

- Empty filters = no filtering
- DESC_KEY is the primary key
- ENV = DATALIB-COMPANY

---

## ⚠️ Common Pitfalls

| Problem | Cause |
|--------|-------|
| No output | Filters exclude everything |
| Schema error | Missing column |

---

## ⚠️ Limitations

- Matching is description-based
- Requires structured Excel input
- No ERP write-back

---

## 📄 License

MIT License
