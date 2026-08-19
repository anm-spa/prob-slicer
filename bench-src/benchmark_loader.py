"""
benchmark_loader.py
===================
Loads benchmark programs from the benchmarks/ directory.

Each .prob file contains:
  - Metadata lines starting with  // @METADATA:key = value
  - Program source code (everything else)

Lives in bench-src/ — it is a benchmark-authoring utility, not part of
the core slicing library (src/prob_slicer/).
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Any

# benchmarks/ lives at the repo root, one level up from bench-src/
REPO_ROOT      = Path(__file__).resolve().parent.parent
BENCHMARKS_ROOT = REPO_ROOT / 'benchmarks'
BENCHMARKS_DIR = BENCHMARKS_ROOT / 'real-world'

# Regex to match metadata lines: // @METADATA:key = value
_META_RE = re.compile(
    r'^\s*//\s*@METADATA:(\w+)\s*=\s*(.*)$'
)

# Metadata keys that are required in every benchmark file
_REQUIRED_KEYS = {'name', 'description', 'reference', 'criterion', 'expected'}

# Metadata keys that are optional
# 'license' — the benchmark's own license, if known (e.g. 'Apache-2.0',
# 'MIT', or 'Unknown' when the source is cited but its license isn't).
# This is independent of ProbSlicer's own MIT license — see the
# "License" section of README.md. Not required, since most existing
# benchmark files predate this field and haven't been back-filled yet.
_OPTIONAL_KEYS = {'tags', 'license'}


def _parse_file(path: Path) -> dict[str, Any]:
    """
    Parse a single .prob file into a benchmark dict.

    Returns dict with keys:
        name, description, reference, criterion,
        expected, tags, source, path
    """
    meta:         dict[str, str] = {}
    source_lines: list[str]      = []

    with open(path, encoding='utf-8') as f:
        for line in f:
            m = _META_RE.match(line)
            if m:
                key   = m.group(1).strip()
                value = m.group(2).strip()
                if key in meta:
                    # Multi-line metadata value: append with space
                    meta[key] = meta[key] + ' ' + value
                else:
                    meta[key] = value
            else:
                # Keep non-metadata lines as source
                # (pure // comments without @METADATA are dropped)
                stripped = line.strip()
                if not stripped.startswith('//'):
                    source_lines.append(line)

    source = ''.join(source_lines).strip()

    # Validate required keys
    missing = _REQUIRED_KEYS - meta.keys()
    if missing:
        raise ValueError(
            f"Benchmark file {path.name} is missing "
            f"required metadata keys: {missing}"
        )

    if not source:
        raise ValueError(
            f"Benchmark file {path.name} has no program source."
        )

    # Parse tags as a list
    tags_raw = meta.get('tags', '')
    tags = [t.strip() for t in tags_raw.split(',') if t.strip()]

    return {
        'name':        meta['name'],
        'description': meta['description'],
        'reference':   meta['reference'],
        'criterion':   meta['criterion'],
        'expected':    meta['expected'],
        'tags':        tags,
        # Optional; None when not yet annotated (most existing files).
        'license':     meta.get('license'),
        'source':      source,
        'path':        str(path),
    }


def load_benchmarks(
    tags:  list[str] | None = None,
    names: list[str] | None = None,
    benchmarks_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Load all .prob files from a benchmarks directory.

    Args:
        tags:  if given, return only benchmarks whose tags
               intersect this list.
        names: if given, return only benchmarks with these names.
        benchmarks_dir: directory to load .prob files from.
               Defaults to BENCHMARKS_DIR (benchmarks/real-world/)
               when not given.

    Returns:
        List of benchmark dicts, sorted by name.
    """
    bench_dir = Path(benchmarks_dir) if benchmarks_dir else BENCHMARKS_DIR

    if not bench_dir.exists():
        raise FileNotFoundError(
            f"Benchmarks directory not found: {bench_dir}\n"
            f"Expected location: {bench_dir.resolve()}"
        )

    benchmarks = []
    errors     = []

    for path in sorted(bench_dir.glob('*.prob')):
        try:
            print(f"Loading benchmark: {path.name}")
            b = _parse_file(path)
            benchmarks.append(b)
        except Exception as e:
            print(f"Failed to load benchmark: {path.name}")
            errors.append(f"  {path.name}: {e}")

    if errors:
        print(f"[WARNING] Failed to load {len(errors)} benchmark(s):")
        for err in errors:
            print(err)

    # Filter by name
    if names:
        name_set    = set(names)
        benchmarks  = [b for b in benchmarks if b['name'] in name_set]

    # Filter by tags
    if tags:
        tag_set    = set(tags)
        benchmarks = [
            b for b in benchmarks
            if tag_set & set(b['tags'])
        ]

    return benchmarks


def load_benchmarks_any_dir(
    names: list[str] | None = None,
    tags:  list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Search every category subdirectory under benchmarks/ (real-world/,
    prodigy/, contrived/, literature/, ...) for benchmarks matching
    `names` and/or `tags`, and return the combined results.

    Used as a fallback by --bench/--tag when no --benchdir is given and
    the benchmark isn't found in the default benchmarks/real-world/
    directory — since --list now shows benchmarks from every category,
    --bench should be able to find them there too without requiring
    the caller to know which subdirectory a benchmark lives in.

    If the same benchmark name exists in more than one directory, all
    matches are returned (each will carry its own 'path').
    """
    found: list[dict[str, Any]] = []
    for d in discover_benchmark_dirs():
        found.extend(load_benchmarks(tags=tags, names=names, benchmarks_dir=d))
    return found


def discover_benchmark_dirs(root: str | Path | None = None) -> list[Path]:
    """
    Return every immediate subdirectory of benchmarks/ that contains at
    least one .prob file (e.g. real-world/, prodigy/, contrived/, ...),
    sorted by name.
    """
    base = Path(root) if root else BENCHMARKS_ROOT
    if not base.exists():
        return []
    return sorted(
        d for d in base.iterdir()
        if d.is_dir() and any(d.glob('*.prob'))
    )


def _print_benchmark_table(benchmarks: list[dict[str, Any]]) -> None:
    print(f"  {'Name':<32} {'Criterion':<12} {'Tags'}")
    print(f"{'─'*72}")
    for b in benchmarks:
        tags = ', '.join(b['tags'][:3])
        if len(b['tags']) > 3:
            tags += ', ...'
        print(f"  {b['name']:<32} {b['criterion']:<12} {tags}")


def list_benchmarks(benchmarks_dir: str | Path | None = None) -> None:
    """
    Print a summary table of available benchmarks.

    If `benchmarks_dir` is given explicitly, only that single directory
    is listed (previous behaviour). If not given, every category
    subdirectory under benchmarks/ (real-world/, prodigy/, contrived/,
    literature/, ...) is discovered and listed, with a per-directory
    and grand total count.
    """
    if benchmarks_dir:
        bench_dir  = Path(benchmarks_dir)
        benchmarks = load_benchmarks(benchmarks_dir=bench_dir)
        print(f"\n{'='*72}")
        _print_benchmark_table(benchmarks)
        print(f"{'='*72}")
        print(f"  Total: {len(benchmarks)} benchmarks")
        print(f"  Location: {bench_dir.resolve()}")
        print()
        return

    dirs = discover_benchmark_dirs()
    if not dirs:
        print(f"\n[WARNING] No benchmark directories with .prob files found "
              f"under {BENCHMARKS_ROOT.resolve()}")
        return

    grand_total = 0
    print(f"\n{'='*72}")
    for d in dirs:
        benchmarks = load_benchmarks(benchmarks_dir=d)
        grand_total += len(benchmarks)
        print(f"  {d.name}/  ({len(benchmarks)} benchmarks)")
        print(f"{'─'*72}")
        _print_benchmark_table(benchmarks)
        print(f"{'='*72}")
    print(f"  Grand total: {grand_total} benchmarks across {len(dirs)} directories")
    print(f"  Location: {BENCHMARKS_ROOT.resolve()}")
    print()