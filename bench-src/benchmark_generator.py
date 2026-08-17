from dataclasses import dataclass
from pathlib import Path


# benchmarks/ lives at the repo root, one level up from bench-src/
BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / 'benchmarks'

def generate_hmm(n_steps: int) -> str:
    """
    HMM with n_steps time steps.
    Hidden state x_t transitions with noise,
    observation y_t depends on x_t.
    """
    lines = []
    # Initial state
    lines.append(f"x0 :~ unif[1, 4];")
    
    for t in range(1, n_steps + 1):
        # Transition: x_t depends on x_{t-1}
        lines.append(f"noise{t} :~ unif[0, 1];")
        lines.append(f"x{t} := x{t-1} + noise{t};")
        # Emission: y_t depends on x_t
        lines.append(f"y{t} :~ unif[1, 4];")
        lines.append(f"observe(y{t} = x{t});")
    
    lines.append(f"return x{n_steps};")
    return "\n".join(lines)

def generate_bayesian_network(n_vars: int) -> str:
    """
    Layered Bayesian network with n_vars nodes.
    Each variable depends on the previous two (if they exist),
    last variable is observed.
    """
    lines = []
    
    # Root nodes (no parents)
    lines.append(f"x0 :~ unif[1, 4];")
    lines.append(f"x1 :~ unif[1, 4];")
    
    # Interior nodes (two parents each)
    for i in range(2, n_vars):
        lines.append(f"x{i} := x{i-2} + x{i-1};")
    
    # Observe last variable
    lines.append(f"observe(x{n_vars-1} = 3);")
    
    # Criterion: return root
    lines.append(f"return x0;")
    return "\n".join(lines)

def generate_random_walk(n_steps: int) -> str:
    """
    Random walk for n_steps steps.
    At each step, position updated by random increment.
    Observed at the end.
    """
    lines = []
    lines.append(f"x0 :~ unif[1, 4];")
    
    for t in range(1, n_steps + 1):
        lines.append(f"step{t} :~ unif[0, 1];")
        lines.append(f"x{t} := x{t-1} + step{t};")
    
    lines.append(f"observe(x{n_steps} > 2);")
    lines.append(f"return x0;")
    return "\n".join(lines)

def generate_trueskill_tournament(
    n_teams:      int = 31,
    max_players:  int = 4,
    n_matches:    int = 30,   # subset of all possible matches
    query_team:   int = 1,
    query_player: int = 1,
    seed:         int = 42,
) -> str:
    """
    Generate a TrueSkill tournament benchmark.
    Query: skill of query_player on query_team.
    Slicing should remove players/matches irrelevant to that query.
    """
    import random
    rng = random.Random(seed)

    lines = []
    lines.append(f"// TrueSkill tournament: {n_teams} teams, "
                 f"{max_players} players, {n_matches} matches")
    lines.append("")

    # Assign random number of players to each team
    team_sizes = {
        t: rng.randint(1, max_players)
        for t in range(1, n_teams + 1)
    }

    # Sample player skills
    lines.append("// --- Player skills ---")
    for t in range(1, n_teams + 1):
        for p in range(1, team_sizes[t] + 1):
            lines.append(f"s_{t}_{p} :~ unif[0, 10];")
    lines.append("")

    # Team performance = sum of player skills
    lines.append("// --- Team performances ---")
    for t in range(1, n_teams + 1):
        players = " + ".join(
            f"s_{t}_{p}" for p in range(1, team_sizes[t] + 1)
        )
        lines.append(f"perf_{t} := {players};")
    lines.append("")

    # Generate random match schedule
    all_pairs = [
        (i, j)
        for i in range(1, n_teams + 1)
        for j in range(i + 1, n_teams + 1)
    ]
    matches = rng.sample(all_pairs, min(n_matches, len(all_pairs)))

    # Observe match outcomes
    lines.append("// --- Match observations ---")
    for idx, (t1, t2) in enumerate(matches):
        winner = rng.choice([t1, t2])
        loser  = t2 if winner == t1 else t1
        lines.append(f"noise_{idx} :~ unif[0, 1];")
        lines.append(
            f"observe(perf_{winner} + noise_{idx} > perf_{loser});"
        )
    lines.append("")

    # Query
    lines.append(f"// --- Query: skill of player "
                 f"{query_player} on team {query_team} ---")
    lines.append(f"return s_{query_team}_{query_player};")

    return "\n".join(lines)

import random
from itertools import combinations
from pathlib import Path

def generate_chess_trueskill(
    n_players:       int = 77,
    n_games:         int = 2926,
    query_player:    int = 1,
    n_player1_games: int = 10,
    seed:            int = 42,
) -> str:
    rng = random.Random(seed)

    p1_opponents  = list(range(2, n_player1_games + 2))
    player1_games = [(query_player, opp) for opp in p1_opponents]

    isolated_players = list(range(n_player1_games + 2, n_players + 1))
    other_pairs = [
        (i, j)
        for i in isolated_players
        for j in isolated_players
        if i < j
    ]
    n_other     = n_games - len(player1_games)
    other_games = [rng.choice(other_pairs) for _ in range(n_other)]

    all_games = player1_games + other_games
    rng.shuffle(all_games)
    outcomes = [
        (i, j) if rng.random() > 0.5 else (j, i)
        for i, j in all_games
    ]

    lines = []
    lines.append(f"// Chess TrueSkill: {n_players} players, {n_games} games")
    lines.append(f"// Player {query_player} plays {n_player1_games} games")
    lines.append(f"// against dedicated opponents {p1_opponents}")
    lines.append(f"// Remaining {len(isolated_players)} players only play")
    lines.append(f"// among themselves — irrelevant to the query")
    lines.append(f"// Expected reduction: ~"
                 f"{100*(n_other*5)/(n_games*5 + n_players):.0f}%")
    lines.append(f"// Model: Herbrich et al. NeurIPS 2006 /")
    lines.append(f"//        Dangauthier et al. NeurIPS 2007")
    lines.append("")

    # --- Latent player skills ---
    lines.append("// --- Latent player skills ---")
    for i in range(1, n_players + 1):
        lines.append(f"s_{i} :~ unif[0, 100];")
    lines.append("")

    # --- Game observations ---
    lines.append("// --- Game observations ---")
    for g, (winner, loser) in enumerate(outcomes, start=1):
        lines.append(f"// Game {g}: player {winner} beat player {loser}")
        lines.append(f"noise_{g}_w :~ unif[0, 10];")
        lines.append(f"noise_{g}_l :~ unif[0, 10];")
        lines.append(f"perf_{g}_w  := s_{winner} + noise_{g}_w;")
        lines.append(f"perf_{g}_l  := s_{loser}  + noise_{g}_l;")
        lines.append(f"observe(perf_{g}_w > perf_{g}_l);")
        lines.append("")

    # --- Query ---
    lines.append(f"// --- Query: skill of player {query_player}? ---")
    lines.append(f"return s_{query_player};")

    return "\n".join(lines)

def generate_chess_trueskill_old(
    n_players:  int = 77,
    n_games:    int = 2926,
    query_player: int = 1,
    seed:       int = 42,
) -> str:
    """
    TrueSkill skill rating for a chess tournament.
    Structure follows Herbrich et al. (NeurIPS 2006) / 
    Dangauthier et al. (NeurIPS 2007):

      - Each player i has a latent skill:  s_i :~ unif[0, 100]
      - Each game g between players i and j:
            perf_i_g := s_i + noise_i_g   (noise_i_g :~ unif[0, 10])
            perf_j_g := s_j + noise_j_g   (noise_j_g :~ unif[0, 10])
            observe(perf_winner_g > perf_loser_g)
      - Query: return s_1  (skill of player 1)

    Slicing interest: only players connected to player 1 through
    the game graph are relevant — all others can be removed.
    """
    rng = random.Random(seed)

    # Generate game schedule: random pairs from all possible matchups,
    # with replacement to reach 2926 games across 77 players
    all_pairs = list(combinations(range(1, n_players + 1), 2))
    # Sample with replacement to hit exactly n_games
    games = [rng.choice(all_pairs) for _ in range(n_games)]
    # Assign winner randomly
    outcomes = [(i, j) if rng.random() > 0.5 else (j, i)
                for i, j in games]

    lines = []
    lines.append(f"// Chess TrueSkill: {n_players} players, {n_games} games")
    lines.append(f"// Model: Herbrich et al. NeurIPS 2006 /")
    lines.append(f"//        Dangauthier et al. NeurIPS 2007")
    lines.append("")

    # --- Latent player skills ---
    lines.append("// --- Latent player skills ---")
    for i in range(1, n_players + 1):
        lines.append(f"s_{i} :~ unif[0, 100];")
    lines.append("")

    # --- Game observations ---
    lines.append("// --- Game observations ---")
    for g, (winner, loser) in enumerate(outcomes, start=1):
        lines.append(f"// Game {g}: player {winner} beat player {loser}")
        lines.append(f"noise_{g}_w :~ unif[0, 10];")
        lines.append(f"noise_{g}_l :~ unif[0, 10];")
        lines.append(f"perf_{g}_w  := s_{winner} + noise_{g}_w;")
        lines.append(f"perf_{g}_l  := s_{loser}  + noise_{g}_l;")
        lines.append(f"observe(perf_{g}_w > perf_{g}_l);")
        lines.append("")

    # --- Query ---
    lines.append(f"// --- Query: what is the skill of player {query_player}? ---")
    lines.append(f"return s_{query_player};")

    return "\n".join(lines)


def save_chess_trueskill_benchmark(
    benchmarks_dir: Path = BENCHMARKS_DIR,
    **kwargs,
) -> str:
    """Generate and save the chess TrueSkill benchmark."""
    benchmarks_dir.mkdir(parents=True, exist_ok=True)

    source  = generate_chess_trueskill(**kwargs)
    name    = "trueskill_chess_77p_2926g"
    content = _format_benchmark_source(
        name        = name,
        description = (
            "TrueSkill skill rating for a chess tournament "
            "with 77 players and 2926 games"
        ),
        reference   = (
            "Herbrich et al., TrueSkill (NeurIPS 2006); "
            "Dangauthier et al., TrueSkill Through Time (NeurIPS 2007)"
        ),
        criterion   = "s_1",
        expected    = "unknown",
        source      = source,
        tags        = "generated trueskill chess large",
    )
    path = benchmarks_dir / f"{name}.prob"
    path.write_text(content)
    print(f"Saved {name} ({path})")
    return name

# ---------------------------------------------------------------------------
# Benchmark generation configuration
# ---------------------------------------------------------------------------

@dataclass
class TemplateConfig:
    name:       str
    gen_fn:     callable
    scales:     list
    desc_fn:    callable
    reference:  str
    criterion:  str
    tags:       str


def _trueskill_gen(config: tuple) -> str:
    n_teams, max_players, n_matches = config
    return generate_trueskill_tournament(n_teams, max_players, n_matches)


BENCHMARK_TEMPLATES: list[TemplateConfig] = [
    TemplateConfig(
        name      = 'hmm',
        gen_fn    = generate_hmm,
        scales    = [2, 5, 10, 20, 50],
        desc_fn   = lambda n: f"HMM with {n} time steps",
        reference = "generated/hmm",
        criterion = "x0",
        tags      = "generated",
    ),
    TemplateConfig(
        name      = 'bayes_net',
        gen_fn    = generate_bayesian_network,
        scales    = [4, 8, 16, 32, 64],
        desc_fn   = lambda n: f"Bayesian network with {n} variables",
        reference = "generated/bayes_net",
        criterion = "x0",
        tags      = "generated",
    ),
    TemplateConfig(
        name      = 'rnd_walk',
        gen_fn    = generate_random_walk,
        scales    = [5, 10, 25, 50, 100],
        desc_fn   = lambda n: f"Random walk with {n} steps",
        reference = "generated/rnd_walk",
        criterion = "x0",
        tags      = "generated",
    ),
    TemplateConfig(
        name      = 'trueskill',
        gen_fn    = _trueskill_gen,
        scales    = [
            ( 5,  2,  4),   # small
            (10,  2, 10),   # medium
            (15,  3, 20),   # medium-large
            (20,  4, 30),   # large
            (31,  4, 50),   # full tournament
        ],
        desc_fn   = lambda s: (
            f"TrueSkill tournament: {s[0]} teams, "
            f"{s[1]} players/team, {s[2]} matches"
        ),
        reference = "Herbrich et al., TrueSkill (NeurIPS 2006)",
        criterion = "s_1_1",
        tags      = "generated probabilistic",
    ),
]

# ---------------------------------------------------------------------------
# Shared formatting
# ---------------------------------------------------------------------------

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


def _scale_to_name(template_name: str, scale) -> str:
    """Convert a scale (int or tuple) to a filename-safe suffix."""
    if isinstance(scale, tuple):
        return "_".join(str(s) for s in scale)
    return str(scale)

# ---------------------------------------------------------------------------
# Unified generator
# ---------------------------------------------------------------------------

def generate_and_save_benchmarks(
    benchmarks_dir: Path = BENCHMARKS_DIR,
    templates:      list[TemplateConfig] = BENCHMARK_TEMPLATES,
) -> list[str]:
    """
    Generate all scaled benchmarks from BENCHMARK_TEMPLATES and save them
    to benchmarks_dir so that load_benchmarks() picks them up.
    Returns list of generated benchmark names.
    """
    benchmarks_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for tmpl in templates:
        for scale in tmpl.scales:
            scale_suffix = _scale_to_name(tmpl.name, scale)
            name         = f"{tmpl.name}_{scale_suffix}"
            source       = tmpl.gen_fn(scale)
            content      = _format_benchmark_source(
                name        = name,
                description = tmpl.desc_fn(scale),
                reference   = tmpl.reference,
                criterion   = tmpl.criterion,
                expected    = "unknown",
                source      = source,
                tags        = tmpl.tags,
            )
            path = benchmarks_dir / f"{name}.prob"
            path.write_text(content)
            generated.append(name)

    print(f"Generated {len(generated)} benchmarks in {benchmarks_dir}:")
    for name in generated:
        print(f"  {name}")

    return generated


if __name__ == '__main__':
    generate_and_save_benchmarks()
    save_chess_trueskill_benchmark()
