import argparse
import sys
import pandas as pd
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================

_ap = argparse.ArgumentParser(
    description='Quick headline statistics and paper summary sentences '
                 'from an Excel workbook of slicer results (one sheet '
                 'per benchmark category, as produced by test_slicer.py '
                 '--save-xlsx or run_all_benchmarks.py).'
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
_ap.add_argument('--out-txt', default='results/data_analysis_summary.txt',
                  metavar='FILE',
                  help='Also save this script\'s full console output to a '
                       'text file (default: results/data_analysis_summary.txt). '
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
# typically one sheet per benchmarks/<name>/ directory, written
# automatically by test_slicer.py --save-xlsx or run_all_benchmarks.py.
# The sheet name is used directly as the Category label.

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
    category = sheet
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

for col in ["slice_size", "orig_size", "while_loops", "time_ms"]:
    combined[col] = pd.to_numeric(combined[col], errors="coerce")

combined = combined.dropna(subset=["orig_size", "slice_size", "Variant"])

# Compute per-row reduction
combined["reduction"] = 1.0 - combined["slice_size"] / combined["orig_size"]

combined.to_csv(CSV_FILE, index=False)
print(f"\n✔ Saved {len(combined)} rows to {CSV_FILE}\n")

# ==========================================
# PART 2: KEY NUMBERS FOR THE PAPER
# ==========================================

SEP = "=" * 70

def pct(x: float) -> str:
    return f"{x * 100:.1f}%"

def ms(x: float) -> str:
    return f"{x:.2f} ms"

# ------------------------------------------------------------------
# 1. Overall mean reduction per variant (across ALL benchmarks)
# ------------------------------------------------------------------
print(SEP)
print("1. OVERALL MEAN REDUCTION PER VARIANT")
print(SEP)
overall = (
    combined.groupby("Variant")["reduction"]
    .agg(
        n          = "count",
        mean       = "mean",
        max        = "max",
        min        = "min",
    )
    .reindex(VARIANTS)
    .reset_index()
)
for _, row in overall.iterrows():
    print(f"  {row['Variant']:<6}  n={int(row['n']):>4}  "
          f"mean={pct(row['mean'])}  "
          f"max={pct(row['max'])}  "
          f"min={pct(row['min'])}")

# ------------------------------------------------------------------
# 2. Mean reduction per variant per category
# ------------------------------------------------------------------
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
    for _, row in sub.iterrows():
        print(f"    {row['Variant']:<6}  n={int(row['n']):>4}  "
              f"mean={pct(row['mean'])}  "
              f"max={pct(row['max'])}")

# ------------------------------------------------------------------
# 3. Top-N reductions across ALL benchmarks
# ------------------------------------------------------------------
print(f"\n{SEP}")
print("3. TOP 10 REDUCTIONS ACROSS ALL BENCHMARKS")
print(SEP)
top = (
    combined[["Benchmark", "Category", "Variant", "orig_size",
              "slice_size", "reduction"]]
    .sort_values("reduction", ascending=False)
    .head(10)
    .copy()
)
top["reduction_pct"] = top["reduction"].map(pct)
print(top[["Benchmark", "Category", "Variant",
           "orig_size", "slice_size", "reduction_pct"]]
      .to_string(index=False))

# ------------------------------------------------------------------
# 4. Programs where variants differ
# ------------------------------------------------------------------
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
# ------------------------------------------------------------------
print(f"\n{SEP}")
print("5. MEAN ANALYSIS TIME BY CATEGORY AND VARIANT (excl. > 10,000 ms)")
print(SEP)
time_df = combined[combined["time_ms"] <= 10_000]
time_stats = (
    time_df.groupby(["Category", "Variant"])["time_ms"]
    .agg(mean="mean", median="median", max="max", total="sum")
    .reset_index()
)
for cat in SHEET_CATEGORY.values():
    print(f"\n  [{cat}]")
    sub = time_stats[time_stats["Category"] == cat]
    for _, row in sub.iterrows():
        print(f"    {row['Variant']:<6}  "
              f"mean={ms(row['mean'])}  "
              f"median={ms(row['median'])}  "
              f"max={ms(row['max'])}  "
              f"total={ms(row['total'])}")

# ------------------------------------------------------------------
# 6. Outlier benchmarks (> 10,000 ms) reported separately
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 7. Summary sentence for the paper
# ------------------------------------------------------------------
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

print(f"\n  Suggested text:")
ns_mean   = overall.loc[overall["Variant"]=="NS",   "mean"].values[0]
nids_mean = overall.loc[overall["Variant"]=="NIDS", "mean"].values[0]
ni_mean   = overall.loc[overall["Variant"]=="NI",   "mean"].values[0]
ns_max    = overall.loc[overall["Variant"]=="NS",   "max"].values[0]
print(f"""
  Across the full suite of {len(pivot)} programs, the NS variant
  achieves a mean slice-size reduction of {pct(ns_mean)}, NIDS
  achieves {pct(nids_mean)}, and NI achieves {pct(ni_mean)}.
  The maximum reduction is {pct(ns_max)}, achieved by
  '{best['Benchmark']}'.
""")

# ==========================================
# Cleanup: stop mirroring stdout to the text file
# ==========================================
if _txt_file is not None:
    sys.stdout = sys.__stdout__
    _txt_file.close()
    print(f"\n[INFO] Full output saved to {_args.out_txt}")