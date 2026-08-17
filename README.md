# prob_slicer — Static Dependence-Based Slicing for Probabilistic Programs

A research prototype implementing **static backward slicing** for a small
probabilistic imperative language, following the dependence-graph approach
of Weiser (1984) extended with two new edge types for probabilistic semantics.

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
└── README_test_slicer.md     Detailed docs for test/test_slicer.py & friends
```

See [README_test_slicer.md](README_test_slicer.md) for full usage of the
`test/` scripts, including the automated paper-ready analysis pipeline.

## Architecture

```
source string
    │
    ▼
src/prob_slicer/parser.py       Hand-written recursive-descent parser
    │                             Tokenizer → tokens → AST (ast_nodes.py)
    ▼
src/prob_slicer/ast_nodes.py    Algebraic ADT: AExpr, BExpr, Distr, Cmd, Program
    │                             Each Cmd gets a unique node_id at construction time
    ▼
src/prob_slicer/cfg_builder.py  CFG as a NetworkX DiGraph
    │                             Nodes: atomic commands + synthetic ENTRY(0) / EXIT(-1)
    │                             Edges: 'seq' | 'true' | 'false' | 'back'
    │                             Exports: cfg_to_dot()
    ▼
src/prob_slicer/dependence.py   Three-phase dependence analysis
    │
    ├── ReachingDefinitions   forward dataflow (gen/kill worklist)
    │
    ├── DDG  (Data Dependence Graph)
    │        edge (d→u): def d reaches use u
    │        dep_type: 'data' | 'stoch_data'   ← :~ is stochastic def
    │
    ├── CDG  (Control Dependence Graph)
    │        post-dominator tree on reversed CFG
    │        dep_type: 'control'
    │
    ├── ObsDep  (Observation Dependence)          ← probabilistic extension
    │        edge (s→o): CSample s reaches CObserve o
    │        dep_type: 'observation'
    │
    └── PDG = DDG ∪ CDG ∪ ObsDep
             backward BFS from criterion → slice set
    │
    ▼
src/prob_slicer/slicer.py       AST reconstruction from slice node set
    │                             Prunes unreachable commands, replaces with CSkip
    │                             pretty_print() emits clean source
    ▼
test/test_slicer.py             Benchmark runner (--dot, --verbose, --bench, --evaluate, ...)
test/run_all_benchmarks.py      Runs test_slicer.py across every benchmarks/ category
```

---

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

Run everything from the repo root:

```bash
# Run all benchmarks in benchmarks/real-world/ (the default directory)
python test/test_slicer.py

# Run a single benchmark
python test/test_slicer.py --bench coin_flip

# Run benchmarks from a different category directory
python test/test_slicer.py --benchdir benchmarks/contrived

# Emit Graphviz .dot files for CFG and PDG
python test/test_slicer.py --dot

# Print full dependence report (reaching defs, DDG, CDG, ObsDep edges)
python test/test_slicer.py --verbose

# Combine flags
python test/test_slicer.py --bench discrete_bayes --verbose --dot

# Run the whole benchmark suite (every benchmarks/ subdirectory) at once,
# with Monte Carlo correctness evaluation, and produce the paper-ready
# statistics automatically
python test/run_all_benchmarks.py --evaluate --analyze
```

Then render dot files:
```bash
dot -Tpng dot_output/coin_flip_ns_cfg.dot -o coin_flip_cfg.png
dot -Tpng dot_output/coin_flip_ns_pdg.dot -o coin_flip_pdg.png
```

See [README_test_slicer.md](README_test_slicer.md) for the full command
reference (correctness evaluation, memory-usage tracking, saving results
to JSON/CSV/Excel, and the automated paper-ready analysis pipeline).

---

## Benchmark Results (summary)

| Benchmark       | Nodes | Slice | Reduction | Notes                              |
|-----------------|-------|-------|-----------|-------------------------------------|
| coin_flip       |   3   |   2   |  33.3%    | `z := 42` eliminated                |
| noisy_sensor    |   7   |   5   |  28.6%    | counter k eliminated                |
| geometric_loop  |   8   |   5   |  37.5%    | dead `if (count > 10)` eliminated   |
| two_coins       |   5   |   3   |  40.0%    | `result2 := c2*5` eliminated        |
| discrete_bayes  |   6   |   5   |  16.7%    | `age := 35` eliminated              |
| random_walk     |   9   |   7   |  22.2%    | log variables eliminated            |

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
| `test/test_slicer.py`                    | Benchmark runner (see [README_test_slicer.md](README_test_slicer.md)) |
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
