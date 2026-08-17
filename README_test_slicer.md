# test/test_slicer.py — Benchmark Test Runner

Evaluation script for the probabilistic program slicer. It loads benchmark
programs, runs the slicer under one or more slicing variants, optionally
checks correctness via Monte Carlo simulation, and reports results.

Lives in `test/` — it is an evaluation script, not part of the core
slicing library (`src/prob_slicer/`). All commands below are meant to be
run from the **repo root**.

---

## Requirements

- Python 3.10+ (uses `from __future__ import annotations` and modern type hints)
- `networkx`
- `pandas` and `openpyxl` — only required for `--save-xlsx`,
  `run_all_benchmarks.py`, `get_statistics.py`, and `data_analysis.py`
- Repo layout (see the root [README.md](README.md#project-structure) for
  the full tree):
  - `src/prob_slicer/` — core slicing library (`parse`, `build_cfg`,
    `cfg_to_dot`, `parser`, `pretty_print`, `ast_nodes`, `dependence`,
    `slicer`). Installed as an editable package via `pyproject.toml`, so
    `import prob_slicer` works from anywhere once installed.
  - `bench-src/benchmark_loader.py` — loads `.prob` benchmark files.
    Imported by scripts in `test/` via a small `sys.path` shim at the top
    of `test_slicer.py` / `run_all_benchmarks.py` (no extra install step
    needed for this one).
  - `test/evaluator.py` — Monte Carlo correctness evaluation, imported
    directly since it lives alongside `test_slicer.py`.
  - `test/run_all_benchmarks.py` — orchestrates `test_slicer.py` across
    every `benchmarks/<category>/` directory (imports `run_benchmark` and
    `save_results_xlsx` directly from `test_slicer.py`).

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[analysis]"
```

This installs `prob_slicer` in editable mode (from `src/prob_slicer/`) plus
`pandas`/`openpyxl`. See the root [README.md](README.md#installation) for
details, including the optional `grammar` extra for regenerating the
parser from `ProbLang.g4`.

## Benchmark Files

By default, benchmarks are loaded from `benchmarks/real-world/*.prob` (see
`BENCHMARKS_DIR` in `bench-src/benchmark_loader.py`). Pass `--benchdir DIR`
to load `.prob` files from a different directory instead, e.g.:

```bash
python test/test_slicer.py --benchdir benchmarks/contrived --list
python test/test_slicer.py --bench my_bench --benchdir /path/to/custom/benchmarks
```

Each `.prob` file must contain metadata lines of the form:

```
// @METADATA:name = ...
// @METADATA:description = ...
// @METADATA:reference = ...
// @METADATA:criterion = ...
// @METADATA:expected = ...
// @METADATA:tags = tag1, tag2
```

followed by the program source. `tags` is optional; the rest are required.

## Slicing Variants

| Variant | Label                                                    | cd  | R            |
|---------|-----------------------------------------------------------|-----|--------------|
| `ns`    | Nontermination-Sensitive, Distribution-Sensitive           | scd | obsntd(scd)  |
| `nids`  | Nontermination-Insensitive, Distribution-Sensitive          | wcd | obsntd(wcd)  |
| `ni`    | Nontermination-Insensitive, Distribution-Insensitive (most aggressive) | wcd | obsd(wcd) |

By default all three variants are run per benchmark.

## Usage

```bash
# List all available benchmarks and exit
python test/test_slicer.py --list

# Run all benchmarks, all three variants
python test/test_slicer.py

# Run a specific benchmark
python test/test_slicer.py --bench olmedo_nontermination

# Run multiple benchmarks
python test/test_slicer.py --bench bench1 bench2

# Filter benchmarks by tag
python test/test_slicer.py --tag key-example

# Load benchmarks from a different directory
python test/test_slicer.py --benchdir benchmarks/contrived

# Run only one variant
python test/test_slicer.py --variant ns

# Side-by-side comparison of all three variants per benchmark
python test/test_slicer.py --compare

# Print a final summary table across all benchmarks/variants
python test/test_slicer.py --summary

# Show Phase 1 output (before postprocessing)
python test/test_slicer.py --phase1

# Emit .dot files for CFG and PDG into dot_output/
python test/test_slicer.py --dot

# Print full dependence report (reaching defs, DDG, CDG, ObsDep edges)
python test/test_slicer.py --verbose

# Enable slice computation progress/debug output
python test/test_slicer.py --debug

# Run Monte Carlo correctness evaluation (checks NS/NI/NIDS properties)
python test/test_slicer.py --evaluate --eval-runs 10000

# Save all results (excluding sliced source) to a JSON file
python test/test_slicer.py --save-json results.json

# Save all results (flattened) to a CSV file, e.g. to open in Excel
python test/test_slicer.py --save-csv results.csv

# Save to both at once
python test/test_slicer.py --save-json results.json --save-csv results.csv

# Combine flags
python test/test_slicer.py --tag key-example --compare --summary --evaluate
```

### CLI Flags

| Flag           | Description                                                        |
|----------------|---------------------------------------------------------------------|
| `--bench NAME...` | Run specific benchmarks by name (default: all)                  |
| `--tag TAG...`    | Run only benchmarks matching these tags                         |
| `--benchdir DIR`  | Directory to load `.prob` benchmark files from (default: `benchmarks/real-world/`) |
| `--variant {ns,nids,ni}` | Run a single slicing variant (default: all three)        |
| `--compare`       | Print side-by-side comparison of all three variants per benchmark |
| `--summary`       | Print a summary table of all results at the end                 |
| `--phase1`        | Show Phase 1 output (Definition 6.6, before postprocessing)     |
| `--dot`           | Emit `.dot` files for CFG and dependence graphs into `dot_output/` |
| `--verbose`       | Print full dependence report for each experiment                |
| `--list`          | List all available benchmarks and exit                          |
| `--save-json FILE`| Save all results (excluding sliced source) to a JSON file       |
| `--save-csv FILE` | Save all results, flattened to one row per benchmark/variant, to a CSV file |
| `--save-xlsx FILE` | Write/overwrite a sheet of results in an Excel workbook (pairs with `--sheet-name`) |
| `--sheet-name NAME` | Sheet name to use with `--save-xlsx` (default: `--benchdir` name, or `"results"`) |
| `--save-summary-txt FILE` | Save the `--summary` table + memory breakdown to a text file (requires `--summary`) |
| `--debug`         | Enable slice computation progress output                        |
| `--evaluate`      | Run Monte Carlo correctness evaluation                          |
| `--eval-runs N`   | Number of Monte Carlo runs for evaluation (default: 10000)      |

## What It Does

For each selected benchmark and each selected variant, `test_slicer.py`:

1. Parses the benchmark source and builds a CFG (`parse`, `build_cfg`).
2. Runs `DependenceAnalysis` for the variant to compute control/data/
   observation dependence.
3. Finds the slicing criterion (the `return` node(s) in the CFG).
4. Computes the backward slice and reconstructs the sliced program
   (`slice_program`, or `slice_only` with `--phase1`).
5. Computes size/reduction metrics (statement counts, % reduction, while-loop
   count, cd/dd edge counts, elapsed time, and peak resident set size).
6. Optionally emits `.dot` files, prints a dependence report (`--verbose`),
   and/or runs Monte Carlo correctness evaluation (`--evaluate`) comparing
   the original and sliced program's output distributions.

Results are printed per experiment, optionally compared per benchmark
(`--compare`), summarized across all runs (`--summary`), and optionally
saved to JSON (`--save-json`).

## Memory Usage Tracking

Alongside execution time, every run reports two different memory
measurements: **peak RSS** (the actual memory-usage metric) and
**Python-level allocation size** (a secondary, finer-grained breakdown).

### Peak RSS — actual memory usage

The primary metric is process-wide **peak resident set size (RSS)**, read
from `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`. This is the real
OS-level memory the process has touched — it reflects the slicer's actual
resource consumption, including C-extension internals (e.g. NetworkX) that
`tracemalloc` cannot see.

`ru_maxrss` units differ by platform (Linux reports KB, macOS reports
bytes), so `test_slicer.py` normalizes both to KB via `platform.system()`.

`ru_maxrss` is also a monotonically non-decreasing high-water mark for the
**whole process** — it can only go up, never down, and it isn't reset
between benchmarks. So for each phase, two values are recorded:

- **`peak_rss_kb`** — the process's peak RSS as of the end of that phase
  (a running total since the process started, not phase-isolated).
- **`rss_delta_kb`** — how much that peak grew *during* this specific
  phase (`peak_rss_kb` before → after). This is `0` whenever the process
  had already touched at least that much memory in an earlier phase, which
  is expected and not a bug — it just means this phase didn't push memory
  usage to a new high.
- **`eval_peak_rss_kb`** / **`eval_rss_delta_kb`** — the same, but for the
  separate Monte Carlo correctness evaluation phase (`--evaluate` only).

### Python-level allocation (`tracemalloc`) — supplementary detail

A secondary measurement uses Python's built-in `tracemalloc` module to
trace **Python object allocations only** (not full OS memory):

- **`py_peak_memory_kb`** — high-water mark of Python-level memory
  allocated during parsing, CFG construction, dependence analysis, and
  slice reconstruction.
- **`py_current_memory_kb`** — live (still-referenced) Python memory at the
  end of that phase.
- **`eval_py_peak_memory_kb`** — same, for the evaluation phase.

This is useful for spotting relative memory cost differences between
benchmarks/variants at the Python level, but it undercounts total resource
consumption since it excludes C-extension internals — use peak RSS as the
authoritative number.

### Where this shows up

- Per-experiment output prints a `Peak RSS` line (process, slicing +
  evaluation) and a `Py alloc` line (tracemalloc, slicing + evaluation).
- `--compare` adds `RSS_KB` and `PY_KB` columns next to `ms`.
- `--summary` adds `RSS_KB` and `PY_KB` columns per row, a **"Memory usage
  by variant"** breakdown (peak and average RSS *and* peak/average
  Python-level allocation, aggregated per slicing variant across all
  benchmarks in the run), and a footer line with the overall max/average
  for both metrics.
- `--save-json` includes `peak_rss_kb`, `rss_delta_kb`,
  `py_peak_memory_kb`, `py_current_memory_kb`, and (if evaluated)
  `eval_peak_rss_kb`, `eval_rss_delta_kb`, `eval_py_peak_memory_kb` as
  top-level fields per result.
- `--save-csv` includes the same fields as columns.
- `--save-xlsx` includes both `RSS_KB` and `PY_KB` columns (see below).

## Saving Results

By default, results only print to the console. To persist them:

- **`--save-json FILE`** — writes all results (one entry per benchmark ×
  variant) as structured JSON. Includes nested fields (`slice_nodes`,
  `tags`, `peak_rss_kb`, `rss_delta_kb`, `py_peak_memory_kb`,
  `py_current_memory_kb`, and — if `--evaluate` was used —
  `eval_peak_rss_kb`, `eval_rss_delta_kb`, `eval_py_peak_memory_kb` plus
  the full `eval` object with `ns_ok`/`ni_ok`/`nids_ok`, `q`, `tv_shape`,
  etc.). Excludes the sliced source code.

- **`--save-csv FILE`** — writes all results flattened to one row per
  benchmark × variant, suitable for opening directly in Excel/Sheets or
  loading with pandas. List/set fields (`tags`, `criterion_nid`) are
  joined into `;`-separated strings. Includes `peak_rss_kb`, `rss_delta_kb`,
  `py_peak_memory_kb`, and `py_current_memory_kb` columns. If `--evaluate`
  was used, correctness and memory fields are included as `eval_*` columns
  (e.g. `eval_ns_ok`, `eval_q`, `eval_tv_shape`, `eval_peak_rss_kb`);
  otherwise those columns are simply absent.

- **`--save-xlsx FILE` (+ `--sheet-name NAME`)** — writes/overwrites one
  sheet of an Excel workbook with a flat results table (`Benchmark`,
  `Variant`, `orig`, `slice`, `While`, `Reduction`, `ms`, `RSS_KB`,
  `PY_KB`, `NS`, `NI`, `NIDS`). Only the target sheet is touched — other
  sheets in an existing workbook are preserved. `--sheet-name` defaults to
  the `--benchdir` directory name (or `"results"` if `--benchdir` wasn't
  given). Requires `openpyxl` (`pip install openpyxl`). This is the
  building block behind the automated workflow below — see
  [Automated Paper-Ready Analysis](#automated-paper-ready-analysis).

- **`--save-summary-txt FILE`** (requires `--summary`) — saves the
  `--summary` table, correctness breakdown, and per-variant memory
  breakdown to a plain-text file, so the summary isn't console-output-only.

- **`--dot`** — not a results file, but also persists output: emits CFG/PDG
  Graphviz `.dot` files to `dot_output/` per benchmark/variant.

- **Shell redirection** — everything printed to the console (including
  `--compare` and `--summary` tables) can also be captured directly:

  ```bash
  python test/test_slicer.py --benchdir benchmarks/contrived --evaluate \
      --compare --summary > run.log 2>&1
  ```

`--save-json`, `--save-csv`, `--save-xlsx`, and `--save-summary-txt` can
all be combined and used alongside `--dot` and redirection in the same run.

## Automated Paper-Ready Analysis

Previously, producing the paper's benchmark tables meant running
`test_slicer.py` by hand once per `benchmarks/<category>/` directory,
copy-pasting the console output into `results/experimental-result.xlsx`
(one sheet per category), then running `get_statistics.py`. That whole
pipeline is now automated by **`run_all_benchmarks.py`**.

`run_all_benchmarks.py` discovers every subdirectory of `benchmarks/`
that contains `.prob` files (e.g. `prodigy`, `real-world`, `literature`,
`contrived`, `misc-variant`, `noisy_or`, `medical`), runs the slicer on
every benchmark in each directory under all three variants, and writes
each directory's results to its own sheet (named after the directory) in
`results/experimental-result.xlsx` via `test_slicer.save_results_xlsx()`.

```bash
# Run every benchmarks/ subdirectory, all 3 variants (no Monte Carlo eval)
python test/run_all_benchmarks.py

# Include Monte Carlo correctness evaluation (needed for NS/NI/NIDS
# correctness columns and pass-rate stats — slower)
python test/run_all_benchmarks.py --evaluate --eval-runs 2000

# Only specific directories (space-separated; a single quoted/comma-
# separated string like "prodigy real-world" is also tolerated)
python test/run_all_benchmarks.py --dirs prodigy real-world

# Custom output workbook
python test/run_all_benchmarks.py --out results/my-results.xlsx

# Run get_statistics.py automatically once all directories finish
python test/run_all_benchmarks.py --evaluate --analyze

# Print a --summary table per directory as it completes, and save all
# of them (one section per directory) to a text file
python test/run_all_benchmarks.py --evaluate --summary \
    --save-summary-txt results/run_summary.txt

# Save get_statistics.py's full --analyze output to a custom text file
# (default: results/statistics_summary.txt)
python test/run_all_benchmarks.py --evaluate --analyze \
    --analysis-txt results/my_stats.txt
```

`run_all_benchmarks.py` never deletes anything: `save_results_xlsx()` only
replaces the sheet being (re)written — other sheets in an existing
workbook, and unrelated files elsewhere in the project, are untouched.

Once the workbook is written, `get_statistics.py` and `data_analysis.py`
auto-discover its sheets — there's no hardcoded sheet→category mapping
to update when you add or remove a `benchmarks/` subdirectory. Each
sheet name is used directly as the category label in the output
(including the auto-generated LaTeX table, whose column count and
headers now scale to however many sheets exist).

```bash
# Full paper-ready statistics + LaTeX table rows (also saved to
# results/statistics_summary.txt by default)
python test/get_statistics.py

# Quick headline numbers + summary sentences (also saved to
# results/data_analysis_summary.txt by default)
python test/data_analysis.py

# Point either at a different workbook / cleaned-CSV path, or a subset of sheets
python test/get_statistics.py --xlsx results/my-results.xlsx --csv my_data.csv
python test/get_statistics.py --sheets prodigy real-world

# Disable the text-file mirror (console output only)
python test/get_statistics.py --out-txt ""
```

Both `get_statistics.py` and `data_analysis.py` mirror their entire console
output to a text file by default (`--out-txt`, disabled by passing `""`) —
the "summary results" are never console-only.

### Memory analysis in `get_statistics.py`

If the workbook's sheets include the `RSS_KB`/`PY_KB` columns written by
`save_results_xlsx()`, `get_statistics.py` adds three sections using
**both** memory metrics (see [Memory Usage Tracking](#memory-usage-tracking)
for what each one measures):

- **§0c** — peak and average RSS/Python-allocation per slicing variant,
  across the whole workbook.
- **§0d** — the same, broken down per category × variant.
- **§0e** — the raw RSS/Python-allocation value per individual
  benchmark × variant row.

End-to-end, the whole pipeline — run every benchmark directory, save
results, save a run summary, and produce the paper-ready analysis
(with memory stats) — is now just:

```bash
python test/run_all_benchmarks.py --evaluate --summary --analyze
```

## Rendering `.dot` Output

After running with `--dot`:

```bash
dot -Tpng dot_output/<benchmark>_<variant>_cfg.dot -o cfg.png
dot -Tpng dot_output/<benchmark>_<variant>_pdg.dot -o pdg.png
```

## Notes

- Exceptions during a single benchmark/variant run are caught, logged with a
  traceback, and do not stop the rest of the run.
- `--evaluate` checks the NS (nontermination-sensitive), NI
  (nontermination-insensitive), and NIDS (nontermination-insensitive,
  distribution-sensitive) correctness properties by comparing Monte Carlo
  distributions of the original vs. sliced program.
