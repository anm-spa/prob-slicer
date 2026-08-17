"""
ast_nodes.py
============
Algebraic-style AST nodes for the probabilistic language.
Each node carries an optional `node_id` assigned during CFG construction
so that dependence edges can reference AST nodes directly.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ─── Unique node ID counter ───────────────────────────────────────────────────

_counter = 0

def fresh_id() -> int:
    global _counter
    _counter += 1
    return _counter

def reset_ids() -> None:
    global _counter
    _counter = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Arithmetic Expressions
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AExpr:
    """Base class for arithmetic expressions."""
    def vars(self) -> set[str]:
        """Return all variable names referenced in this expression."""
        raise NotImplementedError


@dataclass
class AInt(AExpr):
    value: int
    def vars(self): return set()
    def __str__(self): return str(self.value)


@dataclass
class AReal(AExpr):
    value: float
    def vars(self): return set()
    def __str__(self): return str(self.value)


@dataclass
class AVar(AExpr):
    name: str
    def vars(self): return {self.name}
    def __str__(self): return self.name


@dataclass
class ANeg(AExpr):
    expr: AExpr
    def vars(self): return self.expr.vars()
    def __str__(self): return f"-{self.expr}"


@dataclass
class ABinOp(AExpr):
    op: str          # '+' | '-' | '*' | '/' | '%'
    left: AExpr
    right: AExpr
    def vars(self): return self.left.vars() | self.right.vars()
    def __str__(self): return f"({self.left} {self.op} {self.right})"


# ═══════════════════════════════════════════════════════════════════════════════
# Boolean Expressions
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BExpr:
    """Base class for boolean expressions."""
    def vars(self) -> set[str]:
        raise NotImplementedError


@dataclass
class BTrue(BExpr):
    def vars(self): return set()
    def __str__(self): return "true"


@dataclass
class BFalse(BExpr):
    def vars(self): return set()
    def __str__(self): return "false"


@dataclass
class BCompare(BExpr):
    op: str          # '=' | '!=' | '<=' | '>=' | '<' | '>'
    left: AExpr
    right: AExpr
    def vars(self): return self.left.vars() | self.right.vars()
    def __str__(self): return f"({self.left} {self.op} {self.right})"


@dataclass
class BNot(BExpr):
    expr: BExpr
    def vars(self): return self.expr.vars()
    def __str__(self): return f"!({self.expr})"


@dataclass
class BBinOp(BExpr):
    op: str          # '&&' | '||'
    left: BExpr
    right: BExpr
    def vars(self): return self.left.vars() | self.right.vars()
    def __str__(self): return f"({self.left} {self.op} {self.right})"


# ═══════════════════════════════════════════════════════════════════════════════
# Distribution Expressions
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Distr:
    """Base class for distribution expressions."""
    def vars(self) -> set[str]:
        raise NotImplementedError


@dataclass
class DUnif(Distr):
    """Uniform distribution over integers: unif[lo, hi]"""
    lo: AExpr
    hi: AExpr
    def vars(self): return self.lo.vars() | self.hi.vars()
    def __str__(self): return f"unif[{self.lo}, {self.hi}]"


@dataclass
class DBernoulli(Distr):
    """Bernoulli(p)"""
    p: AExpr
    def vars(self): return self.p.vars()
    def __str__(self): return f"bernoulli({self.p})"


@dataclass
class DGaussian(Distr):
    """Gaussian(mu, sigma)"""
    mu: AExpr
    sigma: AExpr
    def vars(self): return self.mu.vars() | self.sigma.vars()
    def __str__(self): return f"gaussian({self.mu}, {self.sigma})"


@dataclass
class DDiscrete(Distr):
    """Finite support distribution: distr{v1->p1, ..., vn->pn}"""
    mapping: List[Tuple[AExpr, AExpr]]   # (value, probability) pairs
    def vars(self):
        result = set()
        for v, p in self.mapping:
            result |= v.vars() | p.vars()
        return result
    def __str__(self):
        pairs = ", ".join(f"{v}->{p}" for v, p in self.mapping)
        return f"distr{{{pairs}}}"


# ═══════════════════════════════════════════════════════════════════════════════
# Commands (Statements)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Cmd:
    """Base class for commands. node_id is assigned by the CFG builder."""
    node_id: int = field(default_factory=fresh_id, init=False)


@dataclass
class CSkip(Cmd):
    def __str__(self): return "skip"


@dataclass
class CAssign(Cmd):
    """x := a"""
    var: str
    expr: AExpr
    def defs(self) -> set[str]: return {self.var}
    def uses(self) -> set[str]: return self.expr.vars()
    def __str__(self): return f"{self.var} := {self.expr}"


@dataclass
class CSample(Cmd):
    """x :~ d   (probabilistic assignment)"""
    var: str
    distr: Distr
    def defs(self) -> set[str]: return {self.var}
    def uses(self) -> set[str]: return self.distr.vars()
    def __str__(self): return f"{self.var} :~ {self.distr}"


@dataclass
class CSeq(Cmd):
    """c1 ; c2"""
    left: Cmd
    right: Cmd
    def __str__(self): return f"{self.left};\n{self.right}"


@dataclass
class CIf(Cmd):
    """if b then c1 else c2 end"""
    cond: BExpr
    then_branch: Cmd
    else_branch: Cmd
    def uses(self) -> set[str]: return self.cond.vars()
    def __str__(self):
        return (f"if {self.cond} then\n  {self.then_branch}\n"
                f"else\n  {self.else_branch}\nend")


@dataclass
class CWhile(Cmd):
    """while b do c end"""
    cond: BExpr
    body: Cmd
    def uses(self) -> set[str]: return self.cond.vars()
    def __str__(self): return f"while {self.cond} do\n  {self.body}\nend"


@dataclass
class CObserve(Cmd):
    """observe b  — conditions on b being true (filter semantics)"""
    cond: BExpr
    def uses(self) -> set[str]: return self.cond.vars()
    def __str__(self): return f"observe({self.cond})"

@dataclass
class CReturn(Cmd):
    """Synthetic return node — not in source, added by CFG builder."""
    expr: AExpr
    def uses(self) -> set[str]: return self.expr.vars()
    def defs(self) -> set[str]: return set()
    def __str__(self): return f"return {self.expr}"

# ═══════════════════════════════════════════════════════════════════════════════
# Program
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Program:
    """p ::= c ; return a"""
    body: Cmd
    return_expr: AExpr

    def __str__(self):
        return f"{self.body};\nreturn {self.return_expr}"
