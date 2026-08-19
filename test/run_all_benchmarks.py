"""
run_all_benchmarks.py
======================
Automates the "run the slicer over every benchmarks/<category>/
directory, collect results, paste into an Excel sheet per category"
workflow that used to be done by hand (running test_slicer.py once per
directory and copy-pasting console output into
results/experimental-result.xlsx).

For every subdirectory under benchmarks/ (each one is treated as a
category and becomes one sheet — e.g. 'prodigy', 'real-world',
'literature', 'contrived', 'misc-variant', 'noisy_or', 'medical'), this
script:

  1. Loads every .prob benchmark in that directory.
  2. Runs the slicer on each benchmark under all three slicing variants
     (or a single variant with --variant), optionally with Monte Carlo
     --evaluate.
  3. Writes the results as one sheet (named after the directory) into an
     Excel workbook (default: results/experimental-result.xlsx), using
     test_slicer.save_results_xlsx(). Existing sheets for other
     categories are preserved; only the sheet being (re)written is
     touched.

Once this finishes, the workbook is ready to feed straight into
get_statistics.py (or data_analysis.py) for the paper-ready analysis —
no manual copy-pasting required. Pass --analyze to run
get_statistics.py automatically as the last step.

Usage:
    # Run every benchmarks/ subdirectory, all 3 variants, no evaluation
    python run_all_benchmarks.py

    # Also run Monte Carlo correctness evaluation (slower)
    python run_all_benchmarks.py --evaluate --eval-runs 2000

    # Only specific directories
    python run_all_benchmarks.py --dirs prodigy real-world

    # Write to a custom workbook path
    python run_all_benchmarks.py --out results/my-results.xlsx

    # Run the analysis script automatically once done
    python run_all_benchmarks.py --evaluate --analyze
"""

from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

# Repo layout: this file lives in test/, benchmark_loader.py lives in
# bench-src/ (a sibling of test/), and prob_slicer lives in src/ (installed
# in editable mode via `pip install -e .` — see pyproject.toml). Add
# bench-src/ to sys.path so `from benchmark_loader import ...` below works
# without requiring bench-src/ to be pip-installed too.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'bench-src'))

from benchmark_loader import load_benchmarks
from prob_slicer.dependence import SliceVariant, set_debug
from test_slicer import (
    run_benchmark, run_benchmark_isolated,
    save_results_xlsx, print_summary, summary_to_text,
)

BENCHMARKS_ROOT = REPO_ROOT / 'benchmarks'


def discover_benchmark_dirs() -> list[Path]:
    """All immediate subdirectories of benchmarks/ that contain .prob files."""
    if not BENCHMARKS_ROOT.exists():
        return []
    return sorted(
        d for d in BENCHMARKS_ROOT.iterdir()
        if d.is_dir() and any(d.glob('*.prob'))
    )


def run_directory(
    bench_dir: Path,
    variants: list[SliceVariant],
    evaluate: bool,
    n_eval_runs: int,
    isolate_subprocess: bool = False,
) -> tuple[list[dict], list[tuple[str, str, str, str]]]:
    """
    Run every benchmark in bench_dir under every variant; return results.

    run_all_benchmarks.py runs every category directory and every
    benchmark/variant inside ONE shared Python process, so peak_rss_kb
    (a process-wide, monotonically non-decreasing high-water mark) is
    NOT independently attributable to any single benchmark/variant —
    it's contaminated by whatever ran earlier, possibly in an entirely
    different category. rss_delta_kb helps but is still order-dependent
    (it reads ~0 whenever an earlier run already set a higher peak).

    With isolate_subprocess=True, each benchmark x variant instead runs
    in its own fresh subprocess via run_benchmark_isolated(), so
    peak_rss_kb starts near-zero every time and becomes genuinely
    comparable across variants/benchmarks/categories — the only way to
    faithfully compute max/avg peak RSS per variant or per category.
    This is slower (one process launch per benchmark x variant) and
    needs enough RAM to run each benchmark standalone.
    """
    benchmarks = load_benchmarks(benchmarks_dir=bench_dir)
    results: list[dict] = []
    failed: list[tuple[str, str, str, str]] = []  # (name, variant, dir, error)
    for b in benchmarks:
        for variant in variants:
            print(f"  [RUN] {b['name']} / {variant.upper()}")
            try:
                if isolate_subprocess:
                    r = run_benchmark_isolated(
                        b, variant=variant, bench_dir=bench_dir,
                        evaluate=evaluate, n_eval_runs=n_eval_runs,
                    )
                    if r is None:
                        failed.append((
                            b['name'], variant, bench_dir.name,
                            'isolated subprocess failed',
                        ))
                        continue
                else:
                    r = run_benchmark(
                        b, variant=variant,
                        evaluate=evaluate, n_eval_runs=n_eval_runs,
                    )
                results.append(r)
            except Exception as e:
                print(f"  [ERROR] {b['name']!r} variant={variant} "
                      f"in {bench_dir.name}: {e}")
                failed.append((b['name'], variant, bench_dir.name, str(e)))
    return results, failed


if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description=(
            'Run the slicer over every benchmarks/<category>/ directory '
            'and write results to one Excel sheet per category, '
            'replacing the manual copy-paste workflow.'
        )
    )
    ap.add_argument(
        '--dirs', nargs='+', default=None, metavar='NAME',
        help='Only run these benchmark directory names, e.g. '
             '"prodigy real-world" (default: every subdirectory of '
             'benchmarks/ containing .prob files)'
    )
    ap.add_argument(
        '--out', default='results/experimental-result.xlsx', metavar='FILE',
        help='Excel workbook to write (default: '
             'results/experimental-result.xlsx)'
    )
    ap.add_argument(
        '--variant', default=None, choices=['ns', 'nids', 'ni'],
        help='Run a single slicing variant (default: all three)'
    )
    ap.add_argument(
        '--evaluate', action='store_true',
        help='Run Monte Carlo correctness evaluation for every '
             'benchmark/variant (slower; needed for the NS/NI/NIDS '
             'correctness columns and pass-rate stats)'
    )
    ap.add_argument(
        '--eval-runs', type=int, default=10_000,
        help='Number of Monte Carlo runs per evaluation (default: 10000)'
    )
    ap.add_argument(
        '--summary', action='store_true',
        help='Print a summary table per directory as it completes'
    )
    ap.add_argument(
        '--save-summary-txt', default='results/run_summary.txt',
        metavar='FILE',
        help='Save the per-directory --summary output (all directories, '
             'one section each) to a text file. Only written if '
             '--summary is also given. Default: results/run_summary.txt'
    )
    ap.add_argument(
        '--analyze', action='store_true',
        help='Also run get_statistics.py against the resulting workbook '
             'once all directories are done'
    )
    ap.add_argument(
        '--analysis-txt', default=None, metavar='FILE',
        help='With --analyze, also save get_statistics.py\'s full output '
             'to this text file (default: results/statistics_summary.txt)'
    )
    ap.add_argument(
        '--isolate-subprocess', action='store_true',
        help=(
            'Run each benchmark x variant in its own fresh subprocess '
            'instead of the shared process this script normally runs '
            'everything in. Required if you want peak_rss_kb to be '
            'faithfully comparable across variants/benchmarks/categories '
            '(e.g. to compute max/avg peak memory per variant or per '
            'category) — otherwise it is a process-wide, order-dependent '
            'high-water mark, not a per-benchmark measurement. Slower, '
            'and needs enough RAM to run your largest benchmark alone.'
        )
    )
    args = ap.parse_args()
    set_debug(False)

    if args.dirs:
        # Tolerate a single quoted/comma-separated string (e.g.
        # --dirs "prodigy real-world literature" or "prodigy,real-world")
        # in addition to the normal space-separated argparse form
        # (--dirs prodigy real-world literature).
        dir_names: list[str] = []
        for raw in args.dirs:
            dir_names.extend(part for part in raw.replace(',', ' ').split() if part)

        bench_dirs = [BENCHMARKS_ROOT / name for name in dir_names]
        missing = [d for d in bench_dirs if not d.exists()]
        if missing:
            print(f"[ERROR] Director{'y' if len(missing) == 1 else 'ies'} "
                  f"not found: {[str(m) for m in missing]}")
            available = discover_benchmark_dirs()
            print(f"        Available: {[d.name for d in available]}")
            sys.exit(1)
    else:
        bench_dirs = discover_benchmark_dirs()

    if not bench_dirs:
        print(f"[ERROR] No benchmark directories with .prob files found "
              f"under {BENCHMARKS_ROOT}")
        sys.exit(1)

    variants: list[SliceVariant] = (
        [args.variant] if args.variant else ['ns', 'nids', 'ni']
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Found {len(bench_dirs)} benchmark director"
          f"{'y' if len(bench_dirs) == 1 else 'ies'}: "
          f"{[d.name for d in bench_dirs]}")
    print(f"[INFO] Variants: {[v.upper() for v in variants]}  "
          f"Evaluate: {args.evaluate}  Output: {out_path}")

    summary_sections: list[str] = []
    all_failed: list[tuple[str, str, str, str]] = []

    for bench_dir in bench_dirs:
        sheet_name = bench_dir.name
        print(f"\n[INFO] === {sheet_name} ===")
        results, failed = run_directory(
            bench_dir, variants,
            evaluate=args.evaluate, n_eval_runs=args.eval_runs,
            isolate_subprocess=args.isolate_subprocess,
        )
        all_failed.extend(failed)
        if not results:
            print(f"[WARNING] No results for {sheet_name}, skipping sheet.")
            continue

        if args.summary:
            print_summary(results)
            header = f"{'#'*72}\n# {sheet_name}\n{'#'*72}"
            summary_sections.append(f"{header}\n{summary_to_text(results)}")

        save_results_xlsx(results, str(out_path), sheet_name)

    if all_failed:
        print(f"\n{'─'*72}")
        print(f"  FAILED ({len(all_failed)} benchmark x variant run(s)):")
        for name, variant, dir_name, err in all_failed:
            first_line = err.splitlines()[0] if err else ''
            print(f"    [{dir_name}] {name}  variant={variant.upper()}  "
                  f"— {first_line}")
        print(f"{'─'*72}")

    print(f"\n[DONE] Wrote {len(bench_dirs)} sheet(s) to {out_path} "
          f"({len(all_failed)} benchmark x variant run(s) failed)")

    if args.summary and summary_sections:
        summary_txt_path = Path(args.save_summary_txt)
        summary_txt_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_txt_path, 'w') as f:
            f.write("\n\n".join(summary_sections))
        print(f"[INFO] Summary saved to {summary_txt_path}")

    if args.analyze:
        analysis_txt_path = (
            Path(args.analysis_txt) if args.analysis_txt
            else Path('results/statistics_summary.txt')
        )
        get_statistics_path = Path(__file__).resolve().parent / 'get_statistics.py'
        print(f"\n[INFO] Running get_statistics.py against {out_path} "
              f"(saving to {analysis_txt_path}) ...")
        subprocess.run(
            [sys.executable, str(get_statistics_path),
             '--xlsx', str(out_path),
             '--out-txt', str(analysis_txt_path)],
            check=False,
        )
