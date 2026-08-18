# Benchmark Test Runner (`test/test_slicer.py`)

Evaluation harness for the probabilistic program slicer. It loads `.prob` benchmark programs, runs the slicer across multiple slicing variants, optionally validates slicing correctness via Monte Carlo simulation, and exports performance and slice-quality metrics.

> **Location Notice:** This script lives in `test/` as part of the evaluation infrastructure and is distinct from the core slicing library in `src/prob_slicer/`. Execute all commands from the **repository root**.

---

## Quick Start

```bash
# 1. Install dependencies in editable mode
pip install -e ".[analysis]"

# 2. List all available real-world benchmarks
python test/test_slicer.py --list

# 3. Run a test on a single benchmark with a side-by-side variant comparison
python test/test_slicer.py --bench olmedo_nontermination --compare --summary
```

---

## Setup & Environment

Dependencies are managed via the root [README.md](README.md#installation):

```bash
pip install -e ".[analysis]"
```

This installs the required core and analysis dependencies, including `networkx`, `pandas`, and `openpyxl`.

### Import Resolution across Top-Level Directories

The evaluation runner coordinates code across three top-level directories (`src/`, `bench-src/`, and `test/`):

* **`src/prob_slicer/`**: Installed in editable mode via `pyproject.toml`, allowing `import prob_slicer` from any location.
* **`bench-src/benchmark_loader.py`**: Not installed as a package. `test_slicer.py` and `run_all_benchmarks.py` add `bench-src/` to `sys.path` dynamically at execution time.
* **`test/evaluator.py`**: Imported directly via local directory access alongside the test runners in `test/`.

---

## Slicing Variants

| Variant | Label | Control Dep. (`cd`) | Observation Dep. (`R`) | Behavior & Semantics |
| --- | --- | --- | --- | --- |
| **`ns`** | Nontermination-Sensitive, Distribution-Sensitive | `scd` | `obsntd(scd)` | Preserves exact execution distributions and non-termination semantics. |
| **`nids`** | Nontermination-Insensitive, Distribution-Sensitive | `wcd` | `obsntd(wcd)` | Relaxes termination strictness while maintaining distribution fidelity. |
| **`ni`** | Nontermination-Insensitive, Distribution-Insensitive | `wcd` | `obsd(wcd)` | **Most permissive variant.** Allows statements to be removed even if probability mass from non-terminating/blocked executions shifts to terminating paths. Does **not** guarantee exact preservation of the return distribution. |

*By default, all three variants are evaluated per benchmark.*

---

## Benchmark Files & Format

Benchmarks are loaded from `benchmarks/real-world/*.prob` by default (defined by `BENCHMARKS_DIR` in `bench-src/benchmark_loader.py`). Use `--benchdir` to target alternative directories:

```bash
python test/test_slicer.py --benchdir benchmarks/contrived --list
python test/test_slicer.py --bench my_bench --benchdir /path/to/custom/benchmarks
```

### Required Metadata Format

Every `.prob` benchmark file must begin with the following metadata header, followed by the probabilistic program source code:

```
// @METADATA:name = example_benchmark
// @METADATA:description = Brief description of the benchmark
// @METADATA:reference = Citation or paper reference
// @METADATA:criterion = return_variable
// @METADATA:expected = expected_behavior_summary
// @METADATA:tags = key-example, loop-heavy
```

*The `tags` field is optional; all other metadata fields are required.*

---

## Usage Guide

### Basic Commands

```bash
# List all available benchmarks
python test/test_slicer.py --list

# Run all benchmarks under all three variants
python test/test_slicer.py

# Run specific benchmark(s)
python test/test_slicer.py --bench olmedo_nontermination
python test/test_slicer.py --bench bench1 bench2

# Filter benchmarks by tag
python test/test_slicer.py --tag key-example

# Run a single slicing variant
python test/test_slicer.py --variant ns
```

### Comparison & Diagnostics

```bash
# Side-by-side variant comparison
python test/test_slicer.py --compare

# Print summary table at the end
python test/test_slicer.py --summary

# Show Phase 1 output (before postprocessing)
python test/test_slicer.py --phase1

# Emit .dot files for CFG and dependence graphs into dot_output/
python test/test_slicer.py --dot

# Print full dependence report (reaching defs, DDG, CDG, ObsDep edges)
python test/test_slicer.py --verbose

# Show slice computation progress
python test/test_slicer.py --debug
```

### Correctness Evaluation & Saving Results

```bash
# Run Monte Carlo correctness evaluation (checks NS, NI, and NIDS properties)
python test/test_slicer.py --evaluate --eval-runs 10000

# Save structured results
python test/test_slicer.py --save-json results.json
python test/test_slicer.py --save-csv results.csv
python test/test_slicer.py --save-xlsx results/experimental-result.xlsx --sheet-name real-world
python test/test_slicer.py --summary --save-summary-txt results/summary.txt

# Combined execution workflow
python test/test_slicer.py --tag key-example --compare --summary --evaluate
```

---

## CLI Flag Reference

| Flag | Description |
| --- | --- |
| **`--bench NAME...`** | Run specific benchmarks by name (default: all). |
| **`--tag TAG...`** | Run benchmarks matching specified tag(s). |
| **`--benchdir DIR`** | Benchmark directory path (default: `benchmarks/real-world/`). |
| **`--variant {ns,nids,ni}`** | Execute a single slicing variant. |
| **`--compare`** | Print side-by-side variant comparison per benchmark. |
| **`--summary`** | Output a consolidated summary table upon completion. |
| **`--phase1`** | Show Phase 1 output (before postprocessing). |
| **`--dot`** | Export CFG and dependence graphs as `.dot` files into `dot_output/`. |
| **`--verbose`** | Print full dependence analysis report per experiment. |
| **`--list`** | Output available benchmarks and exit. |
| **`--save-json FILE`** | Save structured results to JSON (excludes sliced source). |
| **`--save-csv FILE`** | Export flattened result rows to CSV. |
| **`--save-xlsx FILE`** | Write results to an Excel workbook sheet. |
| **`--sheet-name NAME`** | Target sheet name for `--save-xlsx` (default: benchmark directory name). |
| **`--save-summary-txt FILE`** | Write `--summary` table and memory breakdown to a text file. |
| **`--debug`** | Enable detailed slice computation logs. |
| **`--evaluate`** | Run Monte Carlo correctness validation. |
| **`--eval-runs N`** | Number of Monte Carlo simulation runs (default: `10000`). |

---

## Execution Pipeline

For each targeted benchmark and variant, `test_slicer.py` executes:

1. **Parsing & CFG:** Parses `.prob` source and constructs the Control Flow Graph.
2. **Dependence Analysis:** Computes control, data, and observation dependencies for the selected variant.
3. **Criteria Identification:** Isolates `return` node(s) in the CFG as the slicing criteria.
4. **Backward Slicing:** Computes the backward slice and reconstructs the sliced program (`slice_program`, or `slice_only` with `--phase1`).
5. **Metrics Collection:** Measures original/sliced statement counts, percentage reduction, while loop count, dependence edge counts, execution time, and peak RSS.
6. **Optional Diagnostics/Validation:** Emits `.dot` graph files, displays verbose reports, and runs Monte Carlo simulation (`--evaluate`) to compare output distributions between original and sliced programs.

> Exceptions occurring during a benchmark run are caught and logged with a traceback; execution continues uninterrupted for remaining experiments.

---

## Memory Usage Tracking

The runner tracks two complementary memory metrics:

### 1. Peak RSS (Authoritative Metric)

Read directly from `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` and normalized to KB across operating systems (Linux/macOS).

* **`peak_rss_kb`**: High-water mark of process-wide resident set size at the end of slicing.
* **`rss_delta_kb`**: Net growth in process peak RSS *during* that specific phase. An `rss_delta_kb` of `0` indicates the phase did not exceed a process-wide memory peak already established by an earlier benchmark or phase.
* **`eval_peak_rss_kb` / `eval_rss_delta_kb`**: Corresponding peak and delta values for the Monte Carlo evaluation phase (`--evaluate`).
* **Scope**: Process-wide; includes native/C-extension code (e.g., `networkx`) invisible to Python-level allocators.

### 2. Python Allocation Tracing (`tracemalloc`)

Provides a supplementary view of Python-level heap object allocations:

* **`py_peak_memory_kb`**: Peak Python allocation during slicing.
* **`py_current_memory_kb`**: Live Python memory retained at the end of the phase.
* **`eval_py_peak_memory_kb`**: Peak Python allocation during Monte Carlo evaluation.

*Note: `tracemalloc` values undercount total OS resource consumption. Use Peak RSS as the primary metric when reporting process memory usage.*

---

## Automated Paper-Ready Analysis

`run_all_benchmarks.py` automates the paper's multi-category benchmark suite pipeline. It discovers every subdirectory inside `benchmarks/` containing `.prob` files, executes all selected benchmarks, and writes each directory's results into a designated sheet inside `results/experimental-result.xlsx`.

```bash
# Run all benchmark directories (no Monte Carlo)
python test/run_all_benchmarks.py

# Full run with Monte Carlo evaluation + statistical analysis
python test/run_all_benchmarks.py --evaluate --eval-runs 2000 --analyze

# Target specific benchmark directories
python test/run_all_benchmarks.py --dirs prodigy real-world

# Save execution summaries and statistics to text files
python test/run_all_benchmarks.py --evaluate --summary --save-summary-txt results/run_summary.txt
```

> **Non-Destructive Excel Updates:** `run_all_benchmarks.py` and `save_results_xlsx()` replace only the worksheet corresponding to the active benchmark directory. Unrelated sheets in an existing workbook are preserved.

### Downstream Analysis (`get_statistics.py` & `data_analysis.py`)

Once the workbook is generated, analysis tools auto-discover all sheets in the Excel file without hardcoded category maps:

```bash
# Full paper-ready statistics + LaTeX table generation
python test/get_statistics.py

# High-level summary highlights
python test/data_analysis.py

# Target specific workbooks or sub-sheets
python test/get_statistics.py --xlsx results/my-results.xlsx --sheets prodigy real-world
```

### Complete One-Line End-to-End Execution

To execute all benchmark directories, evaluate correctness, save Excel worksheets, output run summaries, and print paper-ready statistical/memory analysis:

```bash
python test/run_all_benchmarks.py --evaluate --summary --analyze
```

---

## Rendering Graph Outputs

When running with `--dot`, Graphviz `.dot` files are emitted into `dot_output/`. Render them using Graphviz:

```bash
dot -Tpng dot_output/<benchmark>_<variant>_cfg.dot -o cfg.png
dot -Tpng dot_output/<benchmark>_<variant>_pdg.dot -o pdg.png
```
