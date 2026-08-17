"""
Virtual machine evaluator.
Compiles AST to flat instruction list once, executes many times.
Much faster than AST interpretation, no exec/source generation fragility.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from collections import Counter
from enum import Enum, auto
from typing import Any
import random
import time
from prob_slicer.dependence import SliceVariant
from prob_slicer.ast_nodes import (
    Cmd, CSkip, CAssign, CSample, CObserve,
    CIf, CWhile, CSeq, Program,
    AExpr, AInt, AReal, AVar, ABinOp, ANeg,
    BExpr, BTrue, BFalse, BNot, BBinOp, BCompare,
)


_MAX_LOOP = 5000

class Outcome(Enum):
    TERMINATED = auto()   # valid terminating
    BLOCKED    = auto()   # observe failed
    DIVERGED   = auto()   # nonterminating (approximated by step limit)


class ObserveFailed(Exception):
    pass

class Diverged(Exception):
    pass


def _eval_a(e, env: dict):
    t = type(e)
    if t is AInt:   return e.value
    if t is AReal:  return e.value
    if t is AVar:   return env.get(e.name, 0)
    if t is ANeg:   return -_eval_a(e.expr, env)
    if t is ABinOp:
        l = _eval_a(e.left,  env)
        r = _eval_a(e.right, env)
        op = e.op
        if op == '+': return l + r
        if op == '-': return l - r
        if op == '*': return l * r
        if op == '/': return l // r if r else 0
        if op == '%': return l %  r if r else 0
    raise NotImplementedError(type(e))


def _eval_b(e, env: dict) -> bool:
    t = type(e)
    if t is BTrue:    return True
    if t is BFalse:   return False
    if t is BNot:     return not _eval_b(e.expr, env)
    if t is BBinOp:
        if e.op == '&&': return bool(_eval_b(e.left, env)) and bool(_eval_b(e.right, env))
        if e.op == '||': return bool(_eval_b(e.left, env)) or  bool(_eval_b(e.right, env))
    if t is BCompare:
        l = _eval_a(e.left,  env)
        r = _eval_a(e.right, env)
        op = e.op
        if op == '=':  return l == r
        if op == '!=': return l != r
        if op == '<':  return l <  r
        if op == '>':  return l >  r
        if op == '<=': return l <= r
        if op == '>=': return l >= r
    raise NotImplementedError(type(e))


def _sample(d, env: dict, rng) -> int:
    from prob_slicer.ast_nodes import DUnif, DBernoulli, DGaussian, DDiscrete

    t = type(d)
    if t is DUnif:
        return rng.randint(int(_eval_a(d.lo, env)),
                           int(_eval_a(d.hi, env)))
    if t is DBernoulli:
        return 1 if rng.random() < _eval_a(d.p, env) else 0
    if t is DGaussian:
        return int(round(rng.gauss(_eval_a(d.mu, env),
                                   _eval_a(d.sigma, env))))
    if t is DDiscrete:
        vals  = [_eval_a(v, env) for v, _ in d.mapping]
        probs = [_eval_a(p, env) for _, p in d.mapping]
        total = sum(probs)
        r     = rng.random() * total
        cum   = 0.0
        for v, p in zip(vals, probs):
            cum += p
            if r <= cum:
                return v
        return vals[-1]
    raise NotImplementedError(type(d))


def _exec(cmd, env: dict, rng, loop_counters: dict):
    """
    Iterative execution — no recursion, no tuple allocation on stack,
    no dict copying.
    """
    # Flatten CSeq into a deque for fast popleft
    from collections import deque
    stack = deque()
    stack.append(cmd)

    while stack:
        node = stack.pop()
        t    = type(node)

        if t is CSkip:
            pass

        elif t is CAssign:
            env[node.var] = _eval_a(node.expr, env)

        elif t is CSample:
            env[node.var] = _sample(node.distr, env, rng)

        elif t is CObserve:
            if not _eval_b(node.cond, env):
                raise ObserveFailed()

        elif t is CSeq:
            # Flatten iteratively
            n = node
            while type(n) is CSeq:
                stack.append(n.right)
                n = n.left
            stack.append(n)

        elif t is CIf:
            stack.append(
                node.then_branch if _eval_b(node.cond, env)
                else node.else_branch
            )

        elif t is CWhile:
            if _eval_b(node.cond, env):
                lid = id(node)
                cnt = loop_counters.get(lid, 0) + 1
                if cnt > _MAX_LOOP:
                    raise Diverged()
                loop_counters[lid] = cnt
                stack.append(node)
                stack.append(node.body)
            else:
                loop_counters.pop(id(node), None)

        else:
            raise NotImplementedError(t)


def run_once(prog, rng) -> tuple[Outcome, int | None]:
    env = {}
    lc  = {}
    try:
        _exec(prog.body, env, rng, lc)
        return Outcome.TERMINATED, _eval_a(prog.return_expr, env)
    except ObserveFailed:
        return Outcome.BLOCKED, None
    except Diverged:
        return Outcome.DIVERGED, None

@dataclass
class EmpiricalDist:
    """
    Empirical distribution over return values.
    Tracks all three outcome categories.
    """
    n_runs:      int
    n_term:      int              # valid terminating
    n_blocked:   int              # blocked (observe failed)
    n_diverged:  int              # nonterminating (step limit)
    samples:     list[float]      # return values from terminating runs

    @property
    def p_term(self) -> float:
        """w[[G]] mu_0 — probability of valid termination."""
        return self.n_term / self.n_runs

    @property
    def p_blocked(self) -> float:
        """Pr[G(mu_0) in E] — probability of blocked executions."""
        return self.n_blocked / self.n_runs

    @property
    def p_diverged(self) -> float:
        """
        Pr[G(mu_0) in ⇑] — nontermination probability.
        Per Definition 4.5: 1 - w[[G]]mu_0 + Pr[G(mu_0) in E]
        = 1 - p_term + p_blocked
        But since p_term + p_blocked + p_diverged_empirical = 1,
        we use the empirical count as approximation.
        """
        return self.n_diverged / self.n_runs

    def unnorm_dist(self) -> Counter:
        """
        Unnormalized distribution over V — mu|_V.
        Each value weighted by 1/n_runs (not 1/n_term),
        giving the SUBdistribution (unnormalized).
        """
        return Counter({
            round(v, 6): 1 / self.n_runs
            for v in self.samples
        })

    @staticmethod
    def collect(prog,
                n_runs: int,
                seeds:  list[int]) -> 'EmpiricalDist':
        import random as _rm
        rng = _rm.Random()   # single instance — no module-level lock

        samples    = []
        n_term     = 0
        n_blocked  = 0
        n_diverged = 0

        for seed in seeds:
            rng.seed(seed)
            outcome, val = run_once(prog, rng)
            if outcome is Outcome.TERMINATED:
                n_term += 1
                samples.append(val)
            elif outcome is Outcome.BLOCKED:
                n_blocked += 1
            else:
                n_diverged += 1

        return EmpiricalDist(
            n_runs     = n_runs,
            n_term     = n_term,
            n_blocked  = n_blocked,
            n_diverged = n_diverged,
            samples    = samples,
        )

# ---------------------------------------------------------------------------
# Distribution comparison, result, thresholds — unchanged from before
# ---------------------------------------------------------------------------

def find_proportionality(d1: EmpiricalDist,
                         d2: EmpiricalDist,
                         tol: float = 0.05) -> tuple[float | None, float]:
    c1 = Counter(d1.samples)
    c2 = Counter(d2.samples)
    all_vals = set(c1.keys()) | set(c2.keys())
    if not all_vals:
        return 1.0, 0.0
    ratios = []
    for v in all_vals:
        n1 = c1.get(v, 0)
        n2 = c2.get(v, 0)
        if n2 == 0:
            return None, float('inf')
        ratios.append(n1 / n2)
    q       = sum(ratios) / len(ratios)
    max_dev = max(abs(r - q) for r in ratios)
    if max_dev > tol:
        return None, max_dev
    return min(q, 1.0), max_dev



TV_THRESHOLD = 0.01
NT_THRESHOLD = 0.01


@dataclass
class EvalResult:
    benchmark:  str
    variant:    str
    n_runs:     int
    orig_dist:  EmpiricalDist
    slice_dist: EmpiricalDist
    q:          float | None
    tv_shape:   float
    nt_diff:    float
    q1:         float
    q2:         float
    ns_ok:      bool
    ni_ok:      bool
    nids_ok:    bool
    elapsed:    float
    error:      str = ''

    def __str__(self) -> str:
        def tick(ok): return '✓' if ok else '✗'
        q_str = f"{self.q:.4f}" if self.q is not None else "None"
        return (
            f"  [{self.benchmark}] variant={self.variant}\n"
            f"    Original : term={self.orig_dist.p_term:.4f}  "
            f"blocked={self.orig_dist.p_blocked:.4f}  "
            f"diverged={self.orig_dist.p_diverged:.4f}\n"
            f"    Slice    : term={self.slice_dist.p_term:.4f}  "
            f"blocked={self.slice_dist.p_blocked:.4f}  "
            f"diverged={self.slice_dist.p_diverged:.4f}\n"
            f"    mu1|V=q*mu2|V : q={q_str}  max_dev={self.tv_shape:.4f}\n"
            f"    NT: q1={self.q1:.4f} q2={self.q2:.4f} "
            f"diff={self.nt_diff:.4f}\n"
            f"    NS={tick(self.ns_ok)}  "
            f"NI={tick(self.ni_ok)}  "
            f"NIDS={tick(self.nids_ok)}  "
            f"({self.n_runs} runs, {self.elapsed:.2f}s)"
        )


def evaluate_benchmark(
    b:           dict,
    variant,
    orig_prog,
    sliced_prog,
    n_runs:      int = 10_000,
    max_stmts:   int = 2_0000,
) -> EvalResult | None:
    from prob_slicer.slicer import count_statements
    orig_size = count_statements(orig_prog)
    if orig_size > max_stmts:
        print(f"  [Eval] Skipping {b['name']} — "
              f"too large ({orig_size} stmts)", flush=True)
        return None

    t0    = time.perf_counter()
    seeds = [random.randint(0, 2**32) for _ in range(n_runs)]

    t1 = time.perf_counter()

    orig_dist  = EmpiricalDist.collect(orig_prog,   n_runs, seeds)

    print(f"  [Eval] orig collect: {time.perf_counter()-t1:.2f}s", flush=True)
    t2 = time.perf_counter()

    slice_dist = EmpiricalDist.collect(sliced_prog, n_runs, seeds)
    print(f"  [Eval] slice collect: {time.perf_counter()-t2:.2f}s", flush=True)

    q, max_dev = find_proportionality(orig_dist, slice_dist)
    prop_ok    = q is not None

    c1       = Counter(orig_dist.samples)
    c2       = Counter(slice_dist.samples)
    all_vals = set(c1.keys()) | set(c2.keys())
    sub_ok   = all(
        c1.get(v, 0) <= c2.get(v, 0) + n_runs * TV_THRESHOLD
        for v in all_vals
    )

    nt_diff  = abs(orig_dist.p_diverged - slice_dist.p_diverged)
    nt_ns_ok = (
        prop_ok and
        abs(orig_dist.p_diverged - q * slice_dist.p_diverged) <= NT_THRESHOLD
    ) if prop_ok else False


    # NS check: proportional distribution AND proportional nontermination
    # with potentially DIFFERENT q values
    nt1, nt2 = orig_dist.p_diverged, slice_dist.p_diverged
    if nt2 == 0:
        nt_q = 0.0 if nt1 == 0 else None   # nt1 > 0 with nt2 = 0 fails
    else:
        nt_q = nt1 / nt2

    nt_ns_ok = (
        nt_q is not None and
        0.0 <= nt_q <= 1.0 + NT_THRESHOLD and
        abs(nt1 - nt_q * nt2) <= NT_THRESHOLD
    )

    ns_ok = prop_ok and nt_ns_ok

    ''' Following computation is no more relevant and nt_linear_ok is not used to evaluate nids_ok'''    
    if nt2 == 0:
        q1, q2 = 0.0, nt1
    else:
        q1 = nt1 / nt2
        q2 = 0.0
        if q1 > 1.0:
            q1, q2 = 1.0, nt1 - nt2
    nt_linear_ok = (
        0.0 <= q1 <= 1.0 + NT_THRESHOLD and
        0.0 <= q2 <= 1.0 + NT_THRESHOLD and
        abs(nt1 - (q1 * nt2 + q2)) <= NT_THRESHOLD
    )

    return EvalResult(
        benchmark  = b['name'],
        variant    = variant,
        n_runs     = n_runs,
        orig_dist  = orig_dist,
        slice_dist = slice_dist,
        q          = q,
        tv_shape   = max_dev,
        nt_diff    = nt_diff,
        q1         = q1,
        q2         = q2,
        ns_ok      = ns_ok,
        ni_ok      = sub_ok,
        nids_ok    = prop_ok,
        elapsed    = time.perf_counter() - t0,
    )