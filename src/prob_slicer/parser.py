"""
parser.py
=========
Hand-written recursive-descent parser for the probabilistic language.
Produces AST nodes from prob_slicer.ast_nodes.

Usage:
    from prob_slicer.parser import parse
    prog = parse(source_string)

This parser mirrors the ProbLang.g4 grammar exactly so that the
ANTLR4 grammar and this implementation stay in sync.  Once the project
matures you can swap this out for the ANTLR4-generated parser with the
visitor in antlr_builder.py.
"""

from __future__ import annotations
import re
from typing import List, Tuple
from .ast_nodes import (
    AInt, AReal, AVar, ANeg, ABinOp,
    BTrue, BFalse, BCompare, BNot, BBinOp, Cmd,
    DUnif, DBernoulli, DGaussian, DDiscrete,
    CSkip, CAssign, CSample, CSeq, CIf, CWhile, CObserve,
    Program,
)

# ─── Tokenizer ────────────────────────────────────────────────────────────────

TOKEN_SPEC = [
    ('COMMENT', r'//[^\n]*'),
    ('REAL',    r'\d+\.\d+'),
    ('INT',     r'\d+'),
    ('OP2',     r':~|:=|->|<=|>=|!=|&&|\|\|'),
    ('OP1',     r'[+\-*/%=<>!;,\[\]{}()]'),
    ('ID',      r'[a-zA-Z_][a-zA-Z0-9_]*'),
    ('WS',      r'[ \t\r\n]+'),
]

_TOKEN_RE = re.compile('|'.join(f'(?P<{name}>{pat})' for name, pat in TOKEN_SPEC))

KEYWORDS = {
    'skip', 'return', 'if', 'then', 'else', 'end',
    'while', 'do', 'observe', 'true', 'false',
    'unif', 'bernoulli', 'gaussian', 'distr',
}


def tokenize(src: str) -> List[Tuple[str, str]]:
    tokens = []
    line   = 1
    col    = 1
    for m in _TOKEN_RE.finditer(src):
        kind = m.lastgroup
        val  = m.group()
        start   = m.start()
        before  = src[:start]
        m_line  = before.count('\n') + 1
        m_col   = start - before.rfind('\n')
        if kind in ('WS', 'COMMENT'):
            continue
        if kind == 'ID' and val in KEYWORDS:
            kind = val.upper()
        tokens.append((kind, val, m_line, m_col))
    return tokens


# ─── Parser ───────────────────────────────────────────────────────────────────

class ParseError(Exception):
     def __init__(self, msg: str, line: int = 0, col: int = 0):
        self.line = line
        self.col  = col
        loc = f" [line {line}, col {col}]" if line else ""
        super().__init__(f"{msg}{loc}")


class Parser:
    def __init__(self, tokens: List[Tuple[str, str]]):
        self.tokens = tokens
        self.pos    = 0

    # ── Primitives ──────────────────────────────────────────────────────────

    def peek(self) -> Tuple[str, str] | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def peek_kind(self) -> str | None:
        t = self.peek()
        return t[0] if t else None
    
    def _loc(self) -> Tuple[int, int]:
        """Return (line, col) of current token, or (0,0) at EOF."""
        t = self.peek()
        return (t[2], t[3]) if t else (0, 0)

    def consume(self, expected_kind: str | None = None, expected_val: str | None = None) -> Tuple[str, str]:
        t = self.peek()
        if t is None:
            raise ParseError(f"Unexpected EOF, expected {expected_kind!r}")
        kind, val, line, col = t      
        if expected_kind and kind != expected_kind:
            raise ParseError(f"Expected token kind {expected_kind!r}, "f"got {kind!r} ({val!r})",line, col)        
        if expected_val and val != expected_val:
            raise ParseError(
                f"Expected token value {expected_val!r}, "
                f"got {val!r}",
                line, col)        
        self.pos += 1
        return t

    def match(self, *kinds_or_vals) -> bool:
        t = self.peek()
        if t is None:
            return False
        kind, val,_,_ = t
        return kind in kinds_or_vals or val in kinds_or_vals

    # ── Program ─────────────────────────────────────────────────────────────

    def parse_program(self) -> Program:
        body = self.parse_command()
        self.consume('RETURN')
        ret  = self.parse_aexpr()
        self.consume(expected_val=';')
        return Program(body=body, return_expr=ret)

    # ── Commands ────────────────────────────────────────────────────────────

    def parse_command(self) -> 'Cmd':
        """Parse a (possibly sequential) command."""
        cmd = self.parse_single_command()
        while self.peek_kind() not in (None, 'RETURN', 'ELSE', 'END'):
            right = self.parse_single_command()
            cmd   = CSeq(left=cmd, right=right)
        return cmd

    def parse_single_command(self) -> 'Cmd':
        t = self.peek()
        k = t[0] if t else None
        v = t[1] if t else None
        line, col = self._loc()

        if k == 'SKIP':
            self.consume('SKIP')
            self.consume(expected_val=';')
            return CSkip()

        elif k == 'IF':
            return self.parse_if()

        elif k == 'WHILE':
            return self.parse_while()

        elif k == 'OBSERVE':
            self.consume('OBSERVE')
            self.consume(expected_val='(')
            cond = self.parse_bexpr()
            self.consume(expected_val=')')
            self.consume(expected_val=';')
            return CObserve(cond=cond)

        elif k == 'ID':
            # Look ahead for := or :~
            name = self.consume('ID')[1]
            op_t = self.consume('OP2')
            op_val = op_t[1]
            if op_val == ':=':
                expr = self.parse_aexpr()
                self.consume(expected_val=';')
                return CAssign(var=name, expr=expr)
            elif op_val == ':~':
                d = self.parse_distr()
                self.consume(expected_val=';')
                return CSample(var=name, distr=d)
            else:
                raise ParseError(
                    f"Expected ':=' or ':~' after variable, "
                    f"got {op_val!r}",
                    line, col)
        else:
                raise ParseError(f"Unexpected token {k!r} ({v!r}) "f"while parsing command",
                line, col)
    def parse_if(self) -> CIf:
        self.consume('IF')
        cond = self.parse_bexpr()
        self.consume('THEN')
        then_branch = self.parse_command()
        self.consume('ELSE')
        else_branch = self.parse_command()
        self.consume('END')
        return CIf(cond=cond, then_branch=then_branch, else_branch=else_branch)

    def parse_while(self) -> CWhile:
        self.consume('WHILE')
        cond = self.parse_bexpr()
        self.consume('DO')
        body = self.parse_command()
        self.consume('END')
        return CWhile(cond=cond, body=body)

    # ── Arithmetic expressions (Pratt / precedence climbing) ────────────────

    def parse_aexpr(self, min_prec: int = 0):
        left = self.parse_aexpr_atom()
        while True:
            t = self.peek()
            if t is None:
                break
            _, val,_,_ = t
            prec = {'+': 1, '-': 1, '*': 2, '/': 2, '%': 2}.get(val)
            if prec is None or prec < min_prec:
                break
            self.consume()
            right = self.parse_aexpr(prec + 1)
            left  = ABinOp(op=val, left=left, right=right)
        return left

    def parse_aexpr_atom(self):
        t = self.peek()
        if t is None:
            raise ParseError("Unexpected EOF in arithmetic expression")
        kind, val, line, col = t

        if kind == 'REAL':
            self.consume()
            return AReal(float(val))
        if kind == 'INT':
            self.consume()
            return AInt(int(val))
        if kind == 'ID':
            self.consume()
            return AVar(val)
        if val == '-':
            self.consume()
            expr = self.parse_aexpr_atom()
            return ANeg(expr)
        if val == '(':
            self.consume(expected_val='(')
            expr = self.parse_aexpr()
            self.consume(expected_val=')')
            return expr
        
        raise ParseError(
            f"Unexpected token in arithmetic expression: "
            f"{kind!r} ({val!r})",
            line, col)
    # ── Boolean expressions ──────────────────────────────────────────────────

    def parse_bexpr(self, min_prec: int = 0):
        left = self.parse_bexpr_atom()
        while True:
            t = self.peek()
            if t is None:
                break
            _, val, line, col = t
            prec = {'||': 1, '&&': 2}.get(val)
            if prec is None or prec < min_prec:
                break
            self.consume()
            right = self.parse_bexpr(prec + 1)
            left  = BBinOp(op=val, left=left, right=right)
        return left

    def parse_bexpr_atom(self):
        t = self.peek()
        if t is None:
            raise ParseError("Unexpected EOF in boolean expression")
        kind, val, line, col = t

        if kind == 'TRUE':
            self.consume(); return BTrue()
        if kind == 'FALSE':
            self.consume(); return BFalse()
        if val == '!':
            self.consume()
            expr = self.parse_bexpr_atom()
            return BNot(expr)
        if val == '(':
            self.consume(expected_val='(')
            expr = self.parse_bexpr()
            self.consume(expected_val=')')
            return expr

        # Otherwise it must be a comparison: aexpr OP aexpr
        left = self.parse_aexpr()
        cmp_t = self.peek()
        if cmp_t is None:
            raise ParseError("Expected comparison operator")
        _, cmp_op, line, col = cmp_t
        if cmp_op not in ('=', '!=', '<=', '>=', '<', '>'):
            raise ParseError(
                f"Expected comparison operator, got {cmp_op!r}",
                line, col)
        self.consume()
        right = self.parse_aexpr()
        return BCompare(op=cmp_op, left=left, right=right)

    # ── Distribution expressions ─────────────────────────────────────────────

    def parse_distr(self):
        t=self.peek()
        k = t[0] if t else None
        line, col = self._loc()
        if k == 'UNIF':
            self.consume('UNIF')
            self.consume(expected_val='[')
            lo = self.parse_aexpr()
            self.consume(expected_val=',')
            hi = self.parse_aexpr()
            self.consume(expected_val=']')
            return DUnif(lo=lo, hi=hi)

        if k == 'BERNOULLI':
            self.consume('BERNOULLI')
            self.consume(expected_val='(')
            p = self.parse_aexpr()
            self.consume(expected_val=')')
            return DBernoulli(p=p)

        if k == 'GAUSSIAN':
            self.consume('GAUSSIAN')
            self.consume(expected_val='(')
            mu = self.parse_aexpr()
            self.consume(expected_val=',')
            sigma = self.parse_aexpr()
            self.consume(expected_val=')')
            return DGaussian(mu=mu, sigma=sigma)

        if k == 'DISTR':
            self.consume('DISTR')
            self.consume(expected_val='{')
            mapping = []
            while not self.match('}'):
                v = self.parse_aexpr()
                self.consume('OP2', '->')
                p = self.parse_aexpr()
                mapping.append((v, p))
                if self.match(','):
                    self.consume(expected_val=',')
            self.consume(expected_val='}')
            return DDiscrete(mapping=mapping)

        raise ParseError(
            f"Unknown distribution: {self.peek()}",
            line, col)

# ─── Public API ───────────────────────────────────────────────────────────────

def parse(source: str) -> Program:
    """Parse a source string and return a Program AST."""
    tokens = tokenize(source)
    parser = Parser(tokens)
    prog   = parser.parse_program()
    if parser.pos != len(parser.tokens):
        remaining = parser.tokens[parser.pos:]
        t = remaining[0]
        raise ParseError(
            f"Unparsed tokens remaining: {remaining[:5]}",
            t[2], t[3])
    return prog
