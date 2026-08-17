import math
from ast import stmt
import re
import sys
from pathlib import Path
import traceback

# benchmarks/ lives at the repo root, one level up from bench-src/
BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / 'benchmarks'
NoisyOr_OUT    = BENCHMARKS_DIR/'noisy_or'

def noisy_or_threshold(acc: int) -> int:
    """Convert accumulated weight to Bernoulli threshold (out of 1000)."""
    p = 1 - math.exp(-acc)
    return int(round(p * 1000))


def generate_noisy_or_topic(
    num_topics:  int = 3,
    num_words:   int = 4,
    edge_weights: dict | None = None,  # (i,j) -> weight, None = all 1
    seed:        int = 42,
) -> str:
    """
    Generate a noisy-OR topic model approximated in the discrete domain.

    Topology:
      node_0  = root (always active = 1)
      node_1  .. node_T   = topic nodes  (latent, queried)
      node_T1 .. node_T+W = word  nodes  (observed = 1)

    Noisy-OR approximation:
      For each node i, accumulate acc = sum of (weight * parent_active)
      Then: node_i = 1  iff  unif[1,1000] <= round((1-exp(-acc))*1000)

    Slicing interest:
      - Return node_1 (first topic)
      - Only topics/words connected to node_1 matter
      - Disconnected topics+words are sliced away by all variants
      - While-loop variant: wrap inference in rejection loop
        to show NS/NIDS/NI differences
    """
    import random
    rng = random.Random(seed)

    # Default: all edges weight 1 between root->topics and topics->words
    if edge_weights is None:
        edge_weights = {}
        # root -> all topics
        for t in range(1, num_topics + 1):
            edge_weights[(t, 0)] = 1
        # topics -> words (random sparse connections)
        for w in range(1, num_words + 1):
            word_id = num_topics + w
            # each word connected to 1..num_topics topics randomly
            n_parents = rng.randint(1, num_topics)
            parents   = rng.sample(range(1, num_topics + 1), n_parents)
            for t in parents:
                edge_weights[(word_id, t)] = 1

    lines = []
    lines.append(f"// Noisy-OR Topic Model")
    lines.append(f"// {num_topics} topics, {num_words} words")
    lines.append(f"// Bernoulli(1-exp(-acc)) approximated via unif[1,1000]")
    lines.append(f"// Reference: Henrion (1987), Noisy-OR gates")
    lines.append("")

    # --- Root node (always active) ---
    lines.append("// Root node (always active)")
    lines.append("node_0 := 1;")
    lines.append("")

    # --- Topic nodes (latent) ---
    lines.append("// Topic nodes (latent)")
    for t in range(1, num_topics + 1):
        parents = [
            (par, w) for (node, par), w in edge_weights.items()
            if node == t
        ]
        if not parents:
            # No parents — node is always inactive
            lines.append(f"node_{t} := 0;")
            continue

        # Accumulate weighted sum of active parents
        lines.append(f"// Topic {t}: parents = "
                     f"{[(p, w) for p,w in parents]}")
        lines.append(f"_acc_{t} := 0;")
        for par, w in parents:
            lines.append(f"_acc_{t} := _acc_{t} + node_{par} * {w};")

        # Sample via noisy-OR thresholds
        # P(node=1 | acc=k) = 1 - exp(-k)
        # Implement as nested if on _acc value
        max_acc = sum(w for _, w in parents)
        lines.append(f"_coin_{t} :~ unif[1, 1000];")
        lines.append(f"node_{t} := 0;")
        for acc_val in range(1, max_acc + 1):
            thr = noisy_or_threshold(acc_val)
            if acc_val == 1:
                lines.append(f"if (_acc_{t} >= {acc_val} && "
                             f"_coin_{t} <= {thr}) then")
            else:
                lines.append(f"if (_acc_{t} >= {acc_val} && "
                             f"_coin_{t} <= {thr}) then")
            lines.append(f"  node_{t} := 1;")
            lines.append(f"else")
            lines.append(f"  skip;")
            lines.append(f"end")
        lines.append("")

    # --- Word nodes (observed = 1) ---
    lines.append("// Word nodes (observed active)")
    for w in range(1, num_words + 1):
        word_id = num_topics + w
        parents = [
            (par, wt) for (node, par), wt in edge_weights.items()
            if node == word_id
        ]
        if not parents:
            lines.append(f"// Word {w}: no parents — skip")
            continue

        lines.append(f"// Word {w}: parents = "
                     f"{[(p, wt) for p, wt in parents]}")
        lines.append(f"_acc_w{w} := 0;")
        for par, wt in parents:
            lines.append(f"_acc_w{w} := _acc_w{w} + node_{par} * {wt};")

        max_acc = sum(wt for _, wt in parents)
        lines.append(f"_coin_w{w} :~ unif[1, 1000];")
        lines.append(f"_word_{w} := 0;")
        for acc_val in range(1, max_acc + 1):
            thr = noisy_or_threshold(acc_val)
            lines.append(f"if (_acc_w{w} >= {acc_val} && "
                         f"_coin_w{w} <= {thr}) then")
            lines.append(f"  _word_{w} := 1;")
            lines.append(f"else")
            lines.append(f"  skip;")
            lines.append(f"end")

        # Observe word is active
        lines.append(f"observe(_word_{w} = 1);")
        lines.append("")

    # --- Query ---
    lines.append("return node_1;")
    return "\n".join(lines)

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

def generate_noisy_or_variants(
    benchmarks_dir: Path = BENCHMARKS_DIR,
) -> list[str]:
    """
    Generate noisy-OR topic model benchmarks at increasing scales.
    Scales: (num_topics, num_words)
    """
    scales = [
        (2, 3),
        (3, 5),
        (5, 8),
        (8, 12),
        (10, 20),
    ]
    saved = []
    out_dir = benchmarks_dir / 'noisy_or'
    out_dir.mkdir(parents=True, exist_ok=True)

    for num_topics, num_words in scales:
        name   = f"noisy_or_{num_topics}t_{num_words}w"
        source = generate_noisy_or_topic(num_topics, num_words)
        content = _format_benchmark_source(
            name        = name,
            description = (
                f"Noisy-OR topic model: {num_topics} topics, "
                f"{num_words} words. "
                f"Bernoulli approximated via unif[1,1000]."
            ),
            reference   = (
                "Henrion (1987), Noisy-OR; "
                "BeanMachine PPLBench implementation"
            ),
            criterion   = "node_1",
            expected    = "unknown",
            source      = source,
            tags        = "generated noisy_or bayesian_network",
        )
        path = out_dir / f"{name}.prob"
        path.write_text(content)
        saved.append(name)
        print(f"  Saved {name}", flush=True)

    return saved
def generate_noisy_or_with_rejection(
    num_topics:  int = 3,
    num_words:   int = 4,
    edge_weights: dict | None = None,
    seed:        int = 42,
) -> str:
    """
    Noisy-OR topic model wrapped in a rejection sampling loop.
    Resamples all variables until all word observations pass.
    This creates a nonterminating loop, showing NS/NIDS/NI differences:
      - NS  keeps the while loop (nontermination-sensitive)
      - NI  removes the while loop (nontermination-insensitive)
      - NIDS middle ground
    """
    import math

    def noisy_or_threshold(acc: int) -> int:
        p = 1 - math.exp(-acc)
        return int(round(p * 1000))

    import random
    rng = random.Random(seed)

    if edge_weights is None:
        edge_weights = {}
        for t in range(1, num_topics + 1):
            edge_weights[(t, 0)] = 1
        for w in range(1, num_words + 1):
            word_id = num_topics + w
            n_parents = rng.randint(1, num_topics)
            parents   = rng.sample(range(1, num_topics + 1), n_parents)
            for t in parents:
                edge_weights[(word_id, t)] = 1

    lines = []
    lines.append(f"// Noisy-OR Topic Model with Rejection Sampling Loop")
    lines.append(f"// {num_topics} topics, {num_words} words")
    lines.append(f"// Loop resamples until all word observations pass")
    lines.append(f"// NS keeps loop; NI removes it -> variant distinction")
    lines.append(f"// Reference: Henrion (1987), BeanMachine PPLBench")
    lines.append("")

    # --- Rejection loop ---
    lines.append("_accepted := 0;")
    lines.append("while (_accepted = 0) do")
    lines.append("")

    def emit(line: str, indent: int = 1):
        lines.append("  " * indent + line)

    # Root node
    emit("// Root node (always active)")
    emit("node_0 := 1;")
    emit("")

    # Topic nodes
    emit("// Topic nodes (latent)")
    for t in range(1, num_topics + 1):
        parents = [
            (par, w) for (node, par), w in edge_weights.items()
            if node == t
        ]
        if not parents:
            emit(f"node_{t} := 0;")
            continue

        emit(f"// Topic {t}: parents = {[(p, w) for p, w in parents]}")
        emit(f"_acc_{t} := 0;")
        for par, w in parents:
            emit(f"_acc_{t} := _acc_{t} + node_{par} * {w};")

        max_acc = sum(w for _, w in parents)
        emit(f"_coin_{t} :~ unif[1, 1000];")
        emit(f"node_{t} := 0;")
        for acc_val in range(1, max_acc + 1):
            thr = noisy_or_threshold(acc_val)
            emit(f"if (_acc_{t} >= {acc_val} && _coin_{t} <= {thr}) then")
            emit(f"  node_{t} := 1;", indent=2)
            emit(f"else", indent=1)
            emit(f"  skip;", indent=2)
            emit(f"end", indent=1)
        emit("")

    # Word nodes with rejection
    emit("// Word nodes — check all observations")
    emit("_all_words_ok := 1;")
    emit("")

    for w in range(1, num_words + 1):
        word_id = num_topics + w
        parents = [
            (par, wt) for (node, par), wt in edge_weights.items()
            if node == word_id
        ]
        if not parents:
            emit(f"// Word {w}: no parents — always inactive")
            emit(f"_word_{w} := 0;")
            emit(f"_all_words_ok := 0;")
            emit("")
            continue

        emit(f"// Word {w}: parents = {[(p, wt) for p, wt in parents]}")
        emit(f"_acc_w{w} := 0;")
        for par, wt in parents:
            emit(f"_acc_w{w} := _acc_w{w} + node_{par} * {wt};")

        max_acc = sum(wt for _, wt in parents)
        emit(f"_coin_w{w} :~ unif[1, 1000];")
        emit(f"_word_{w} := 0;")
        for acc_val in range(1, max_acc + 1):
            thr = noisy_or_threshold(acc_val)
            emit(f"if (_acc_w{w} >= {acc_val} && _coin_w{w} <= {thr}) then")
            emit(f"  _word_{w} := 1;", indent=2)
            emit(f"else", indent=1)
            emit(f"  skip;", indent=2)
            emit(f"end", indent=1)

        # Check word observation
        emit(f"if (_word_{w} = 0) then")
        emit(f"  _all_words_ok := 0;", indent=2)
        emit(f"else", indent=1)
        emit(f"  skip;", indent=2)
        emit(f"end", indent=1)
        emit("")

    # Accept if all words active
    emit("if (_all_words_ok = 1) then")
    emit("  _accepted := 1;", indent=2)
    emit("else", indent=1)
    emit("  skip;", indent=2)
    emit("end", indent=1)
    emit("")

    lines.append("end")
    lines.append("")
    lines.append("return node_1;")

    return "\n".join(lines)


def save_noisy_or_rejection_benchmarks(
    benchmarks_dir: Path = BENCHMARKS_DIR,
) -> list[str]:
    """
    Generate and save noisy-OR rejection sampling benchmarks
    at increasing scales. These show NS/NIDS/NI differences
    due to the rejection loop.
    """
    scales = [
        (2, 3),
        (3, 5),
        (5, 8),
        (8, 12),
        (10, 20),
    ]

    out_dir = benchmarks_dir / 'noisy_or'
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for num_topics, num_words in scales:
        name   = f"noisy_or_rej_{num_topics}t_{num_words}w"
        source = generate_noisy_or_with_rejection(num_topics, num_words)
        content = _format_benchmark_source(
            name        = name,
            description = (
                f"Noisy-OR topic model with rejection sampling loop: "
                f"{num_topics} topics, {num_words} words. "
                f"Loop resamples until all word observations pass. "
                f"NS keeps loop, NI removes it."
            ),
            reference   = (
                "Henrion (1987), Noisy-OR gates; "
                "BeanMachine PPLBench implementation"
            ),
            criterion   = "node_1",
            expected    = "ns_size > ni_size",
            source      = source,
            tags        = "generated noisy_or rejection_sampling",
        )
        path = out_dir / f"{name}.prob"
        path.write_text(content)
        saved.append(name)
        print(f"  Saved {name} "
              f"({num_topics} topics, {num_words} words)",
              flush=True)

    print(f"\nGenerated {len(saved)} noisy-OR rejection benchmarks "
          f"in {out_dir}", flush=True)
    return saved


def generate_framingham() -> str:
    return """\
// Coronary heart disease risk predictor (Framingham formula)
// Translated from PSI/WebPPL version
// All values scaled x10 for integer arithmetic
// unifReal(a,b) -> unif[a*10, b*10]
// Reference: http://www.framinghamheartstudy.org/risk/coronary.html

// --- Primary risk factors ---
age        :~ unif[300, 750];    // age in [30,75], scaled x10
ldlc       :~ unif[700, 2400];   // LDL cholesterol, scaled x10
bpSystolic :~ unif[1200, 1700];  // systolic BP, scaled x10

// cholesterol and HDL depend on LDL level
if (ldlc <= 1600) then
  chol :~ unif[1300, 2400];
  hdlc :~ unif[400, 750];
else
  chol :~ unif[2000, 3000];
  hdlc :~ unif[200, 500];
end

// diastolic BP
_bpDiastolicOffset :~ unif[400, 800];
bpDiastolic := bpSystolic - _bpDiastolicOffset;

// diabetes and smoking (scaled x100, threshold at 50)
isDiabetic :~ unif[0, 100];
isSmoker   :~ unif[0, 100];

// --- Compute LDL points (scaled x10) ---
// ldlPoints = 0.2*(age-35) - 1.0  (scaled: 2*(age-350)//10 - 10)
ldlPoints := 2 * (age - 350) / 10 - 10;
cholPoints := ldlPoints;

// LDL cholesterol adjustment
if (ldlc <= 1000) then
  ldlPoints := ldlPoints - 30;
else
  if (ldlc <= 1600) then
    ldlPoints := ldlPoints + 0;
  else
    if (ldlc <= 1900) then
      ldlPoints := ldlPoints + 10;
    else
      ldlPoints := ldlPoints + 20;
    end
  end
end

// Total cholesterol adjustment
if (chol <= 1600) then
  cholPoints := cholPoints - 30;
else
  if (chol <= 2000) then
    cholPoints := cholPoints + 0;
  else
    if (chol <= 2400) then
      cholPoints := cholPoints + 10;
    else
      if (chol <= 2800) then
        cholPoints := cholPoints + 20;
      else
        cholPoints := cholPoints + 30;
      end
    end
  end
end

// HDL adjustment
if (hdlc <= 350) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  if (hdlc <= 450) then
    ldlPoints  := ldlPoints  + 10;
    cholPoints := cholPoints + 10;
  else
    skip;
  end
end

if (hdlc >= 600) then
  cholPoints := cholPoints - 20;
  ldlPoints  := ldlPoints  - 10;
else
  skip;
end

// BP systolic adjustment
bpSystolicIncr := 0;
if (bpSystolic >= 850) then  bpSystolicIncr := 10; else skip; end
if (bpSystolic >= 900) then  bpSystolicIncr := 20; else skip; end
if (bpSystolic >= 1000) then bpSystolicIncr := 30; else skip; end

// BP diastolic adjustment
bpDiastolicIncr := 0;
if (bpDiastolic >= 1300) then bpDiastolicIncr := 10; else skip; end
if (bpDiastolic >= 1400) then bpDiastolicIncr := 20; else skip; end
if (bpDiastolic >= 1600) then bpDiastolicIncr := 20; else skip; end

// Use larger BP increment
if (bpSystolicIncr >= bpDiastolicIncr) then
  ldlPoints  := ldlPoints  + bpSystolicIncr;
  cholPoints := cholPoints + bpSystolicIncr;
else
  ldlPoints  := ldlPoints  + bpDiastolicIncr;
  cholPoints := cholPoints + bpDiastolicIncr;
end

// Diabetes adjustment
if (isDiabetic >= 50) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  skip;
end

// Smoking adjustment
if (isSmoker >= 50) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  skip;
end

// Save original points
ldlPointsOrig  := ldlPoints;
cholPointsOrig := cholPoints;

// --- Add measurement noise ---
_ldlcNoise      :~ unif[-100, 100];
_cholNoise      :~ unif[-200, 200];
_hdlcNoise      :~ unif[-50,   50];
_bpSysNoise     :~ unif[-200, 200];
_bpDiaNoise     :~ unif[-100, 100];
ldlc       := ldlc       + _ldlcNoise;
chol       := chol       + _cholNoise;
hdlc       := hdlc       + _hdlcNoise;
bpSystolic := bpSystolic + _bpSysNoise;
bpDiastolic := bpDiastolic + _bpDiaNoise;

// Diabetes flip (5% chance)
_flip0 :~ unif[0, 100];
if (_flip0 <= 5) then
  isDiabetic := 100 - isDiabetic;
else
  skip;
end

// Smoking flip (5% chance)
_flip1 :~ unif[0, 100];
if (_flip1 <= 5) then
  isSmoker := 100 - isSmoker;
else
  skip;
end

// --- Recompute points after noise ---
ldlPoints  := 2 * (age - 350) / 10 - 10;
cholPoints := ldlPoints;

if (ldlc <= 1000) then
  ldlPoints := ldlPoints - 30;
else
  if (ldlc <= 1600) then
    ldlPoints := ldlPoints + 0;
  else
    if (ldlc <= 1900) then
      ldlPoints := ldlPoints + 10;
    else
      ldlPoints := ldlPoints + 20;
    end
  end
end

if (chol <= 1600) then
  cholPoints := cholPoints - 30;
else
  if (chol <= 2000) then
    cholPoints := cholPoints + 0;
  else
    if (chol <= 2400) then
      cholPoints := cholPoints + 10;
    else
      if (chol <= 2800) then
        cholPoints := cholPoints + 20;
      else
        cholPoints := cholPoints + 30;
      end
    end
  end
end

if (hdlc <= 350) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  if (hdlc <= 450) then
    ldlPoints  := ldlPoints  + 10;
    cholPoints := cholPoints + 10;
  else
    skip;
  end
end

if (hdlc >= 600) then
  cholPoints := cholPoints - 20;
  ldlPoints  := ldlPoints  - 10;
else
  skip;
end

bpSystolicIncr := 0;
if (bpSystolic >= 850)  then bpSystolicIncr := 10; else skip; end
if (bpSystolic >= 900)  then bpSystolicIncr := 20; else skip; end
if (bpSystolic >= 1000) then bpSystolicIncr := 30; else skip; end

bpDiastolicIncr := 0;
if (bpDiastolic >= 1300) then bpDiastolicIncr := 10; else skip; end
if (bpDiastolic >= 1400) then bpDiastolicIncr := 20; else skip; end
if (bpDiastolic >= 1600) then bpDiastolicIncr := 20; else skip; end

if (bpSystolicIncr >= bpDiastolicIncr) then
  ldlPoints  := ldlPoints  + bpSystolicIncr;
  cholPoints := cholPoints + bpSystolicIncr;
else
  ldlPoints  := ldlPoints  + bpDiastolicIncr;
  cholPoints := cholPoints + bpDiastolicIncr;
end

if (isDiabetic >= 50) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  skip;
end

if (isSmoker >= 50) then
  ldlPoints  := ldlPoints  + 20;
  cholPoints := cholPoints + 20;
else
  skip;
end

// --- Query ---
// Original: estimateProb(tmp >= 5.0) where tmp = ldlPoints - ldlPointsOrig
// Translated: observe that the risk increase is significant (>= 50 scaled)
tmp := ldlPoints - ldlPointsOrig;
observe(tmp >= 50);
return tmp;
"""

def save_framingham_benchmark(
    benchmarks_dir: Path = BENCHMARKS_DIR,
) -> str:
    out_dir = benchmarks_dir / 'medical'
    out_dir.mkdir(parents=True, exist_ok=True)

    name    = 'framingham_heart_risk'
    source  = generate_framingham()
    content = _format_benchmark_source(
        name        = name,
        description = (
            "Coronary heart disease risk predictor (Framingham formula). "
            "Computes LDL and cholesterol risk points from patient data. "
            "Observes significant risk increase after measurement noise. "
            "All values scaled x10 for integer arithmetic."
        ),
        reference   = (
            "Framingham Heart Study "
            "(http://www.framinghamheartstudy.org/risk/coronary.html); "
            "PSI probabilistic program"
        ),
        criterion   = "tmp",
        expected    = "unknown",
        source      = source,
        tags        = "medical framingham real_world no_while",
    )
    path = out_dir / f"{name}.prob"
    path.write_text(content)
    print(f"  Saved {name}", flush=True)
    return name

def generate_inventory_simulator() -> str:
    return """\
// @METADATA:name        = inventory_operations_simulator
// @METADATA:description = Inventory and operations simulator with three independent tracks: Track 1 (inventory/costs, criterion), Track 2 (machinery maintenance, independent), Track 3 (environmental emissions, independent). NI/NIDS should remove Track 2 and Track 3 entirely. NS keeps all tracks due to nontermination-sensitive loop. All reals scaled x10 for integer arithmetic.
// @METADATA:reference   = Generated benchmark for variant distinction
// @METADATA:criterion   = total_holding_cost
// @METADATA:expected    = ns_size > ni_size
// @METADATA:tags        = generated operations real_world while_loop variant_distinction

// Inventory and Operations Simulator
// Three independent computation tracks:
//   Track 1: Core inventory (RETURNED) — stock, backlog, costs
//   Track 2: Machinery maintenance (INDEPENDENT) — machine_wear, breakdowns
//   Track 3: Environmental emissions (INDEPENDENT) — carbon, fines
//
// Approximations (all reals scaled x10):
//   poisson(15)       -> unif[5, 25]
//   uniform(0.1, 0.3) -> unif[1, 3]
//   normal(1.5, 0.2)  -> unif[13, 17]
//   gamma(1.5, 0.1)   -> unif[1, 3]
//   normal(50.0, 5.0) -> unif[450, 550]
//   bernoulli(0.4)    -> unif[1, 10] <= 4
//   bernoulli(0.7)    -> unif[1, 10] <= 7
//   penalty threshold  = 50000 (5000.0 x10)
//   machine_wear threshold = 750 (75.0 x10)
//   carbon threshold   = 40000 (4000.0 x10)

// === TRACK 1: CORE INVENTORY (criterion) ===
stock               := 100;
backlog             := 0;
order_pipeline_days := -1;
total_holding_cost  := 0;
total_penalty_cost  := 0;

// === TRACK 2: MACHINERY MAINTENANCE (independent) ===
machine_wear        := 0;
breakdown_count     := 0;
maintenance_cycles  := 0;

// === TRACK 3: ENVIRONMENTAL EMISSIONS (independent) ===
carbon_footprint            := 0;
regulatory_fine_accumulated := 0;
green_credits               := 100;

days := 0;
while (days < 100 && total_penalty_cost < 50000) do

  days := days + 1;

  // ------------------ TRACK 1 ------------------
  demand     :~ unif[5, 25];
  net_demand := demand + backlog;

  if (stock >= net_demand) then
    stock   := stock - net_demand;
    backlog := 0;
  else
    backlog := net_demand - stock;
    stock   := 0;
  end

  if (order_pipeline_days > 0) then
    order_pipeline_days := order_pipeline_days - 1;
  else
    if (order_pipeline_days = 0) then
      stock               := stock + 50;
      order_pipeline_days := -1;
    else
      skip;
    end
  end

  if (stock < 20 && order_pipeline_days = -1) then
    order_pipeline_days := 3;
  else
    skip;
  end

  holding_rate       :~ unif[1, 3];
  total_holding_cost := total_holding_cost + stock * holding_rate;

  penalty_rate       :~ unif[13, 17];
  total_penalty_cost := total_penalty_cost + backlog * penalty_rate;

  // ------------------ TRACK 2 ------------------
  wear_factor  :~ unif[1, 3];
  machine_wear := machine_wear + wear_factor;

  if (machine_wear > 750) then
    breakdown_fail :~ unif[1, 10];
    if (breakdown_fail <= 4) then
      breakdown_count := breakdown_count + 1;
      machine_wear    := 0;
    else
      skip;
    end
  else
    skip;
  end

  maintenance_cycles := maintenance_cycles + 1;

  // ------------------ TRACK 3 ------------------
  base_emissions   :~ unif[450, 550];
  carbon_footprint := carbon_footprint + base_emissions;

  if (carbon_footprint > 40000) then
    fine_trigger :~ unif[1, 10];
    if (fine_trigger <= 7) then
      regulatory_fine_accumulated := regulatory_fine_accumulated + 5000;
      green_credits               := green_credits - 10;
    else
      skip;
    end
  else
    skip;
  end

  // ------------------ OBSERVATIONS ------------------
  observe(backlog < 40);
  observe(machine_wear < 950);

end

return total_holding_cost;
"""


def save_inventory_benchmark(
    benchmarks_dir: Path = BENCHMARKS_DIR,
) -> str:
    out_dir = benchmarks_dir / 'misc'
    out_dir.mkdir(parents=True, exist_ok=True)

    name    = 'inventory_operations_simulator'
    source  = generate_inventory_simulator()
    content = _format_benchmark_source(
        name        = name,
        description = (
            "Inventory and operations simulator with three independent tracks: "
            "Track 1 (inventory/costs, criterion), "
            "Track 2 (machinery maintenance, independent), "
            "Track 3 (environmental emissions, independent). "
            "NI/NIDS should remove Track 2 and Track 3 entirely. "
            "NS keeps all tracks due to nontermination-sensitive loop. "
            "All reals scaled x10 for integer arithmetic."
        ),
        reference   = "Generated benchmark for variant distinction",
        criterion   = "total_holding_cost",
        expected    = "ns_size > ni_size",
        source      = source,
        tags        = "generated operations real_world while_loop variant_distinction",
    )
    path = out_dir / f"{name}.prob"
    path.write_text(content)
    print(f"  Saved {name}", flush=True)
    return name

if __name__ == '__main__':
    save_noisy_or_rejection_benchmarks()
    generate_noisy_or_variants()
    save_framingham_benchmark()
    save_inventory_benchmark()
    print("\nAll benchmarks generated.")