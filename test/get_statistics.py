import argparse
import contextlib
import sys
import pandas as pd
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================

_ap = argparse.ArgumentParser(
    description='Compute paper-ready statistics from an Excel workbook '
                 'of slicer results (one sheet per benchmark category, '
                 'as produced by test_slicer.py --save-xlsx or '
                 'run_all_benchmarks.py).'
)
_ap.add_argument('--xlsx', default='results/experimental-result.xlsx',
                  metavar='FILE',
                  help='Excel workbook to read (default: '
                       'results/experimental-result.xlsx)')
_ap.add_argument('--csv', default='cleaned_benchmark_data.csv',
                  metavar='FILE',
                  help='Where to save the cleaned/combined CSV (default: '
                       'cleaned_benchmark_data.csv)')
_ap.add_argument('--sheets', nargs='+', default=None, metavar='NAME',
                  help='Only load these sheet names (default: every '
                       'sheet in the workbook)')
_ap.add_argument('--out-txt', default='results/statistics_summary.txt',
                  metavar='FILE',
                  help='Also save this script\'s full console output to a '
                       'text file (default: results/statistics_summary.txt). '
                       'Pass an empty string ("") to disable.')
_args = _ap.parse_args()

EXCEL_FILE = Path(_args.xlsx)
CSV_FILE   = Path(_args.csv)

VARIANTS = ["NS", "NIDS", "NI"]


class _Tee:
    """Write to multiple streams at once (used to mirror stdout to a file)."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


_txt_file = None
if _args.out_txt:
    _txt_path = Path(_args.out_txt)
    _txt_path.parent.mkdir(parents=True, exist_ok=True)
    _txt_file = open(_txt_path, 'w')
    sys.stdout = _Tee(sys.__stdout__, _txt_file)

# ==========================================
# PART 1: LOAD AND CLEAN
# ==========================================
#
# Each sheet in the workbook is treated as one benchmark category —
# typically one sheet per benchmarks/<name>/ directory (e.g. 'prodigy',
# 'real-world', 'literature', 'contrived', ...), written automatically by
# test_slicer.py --save-xlsx or run_all_benchmarks.py. The sheet name is
# used directly as the Category label, so no hardcoded sheet->category
# mapping is needed here — add/remove benchmark directories freely and
# rerun run_all_benchmarks.py; this script picks up whatever sheets exist.

def load_sheet(path: Path, sheet: str, category: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, header=0)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "slice":     "slice_size",
        "orig":      "orig_size",
        "orig ":     "orig_size",
        "While":     "while_loops",
        "Reduction": "reduction_raw",
        "ms":        "time_ms",
        "RSS_KB":    "rss_kb",
        "PY_KB":     "py_kb",
    })
    df = df[df["Variant"].notna()].copy()
    df["Variant"] = df["Variant"].astype(str).str.strip().str.upper()
    df = df[df["Variant"].isin(VARIANTS)]
    df["Category"] = category
    return df


if not EXCEL_FILE.exists():
    raise FileNotFoundError(
        f"Workbook not found: {EXCEL_FILE}\n"
        f"Generate it with: python run_all_benchmarks.py --evaluate"
    )

sheet_names = _args.sheets or pd.ExcelFile(EXCEL_FILE).sheet_names

all_frames = []
for sheet in sheet_names:
    category = sheet  # sheet name doubles as the category label
    try:
        frame = load_sheet(EXCEL_FILE, sheet, category)
        all_frames.append(frame)
        print(f"  ✔ Loaded '{sheet}' ({category}): {len(frame)} rows")
    except Exception as exc:
        print(f"  ✗ Skipped '{sheet}': {exc}")

if not all_frames:
    raise RuntimeError("No data loaded.")

SHEET_CATEGORY = {s: s for s in sheet_names}

combined = pd.concat(all_frames, ignore_index=True)

for col in ["slice_size", "orig_size", "while_loops", "time_ms",
            "rss_kb", "py_kb"]:
    if col in combined.columns:
        combined[col] = pd.to_numeric(combined[col], errors="coerce")

combined = combined.dropna(subset=["orig_size", "slice_size", "Variant"])

HAS_MEMORY = "rss_kb" in combined.columns and "py_kb" in combined.columns

# Compute per-row reduction
combined["reduction"] = 1.0 - combined["slice_size"] / combined["orig_size"]

combined.to_csv(CSV_FILE, index=False)
print(f"\n✔ Saved {len(combined)} rows to {CSV_FILE}\n")

# ==========================================
# HELPERS
# ==========================================

SEP = "=" * 70

def pct(x: float) -> str:
    return f"{x * 100:.1f}%"

def ms(x: float) -> str:
    return f"{x:.2f} ms"

# ==========================================
# PART 2: BENCHMARK SUITE STATISTICS
# ==========================================

print(SEP)
print("0. BENCHMARK SUITE STATISTICS")
print(SEP)

# Use NS variant only to get one row per benchmark
ns_df = combined[combined["Variant"] == "NS"].copy()

for cat in SHEET_CATEGORY.values():
    cat_df = ns_df[ns_df["Category"] == cat]

    # Unique benchmarks
    n_programs = cat_df["Benchmark"].nunique()

    # Program sizes (orig_size)
    sizes       = cat_df["orig_size"]
    size_min    = sizes.min()
    size_max    = sizes.max()
    size_mean   = sizes.mean()
    size_total  = sizes.sum()

    # While loops
    loops        = cat_df["while_loops"]
    total_loops  = int(loops.sum())
    n_with_loops = int((loops > 0).sum())
    n_loop_free  = int((loops == 0).sum())
    max_loops    = int(loops.max())

    print(f"\n  [{cat}]")
    print(f"    Programs                  : {n_programs}")
    print(f"    Min size (statements)     : {int(size_min)}")
    print(f"    Max size (statements)     : {int(size_max)}")
    print(f"    Mean size (statements)    : {size_mean:.1f}")
    print(f"    Total statements          : {int(size_total)}")
    print(f"    Programs with while-loops : {n_with_loops}")
    print(f"    Loop-free programs        : {n_loop_free}")
    print(f"    Total while-loops         : {total_loops}")
    print(f"    Max loops in one program  : {max_loops}")

# Overall
print(f"\n  [ALL CATEGORIES COMBINED]")
n_total       = ns_df["Benchmark"].nunique()
total_stmts   = int(ns_df["orig_size"].sum())
total_loops_all = int(ns_df["while_loops"].sum())
n_with_loops_all = int((ns_df["while_loops"] > 0).sum())
n_loop_free_all  = int((ns_df["while_loops"] == 0).sum())
print(f"    Total programs            : {n_total}")
print(f"    Total statements          : {total_stmts}")
print(f"    Programs with while-loops : {n_with_loops_all}")
print(f"    Loop-free programs        : {n_loop_free_all}")
print(f"    Total while-loops         : {total_loops_all}")

# ==========================================
# PART 3: CORRECTNESS STATISTICS
# ==========================================

print(f"\n{SEP}")
print("0b. CORRECTNESS PASS RATES BY CATEGORY AND VARIANT")
print(SEP)

# Correctness columns: NS, NI, NIDS contain ✓ or ✗
for correct_col, variant_label in [("NS", "NS"), ("NI", "NI"), ("NIDS", "NIDS")]:
    if correct_col not in combined.columns:
        continue
    for cat in SHEET_CATEGORY.values():
        sub = combined[
            (combined["Category"] == cat) &
            (combined["Variant"]  == variant_label)
        ]
        total  = len(sub)
        passed = (sub[correct_col].astype(str).str.strip() == "✓").sum()
        print(f"  [{cat}] {variant_label:<6}: {passed}/{total} "
              f"({pct(passed/total) if total > 0 else 'N/A'})")

# ==========================================
# PART 3b: MEMORY USAGE STATISTICS
# ==========================================
#
# Two metrics per row (see test_slicer.py):
#   rss_kb — process-wide peak RSS (resource.getrusage), the actual
#            OS-level memory-usage metric.
#   py_kb  — tracemalloc peak Python-level allocation, a supplementary/
#            finer-grained (but incomplete) breakdown.

if HAS_MEMORY:
    print(f"\n{SEP}")
    print("0c. MEMORY USAGE (PEAK / AVERAGE) BY VARIANT")
    print(SEP)
    mem_by_variant = (
        combined.groupby("Variant")[["rss_kb", "py_kb"]]
        .agg(["max", "mean", "median"])
        .reindex(VARIANTS)
    )
    for v in VARIANTS:
        if v not in mem_by_variant.index or mem_by_variant.loc[v].isna().all():
            continue
        row = mem_by_variant.loc[v]
        print(f"  {v:<6}  "
              f"RSS: peak={row[('rss_kb','max')]:>10.1f} KB  "
              f"avg={row[('rss_kb','mean')]:>10.1f} KB  "
              f"median={row[('rss_kb','median')]:>10.1f} KB   |  "
              f"Py alloc: peak={row[('py_kb','max')]:>10.1f} KB  "
              f"avg={row[('py_kb','mean')]:>10.1f} KB")

    print(f"\n{SEP}")
    print("0d. MEMORY USAGE (PEAK / AVERAGE) BY CATEGORY AND VARIANT")
    print(SEP)
    mem_by_cat_variant = (
        combined.groupby(["Category", "Variant"])[["rss_kb", "py_kb"]]
        .agg(["max", "mean", "median"])
        .reindex(
            pd.MultiIndex.from_product(
                [list(SHEET_CATEGORY.values()), VARIANTS],
                names=["Category", "Variant"]
            )
        )
    )
    for cat in SHEET_CATEGORY.values():
        print(f"\n  [{cat}]")
        printed_any = False
        for v in VARIANTS:
            key = (cat, v)
            if key not in mem_by_cat_variant.index or mem_by_cat_variant.loc[key].isna().all():
                continue
            printed_any = True
            row = mem_by_cat_variant.loc[key]
            print(f"    {v:<6}  "
                  f"RSS: peak={row[('rss_kb','max')]:>10.1f} KB  "
                  f"avg={row[('rss_kb','mean')]:>10.1f} KB  "
                  f"median={row[('rss_kb','median')]:>10.1f} KB   |  "
                  f"Py alloc: peak={row[('py_kb','max')]:>10.1f} KB  "
                  f"avg={row[('py_kb','mean')]:>10.1f} KB")
        if not printed_any:
            print(f"    (no data — this category wasn't run in this workbook)")

    print(f"\n{SEP}")
    print("0e. MEMORY USAGE (PEAK / AVERAGE) PER BENCHMARK x VARIANT")
    print(SEP)
    mem_per_bench = (
        combined[["Benchmark", "Category", "Variant", "rss_kb", "py_kb"]]
        .sort_values(["Category", "Benchmark", "Variant"])
    )
    print(mem_per_bench.to_string(index=False))
else:
    print(f"\n{SEP}")
    print("0c. MEMORY USAGE STATISTICS")
    print(SEP)
    print("  Skipped: workbook has no RSS_KB/PY_KB columns "
          "(re-run with a version of test_slicer.py that records "
          "memory usage, e.g. via run_all_benchmarks.py).")

# ==========================================
# PART 4: KEY NUMBERS FOR THE PAPER
# ==========================================

print(f"\n{SEP}")
print("1. OVERALL MEAN REDUCTION PER VARIANT")
print(SEP)
overall = (
    combined.groupby("Variant")["reduction"]
    .agg(n="count", mean="mean", max="max", min="min")
    .reindex(VARIANTS)
    .reset_index()
)
for _, row in overall.iterrows():
    if pd.isna(row['n']) or row['n'] == 0:
        continue
    print(f"  {row['Variant']:<6}  n={int(row['n']):>4}  "
          f"mean={pct(row['mean'])}  "
          f"max={pct(row['max'])}  "
          f"min={pct(row['min'])}")

print(f"\n{SEP}")
print("2. MEAN REDUCTION BY CATEGORY AND VARIANT")
print(SEP)
by_cat = (
    combined.groupby(["Category", "Variant"])["reduction"]
    .agg(n="count", mean="mean", max="max")
    .reindex(
        pd.MultiIndex.from_product(
            [list(SHEET_CATEGORY.values()), VARIANTS],
            names=["Category", "Variant"]
        )
    )
    .reset_index()
)
for cat in SHEET_CATEGORY.values():
    print(f"\n  [{cat}]")
    sub = by_cat[by_cat["Category"] == cat]
    printed_any = False
    for _, row in sub.iterrows():
        if pd.isna(row['n']) or row['n'] == 0:
            continue
        printed_any = True
        print(f"    {row['Variant']:<6}  n={int(row['n']):>4}  "
              f"mean={pct(row['mean'])}  "
              f"max={pct(row['max'])}")
    if not printed_any:
        print(f"    (no data — this category wasn't run in this workbook)")

print(f"\n{SEP}")
print("3. TOP 10 REDUCTIONS ACROSS ALL BENCHMARKS")
print(SEP)
top = (
    combined[["Benchmark", "Category", "Variant",
              "orig_size", "slice_size", "reduction"]]
    .sort_values("reduction", ascending=False)
    .head(10)
    .copy()
)
top["reduction_pct"] = top["reduction"].map(pct)
print(top[["Benchmark", "Category", "Variant",
           "orig_size", "slice_size", "reduction_pct"]]
      .to_string(index=False))

print(f"\n{SEP}")
print("4. PROGRAMS WHERE SLICE SIZES DIFFER ACROSS VARIANTS")
print(SEP)
pivot = combined.pivot_table(
    index   = ["Benchmark", "Category"],
    columns = "Variant",
    values  = ["slice_size", "reduction"],
    aggfunc = "first",
)
pivot.columns = [f"{v}_{c}" for v, c in pivot.columns]
pivot = pivot.reset_index()

size_cols = sorted([c for c in pivot.columns if c.startswith("slice_size_")])
pivot["all_same"] = pivot[size_cols].nunique(axis=1) == 1
differ = pivot[~pivot["all_same"]].copy()

red_cols = sorted([c for c in pivot.columns if c.startswith("reduction_")])
for col in red_cols:
    differ[col] = differ[col].map(pct)

print(f"\n  {len(differ)} programs differ / "
      f"{len(pivot)} total = "
      f"{pct(len(differ)/len(pivot))} differ\n")
print(differ[["Benchmark", "Category"] + size_cols + red_cols]
      .to_string(index=False))

# ------------------------------------------------------------------
# 5. Computation time (excluding large outliers > 10,000 ms)
#    Reports both mean ± std and median + IQR
# ------------------------------------------------------------------
print(f"\n{SEP}")
print("5. ANALYSIS TIME BY CATEGORY AND VARIANT (excl. > 10,000 ms)")
print(SEP)
time_df = combined[combined["time_ms"] <= 10_000]

time_stats = (
    time_df.groupby(["Category", "Variant"])["time_ms"]
    .agg(
        n      = "count",
        mean   = "mean",
        std    = "std",
        median = "median",
        q1     = lambda x: x.quantile(0.25),
        q3     = lambda x: x.quantile(0.75),
        max    = "max",
    )
    .reset_index()
)

for cat in SHEET_CATEGORY.values():
    print(f"\n  [{cat}]")
    sub = time_stats[time_stats["Category"] == cat]
    printed_any = False
    for _, row in sub.iterrows():
        if pd.isna(row['n']) or row['n'] == 0:
            continue
        printed_any = True
        print(f"    {row['Variant']:<6}  "
              f"mean={row['mean']:.2f}±{row['std']:.2f} ms  |  "
              f"median={row['median']:.2f} ms "
              f"[IQR {row['q1']:.2f}-{row['q3']:.2f}]  |  "
              f"max={row['max']:.2f} ms  (n={int(row['n'])})")
    if not printed_any:
        print(f"    (no data — this category wasn't run in this workbook)")

# Overall (across all categories combined), excluding outliers
print(f"\n  [OVERALL, excl. outliers]")
overall_time = (
    time_df.groupby("Variant")["time_ms"]
    .agg(
        n      = "count",
        mean   = "mean",
        std    = "std",
        median = "median",
        q1     = lambda x: x.quantile(0.25),
        q3     = lambda x: x.quantile(0.75),
        max    = "max",
    )
    .reindex(VARIANTS)
    .reset_index()
)
for _, row in overall_time.iterrows():
    if pd.isna(row['n']) or row['n'] == 0:
        continue
    print(f"    {row['Variant']:<6}  "
          f"mean={row['mean']:.2f}±{row['std']:.2f} ms  |  "
          f"median={row['median']:.2f} ms "
          f"[IQR {row['q1']:.2f}-{row['q3']:.2f}]  |  "
          f"max={row['max']:.2f} ms  (n={int(row['n'])})")


print(f"\n{SEP}")
print("6. LARGE BENCHMARKS (time > 10,000 ms) — REPORTED SEPARATELY")
print(SEP)
outliers = combined[combined["time_ms"] > 10_000][
    ["Benchmark", "Category", "Variant", "orig_size",
     "slice_size", "reduction", "time_ms"]
].copy()
outliers["reduction"] = outliers["reduction"].map(pct)
outliers["time_ms"]   = outliers["time_ms"].map(lambda x: f"{x:.0f} ms")
if outliers.empty:
    print("  None found.")
else:
    print(outliers.to_string(index=False))

print(f"\n{SEP}")
print("7. PAPER SUMMARY SENTENCES")
print(SEP)
for _, row in overall.iterrows():
    print(f"  {row['Variant']}: mean reduction = {pct(row['mean'])}, "
          f"max = {pct(row['max'])}")

best = combined.loc[combined["reduction"].idxmax()]
print(f"\n  Max reduction overall: {pct(best['reduction'])} "
      f"by '{best['Benchmark']}' "
      f"(variant={best['Variant']}, "
      f"orig={int(best['orig_size'])}, "
      f"slice={int(best['slice_size'])})")

ns_mean   = overall.loc[overall["Variant"] == "NS",   "mean"].values[0]
nids_mean = overall.loc[overall["Variant"] == "NIDS", "mean"].values[0]
ni_mean   = overall.loc[overall["Variant"] == "NI",   "mean"].values[0]
ns_max    = overall.loc[overall["Variant"] == "NS",   "max"].values[0]

print(f"""
  Suggested text:

  Across the full suite of {len(pivot)} programs, the NS variant
  achieves a mean slice-size reduction of {pct(ns_mean)}, NIDS
  achieves {pct(nids_mean)}, and NI achieves {pct(ni_mean)}.
  The maximum reduction is {pct(ns_max)}, achieved by
  '{best['Benchmark']}' ({best['Category']}).
""")

# ==========================================
# PART 5: LATEX TABLE ROWS
# ==========================================

print(SEP)
print("8. LATEX TABLE ROWS FOR tab:benchmark-stats")
print(SEP)

rows = {}
for cat in SHEET_CATEGORY.values():
    cat_ns    = ns_df[ns_df["Category"] == cat]
    cat_all   = combined[combined["Category"] == cat]

    n_prog    = cat_ns["Benchmark"].nunique()
    s_min     = int(cat_ns["orig_size"].min())
    s_max     = int(cat_ns["orig_size"].max())
    s_mean    = cat_ns["orig_size"].mean()
    s_total   = int(cat_ns["orig_size"].sum())
    n_loops   = int(cat_ns["while_loops"].sum())
    n_with    = int((cat_ns["while_loops"] > 0).sum())
    n_free    = int((cat_ns["while_loops"] == 0).sum())
    max_loop  = int(cat_ns["while_loops"].max())

    # Correctness per variant
    correct = {}
    for v in VARIANTS:
        sub   = cat_all[cat_all["Variant"] == v]
        total = len(sub)
        if v in sub.columns:
            passed = (sub[v].astype(str).str.strip() == "✓").sum()
            correct[v] = f"{passed}/{total}"
        else:
            correct[v] = "N/A"

    # Mean reduction per variant
    mean_red = {}
    for v in VARIANTS:
        sub = cat_all[cat_all["Variant"] == v]["reduction"]
        mean_red[v] = pct(sub.mean()) if len(sub) > 0 else "N/A"

    rows[cat] = dict(
        n_prog=n_prog, s_min=s_min, s_max=s_max,
        s_mean=f"{s_mean:.1f}", s_total=s_total,
        n_with=n_with, n_free=n_free,
        n_loops=n_loops, max_loop=max_loop,
        correct=correct, mean_red=mean_red,
    )

# Print LaTeX rows
metrics = [
    ("Total programs",                  lambda r: str(r["n_prog"])),
    ("Min size (statements)",           lambda r: str(r["s_min"])),
    ("Max size (statements)",           lambda r: str(r["s_max"])),
    ("Mean size (statements)",          lambda r: r["s_mean"]),
    ("Total statements",                lambda r: f"{r['s_total']:,}"),
    ("Programs with while-loops",       lambda r: str(r["n_with"])),
    ("Loop-free programs",              lambda r: str(r["n_free"])),
    ("Total while-loops",               lambda r: str(r["n_loops"])),
    ("Max loops in one program",        lambda r: str(r["max_loop"])),
    ("Mean red.\ NS",                   lambda r: r["mean_red"]["NS"]),
    ("Mean red.\ NIDS",                 lambda r: r["mean_red"]["NIDS"]),
    ("Mean red.\ NI",                   lambda r: r["mean_red"]["NI"]),
    ("NS correctness",                  lambda r: r["correct"]["NS"]),
    ("NI correctness",                  lambda r: r["correct"]["NI"]),
    ("NIDS correctness",                lambda r: r["correct"]["NIDS"]),
]

cats = list(SHEET_CATEGORY.values())
col_spec = "l" + "r" * len(cats)
header   = " & ".join(r"\textbf{" + c + "}" for c in cats)
print(r"\begin{tabular}{" + col_spec + "}")
print(r"  \hline")
print(f"  \\textbf{{Metric}} & {header} \\\\")
print(r"  \hline")
for label, fn in metrics:
    vals = " & ".join(fn(rows[c]) for c in cats)
    print(f"  {label} & {vals} \\\\")
print(r"  \hline")
print(r"\end{tabular}")

# ==========================================
# Cleanup: stop mirroring stdout to the text file
# ==========================================
if _txt_file is not None:
    sys.stdout = sys.__stdout__
    _txt_file.close()
    print(f"\n[INFO] Full output saved to {_args.out_txt}")