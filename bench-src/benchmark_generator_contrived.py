from ast import stmt
import re
import sys
from pathlib import Path
import traceback

# benchmarks/ lives at the repo root, one level up from bench-src/
BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / 'benchmarks'
Contrived_OUT    = BENCHMARKS_DIR/'contrived'

def generate_variant_distinction_benchmarks() -> list[dict]:
    """
    Generate benchmarks specifically designed to show differences
    between NS, NIDS, and NI slice variants.
    """
    benchmarks = []

    # -------------------------------------------------------
    # 1. NS vs NI: nonterminating branch on independent variable
    # -------------------------------------------------------
    for n_irrelevant in [0, 5, 10, 20, 50]:
        lines = []
        lines.append("x :~ unif[1, 4];")
        lines.append("coin :~ unif[0, 1];")
        for i in range(n_irrelevant):
            lines.append(f"a{i} :~ unif[1, 4];")
        lines.append("if (coin = 1) then")
        lines.append("  while (true) do")
        lines.append("    skip;")
        lines.append("  end")
        lines.append("else")
        lines.append("  skip;")
        lines.append("end")
        lines.append("observe(x = 2);")
        lines.append("return x;")

        name   = f"ns_ni_gap_{n_irrelevant}irr"
        source = "\n".join(lines)
        benchmarks.append({
            'name':        name,
            'source':      source,
            'description': (
                f"NS vs NI gap: nonterminating branch + "
                f"{n_irrelevant} irrelevant variables. "
                f"NI removes coin+while, NS keeps them."
            ),
            'reference':   'generated/variant_distinction',
            'criterion':   'x',
            'expected':    'ns_size > ni_size',
            'tags':        ['generated', 'variant_distinction', 'ns_ni'],
        })

    # -------------------------------------------------------
    # 2. NIDS vs NI: observe-nontermination interaction
    # -------------------------------------------------------
    for n_obs in [1, 3, 5, 10]:
        lines = []
        lines.append("x :~ unif[1, 4];")
        for i in range(n_obs):
            lines.append(f"y{i} :~ unif[1, 4];")
            lines.append(f"observe(y{i} = 2);")
        lines.append("while (x = 4) do")
        lines.append("  skip;")
        lines.append("end")
        lines.append("observe(x = 2);")
        lines.append("return x;")

        name   = f"nids_ni_gap_{n_obs}obs"
        source = "\n".join(lines)
        benchmarks.append({
            'name':        name,
            'source':      source,
            'description': (
                f"NIDS vs NI gap: {n_obs} observe statements + "
                f"nonterminating while. "
                f"NI removes y_i observes, NIDS keeps them."
            ),
            'reference':   'generated/variant_distinction',
            'criterion':   'x',
            'expected':    'nids_size > ni_size',
            'tags':        ['generated', 'variant_distinction', 'nids_ni'],
        })

    # -------------------------------------------------------
    # 3. NS vs NIDS: nontermination sensitivity
    # -------------------------------------------------------
    for n_vars in [2, 5, 10]:
        lines = []
        lines.append("x :~ unif[1, 4];")
        lines.append("coin :~ unif[0, 1];")
        for i in range(n_vars):
            lines.append(f"y{i} :~ unif[1, 4];")
            lines.append(f"observe(y{i} = 2);")
        lines.append("if (coin = 1) then")
        lines.append("  while (true) do")
        lines.append("    skip;")
        lines.append("  end")
        lines.append("else")
        lines.append("  skip;")
        lines.append("end")
        lines.append("observe(x = 2);")
        lines.append("return x;")

        name   = f"ns_nids_gap_{n_vars}vars"
        source = "\n".join(lines)
        benchmarks.append({
            'name':        name,
            'source':      source,
            'description': (
                f"NS vs NIDS gap: coin-based nontermination + "
                f"{n_vars} observe variables. "
                f"NS keeps coin+while, NIDS may not."
            ),
            'reference':   'generated/variant_distinction',
            'criterion':   'x',
            'expected':    'ns_size >= nids_size >= ni_size',
            'tags':        ['generated', 'variant_distinction', 'ns_nids'],
        })

    # -------------------------------------------------------
    # 4. All three differ: combined scenario
    # -------------------------------------------------------
    for scale in [1, 3, 5, 10]:
        lines = []
        lines.append("x :~ unif[1, 4];")
        lines.append("coin :~ unif[0, 1];")
        # NIDS-relevant: observe variables
        for i in range(scale):
            lines.append(f"y{i} :~ unif[1, 4];")
            lines.append(f"observe(y{i} = 2);")
        # NS-relevant: nonterminating branch
        lines.append("if (coin = 1) then")
        lines.append("  while (true) do")
        lines.append("    skip;")
        lines.append("  end")
        lines.append("else")
        lines.append("  skip;")
        lines.append("end")
        # Irrelevant variables
        for i in range(scale * 2):
            lines.append(f"z{i} :~ unif[1, 4];")
        lines.append("observe(x = 2);")
        lines.append("return x;")

        name   = f"all_three_gap_scale{scale}"
        source = "\n".join(lines)
        benchmarks.append({
            'name':        name,
            'source':      source,
            'description': (
                f"All three variants differ (scale={scale}): "
                f"NS > NIDS > NI expected. "
                f"{scale} observe vars, {scale*2} irrelevant vars."
            ),
            'reference':   'generated/variant_distinction',
            'criterion':   'x',
            'expected':    'ns_size > nids_size > ni_size',
            'tags':        ['generated', 'variant_distinction', 'all_three'],
        })

    return benchmarks

def _format_benchmark_source(
    name:        str,
    description: str,
    reference:   str,
    criterion:   str,
    expected:    str,
    source:      str,
    tags:        str = "",
) -> str:
    lines = [
        f"// @METADATA:name        = {name}",
        f"// @METADATA:description = {description}",
        f"// @METADATA:reference   = {reference}",
        f"// @METADATA:criterion   = {criterion}",
        f"// @METADATA:expected    = {expected}",
    ]
    if tags:
        lines.append(f"// @METADATA:tags        = {tags}")
    lines.append("")
    lines.append(source)
    return "\n".join(lines)

def save_variant_distinction_benchmarks(
    benchmarks_dir: Path = BENCHMARKS_DIR
) -> list[str]:
    """Generate and save variant distinction benchmarks."""
    out_dir = benchmarks_dir 
    out_dir.mkdir(parents=True, exist_ok=True)

    benchmarks = generate_variant_distinction_benchmarks()
    saved      = []

    for b in benchmarks:
        content = _format_benchmark_source(
            name        = b['name'],
            description = b['description'],
            reference   = b['reference'],
            criterion   = b['criterion'],
            expected    = b['expected'],
            source      = b['source'],
            tags        = ' '.join(b['tags']),
        )
        path = out_dir / f"{b['name']}.prob"
        path.write_text(content)
        saved.append(b['name'])
        print(f"  Saved {b['name']}", flush=True)

    print(f"\nGenerated {len(saved)} variant distinction benchmarks "
          f"in {out_dir}")
    return saved


if __name__ == '__main__':
    # Point directly at your imported-benchmarks/contrived directory
    save_variant_distinction_benchmarks(Contrived_OUT)