# prob_slicer — Static Dependence-Based Slicing for Probabilistic Programs

A research prototype implementing **static backward slicing** for 
an imperative probabilistic language.
---

## Language Syntax

```
p  ::= c ; return a                          Program
c  ::= skip | x := a | x :~ d               Commands
     | c1 ; c2
     | if b then c1 else c2 end
     | while b do c end
     | observe(b)
a  ::= z | x | -a | a1 + a2 | ...           Arithmetic
b  ::= true | false | a1 = a2 | a1 <= a2    Boolean
     | !b | b1 && b2 | b1 || b2
d  ::= unif[lo, hi]                          Distributions
     | bernoulli(p)
     | gaussian(mu, sigma)
     | distr{v1->p1, ..., vn->pn}
```

---

## Project Structure

```
ProbSlicer/
├── src/
│   └── prob_slicer/        Core slicing library (installable Python package)
│       ├── ast_nodes.py
│       ├── parser.py
│       ├── cfg_builder.py
│       ├── dependence.py
│       ├── slicer.py
│       └── ProbLang.g4     ANTLR4 grammar (reference)
├── bench-src/               Benchmark-authoring utilities (not part of the library)
│   ├── benchmark_loader.py       Loads .prob files from benchmarks/
│   ├── benchmark_generator.py
│   ├── benchmark_generator_contrived.py
│   ├── benchmark_generator_prodigy.py
│   └── benchmarks_noisy_topic.py
├── test/                    Evaluation / analysis scripts (entry points)
│   ├── test_slicer.py            Run the slicer on benchmarks, report results
│   ├── run_all_benchmarks.py     Orchestrate test_slicer.py across all categories
│   ├── evaluator.py               Monte Carlo correctness evaluation
│   ├── get_statistics.py         Paper-ready statistics + LaTeX tables
│   └── data_analysis.py          Quick headline stats + summary sentences
├── benchmarks/               .prob benchmark programs, one subdirectory per category
│   ├── contrived/ literature/ medical/ misc-variant/ noisy_or/ prodigy/ real-world/
├── imported-benchmarks/      Raw external benchmark sources
├── results/                  Generated output (workbook, CSVs, text summaries)
├── pyproject.toml            Installable package config (src/ layout)
├── README.md                 This file
└── README_Test.md            Detailed docs for test/test_slicer.py & friends
```

See [README_Test.md](README_Test.md) for full usage of the
`test/` scripts, including the automated paper-ready analysis pipeline.

## Probabilistic Extensions

### 1. Stochastic Data Dependence (`stoch_data`)
`x :~ d` is treated as a **definition** of `x` — identical to `x := a` for
reaching-definitions — but edges through it are tagged `stoch_data` so
downstream analyses can distinguish deterministic from probabilistic defs.

### 2. Observation Dependence (`observation`)
`observe(b)` conditions the posterior of *every* latent variable that has a
reaching stochastic definition at that point.  The slicer therefore seeds
the backward slice on **{criterion node} ∪ {all observe nodes}** so that
no conditioning statement is ever dropped when slicing a probabilistic output.

---

## Example: The Three Slice Types

The slicer supports three variants, each preserving a different notion of
program behaviour:

| Variant | Preserves                                    | Aggressiveness |
|---------|-----------------------------------------------|----------------|
| `ns`    | Termination probability **and** output distribution | Least — keeps the most code |
| `nids`  | Output distribution only (not termination probability) | Middle |
| `ni`    | Only the qualitative/possible output values  | Most — keeps the least code |

Take this program, which flips a coin, runs an unrelated bounded loop, and
conditions on the coin:

```
guess :~ bernoulli(0.5);
attempts := 0;
while (attempts <= 5) do
    attempts := attempts + 1;
end
observe(guess = 1);
return guess;
```

Slicing on the `return guess` criterion gives three different results:

**`ns` (nontermination-sensitive)** — keeps the loop. Even though this
particular loop happens to always terminate, the analysis is conservative:
any `while` loop could in principle affect the program's termination
probability, and `ns` must preserve that exactly, so it can't be dropped.

```
guess :~ bernoulli(0.5)
attempts := 0
while (attempts <= 5) do
  attempts := (attempts + 1)
end
observe((guess = 1))
return guess
```

**`nids` (nontermination-insensitive, distribution-sensitive)** — drops the
loop, since `nids` doesn't need to preserve termination probability, only
the output *distribution*. `attempts` has no effect on `guess`'s
distribution, so it's gone. The `observe` stays, since removing it would
change the returned distribution.

```
guess :~ bernoulli(0.5)
observe((guess = 1))
return guess
```

**`ni` (nontermination-insensitive, distribution-insensitive)** — the most
aggressive variant, preserving only which output *values* are reachable,
not their exact probabilities. Here it produces the same slice as `nids`,
since the `observe` is also necessary to determine which values of `guess`
are even possible. (In general `ni` can drop more than `nids` — e.g. an
`observe` that only reweights probabilities without ruling out any output
value — see the `nids_ni_gap_*` benchmarks in `benchmarks/contrived/` for
an example where `ni` produces a strictly smaller slice than `nids`.)

```
guess :~ bernoulli(0.5)
observe((guess = 1))
return guess
```

You can reproduce this yourself once installed (see below). `.prob` files
need a small metadata header (see
[README_Test.md](README_Test.md#benchmark-files)):

```bash
mkdir -p /tmp/readme_example
cat > /tmp/readme_example/example.prob <<'EOF'
// @METADATA:name        = example
// @METADATA:description = Coin flip with an unrelated bounded loop
// @METADATA:reference   = README.md
// @METADATA:criterion   = guess
// @METADATA:expected    = ns keeps the loop; nids/ni drop it

guess :~ bernoulli(0.5);
attempts := 0;
while (attempts <= 5) do
    attempts := attempts + 1;
end
observe(guess = 1);
return guess;
EOF

python test/test_slicer.py --benchdir /tmp/readme_example --bench example --compare
```

---

## Installation

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install prob_slicer in editable mode, plus the analysis extras
# (pandas/openpyxl, needed for get_statistics.py, data_analysis.py, and
# test_slicer.py --save-xlsx)
pip install -e ".[analysis]"
```

This installs `prob_slicer` (from `src/prob_slicer/`) as an importable
package via `pyproject.toml`, so `import prob_slicer` works from anywhere
— including the scripts in `test/` and `bench-src/`.

*(The hand-written parser in `parser.py` only requires `networkx`, installed
automatically as a dependency. The ANTLR4 grammar `src/prob_slicer/ProbLang.g4`
is provided for reference and for generating a more robust parser with
`antlr4 -Dlanguage=Python3 ProbLang.g4` — install the `grammar` extra
[`pip install -e ".[grammar]"`] if you need the ANTLR4 runtime for that.)*

---

## Running Benchmarks

Once installed (see above), run a quick sanity check from the repo root:

```bash
python test/test_slicer.py --bench noisy_or_2t_3w --compare
```

That's the extent of what belongs here — **all usage, CLI flags, Monte
Carlo correctness evaluation, memory-usage tracking, saving results to
JSON/CSV/Excel, and the automated multi-directory paper-ready analysis
pipeline (`run_all_benchmarks.py`) are documented in
[README_Test.md](README_Test.md)**, so they're not repeated here.

---

## File Index

| File                                    | Purpose                              |
|------------------------------------------|--------------------------------------|
| `src/prob_slicer/ProbLang.g4`            | ANTLR4 grammar (reference)           |
| `src/prob_slicer/ast_nodes.py`           | AST node dataclasses                 |
| `src/prob_slicer/parser.py`              | Recursive-descent parser             |
| `src/prob_slicer/cfg_builder.py`         | CFG construction + dot export        |
| `src/prob_slicer/dependence.py`          | RD, DDG, CDG, ObsDep, PDG, slicer    |
| `src/prob_slicer/slicer.py`              | AST reconstruction + pretty-printer  |
| `bench-src/benchmark_loader.py`          | Loads `.prob` benchmark files        |
| `bench-src/benchmark_generator*.py`      | Generate `.prob` benchmark files     |
| `test/test_slicer.py`                    | Benchmark runner (see [README_Test.md](README_Test.md)) |
| `test/run_all_benchmarks.py`             | Orchestrates `test_slicer.py` across every `benchmarks/` category |
| `test/evaluator.py`                      | Monte Carlo correctness evaluation   |
| `test/get_statistics.py`                 | Paper-ready statistics + LaTeX table |
| `test/data_analysis.py`                  | Quick headline stats + summary text  |
| `pyproject.toml`                         | Installable `src/` layout package config |

---

## Extending the Slicer

**New distributions**: add a dataclass in `src/prob_slicer/ast_nodes.py`
(subclass `Distr`), add a `parse_distr` branch in
`src/prob_slicer/parser.py`, and add a grammar rule in
`src/prob_slicer/ProbLang.g4`. The dependence analysis requires no changes.

**Conditioned slicing** (slice w.r.t. a specific observation): pass a custom
criterion set directly to `DependenceAnalysis.backward_slice({n1, n2, ...})`.

**Thin vs. probabilistically-correct slices**: remove `da.observe_nodes()`
from the seed set in `test/test_slicer.py`'s criterion-finding logic to get
a classical thin slice that ignores observation conditioning.
