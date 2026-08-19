"""
translate_prodigy.py
====================
Translates Prodigy pGCL programs (.pgcl) into the prob-slicer language (.prob)
and saves them under benchmarks/prodigy/.

pGCL constructs handled:
  nat x;                        -> (dropped — declarations ignored)
  nat x [0, 10];                -> (dropped)
  x := E                        -> x := E;
  x := unif(a, b)               -> x :~ unif[a, b];
  x := d(p)   (bernoulli etc.)  -> x :~ unif[0, 1];   (best-effort)
  observe(B)                    -> observe(B);
  skip                          -> skip
  if (B) { P } else { Q }       -> if (B) then P else Q
  if (B) { P }                  -> if (B) then P else skip
  while (B) { P }               -> while (B) do P end
  { P } [p] { Q }               -> probabilistic choice expanded (see below)
  tick(E)                       -> (dropped — cost annotation)
"""

from ast import stmt
import re
import sys
from pathlib import Path
import traceback

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# benchmarks/ and imported-benchmarks/ live at the repo root, one level
# up from bench-src/
REPO_ROOT      = Path(__file__).resolve().parent.parent
BENCHMARKS_DIR = REPO_ROOT / 'benchmarks'
PRODIGY_OUT    = BENCHMARKS_DIR / 'prodigy'


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

# Token types
TK_NAT      = 'NAT'        # nat
TK_IDENT    = 'IDENT'      # variable / keyword
TK_INT      = 'INT'        # integer literal
TK_FLOAT    = 'FLOAT'      # float literal
TK_ASSIGN   = 'ASSIGN'     # :=
TK_LBRACE   = 'LBRACE'     # {
TK_RBRACE   = 'RBRACE'     # }
TK_LPAREN   = 'LPAREN'     # (
TK_RPAREN   = 'RPAREN'     # )
TK_LBRACKET = 'LBRACKET'   # [
TK_RBRACKET = 'RBRACKET'   # ]
TK_SEMI     = 'SEMI'       # ;
TK_COMMA    = 'COMMA'      # ,
TK_PROB     = 'PROB'       # [p] between two blocks
TK_EOF      = 'EOF'

_TOKEN_RE = re.compile(
    r'\s*(?:'
    r'(#[^\n]*)'                            # # directive — skip line
    r'|(\?[^\n]*)'                          # ? directive — skip line
    r'|(!(?!=)[^\n]*)'                      # ! directive — skip line
    r'|(/\*.*?\*/)'                         # block comment
    r'|(//[^\n]*)'                          # line comment
    r'|(\d+\.\d+)'                          # float
    r'|(\d+)'                               # int
    r'|(:=)'                                # :=
    r'|(\{)'                                # {
    r'|(\})'                                # }
    r'|(\()'                                # (
    r'|(\))'                                # )
    r'|(\[)'                                # [
    r'|(\])'                                # ]
    r'|(;)'                                 # ;
    r'|(,)'                                 # ,
    r'|(\|\|)'                              # || — must come before |
    r'|(&&)'                                # && — must come before &
    r'|([A-Za-z_][A-Za-z0-9_]*)\^(\d+)'        # var^int — expand to repeated multiply
    r'|([A-Za-z_][A-Za-z0-9_]*)'           # identifier/keyword
    r'|([+\-*/!<>=%&|]+)'                  # operators (single chars)
    r')',
    re.DOTALL,
)

def tokenise(src: str) -> list[tuple[str, str]]:
    tokens = []
    pos    = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            pos += 1
            continue
        pos = m.end()
        (hash_dir, quest_dir, bang_dir,
         block_comment, line_comment, flt, integer, assign,
         lb, rb, lp, rp, lbk, rbk, semi, comma,
         or_op, and_op, 
         var_pow_base, var_pow_exp, ident, op) = m.groups()

        if hash_dir or quest_dir or bang_dir or block_comment or line_comment:
            continue
        if flt:
            tokens.append((TK_FLOAT, flt))
        elif integer:
            tokens.append((TK_INT, integer))
        elif assign:
            tokens.append((TK_ASSIGN, ':='))
        elif lb:
            tokens.append((TK_LBRACE, '{'))
        elif rb:
            tokens.append((TK_RBRACE, '}'))
        elif lp:
            tokens.append((TK_LPAREN, '('))
        elif rp:
            tokens.append((TK_RPAREN, ')'))
        elif lbk:
            tokens.append((TK_LBRACKET, '['))
        elif rbk:
            tokens.append((TK_RBRACKET, ']'))
        elif semi:
            tokens.append((TK_SEMI, ';'))
        elif comma:
            tokens.append((TK_COMMA, ','))
        elif or_op:
            tokens.append(('OP', '||'))    # normalise || 
        elif and_op:
            tokens.append(('OP', '&&'))    # normalise &&
        elif var_pow_base and var_pow_exp:
            base = var_pow_base
            exp  = int(var_pow_exp)
            if exp == 0:
                tokens.append(('OP', '1'))
            elif exp == 1:
                tokens.append((TK_IDENT, base))
            else:
                tokens.append(('OP', ' * '.join([base] * exp)))
        elif ident:
            tokens.append((TK_IDENT, ident))
        elif op:
            # Normalise single & and | to && and ||
            tv = op
            tv = tv.replace('&&', '\x00').replace('||', '\x01')  # protect doubles
            tv = tv.replace('&', '&&').replace('|', '||')         # fix singles
            tv = tv.replace('\x00', '&&').replace('\x01', '||')   # restore doubles
            tokens.append(('OP', tv))
    tokens.append((TK_EOF, ''))
    return tokens
# ---------------------------------------------------------------------------
# Parser → AST (simple recursive descent)
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos    = 0

    def peek(self) -> tuple:
        return self.tokens[self.pos]

    def consume(self, kind=None, value=None):
        tk, tv = self.tokens[self.pos]
        if kind and tk != kind:
            raise SyntaxError(
                f"Expected token {kind} but got {tk}={tv!r} "
                f"at position {self.pos}"
            )
        if value and tv != value:
            raise SyntaxError(
                f"Expected value {value!r} but got {tv!r} "
                f"at position {self.pos}"
            )
        self.pos += 1
        return tv
    
    def _try_consume_semi(self):
        """Consume a semicolon if present — makes semicolons optional."""
        if self.peek()[0] == TK_SEMI:
            self.pos += 1

    def at_eof(self) -> bool:
        return self.tokens[self.pos][0] == TK_EOF

    # --- expression (collected as raw token string) -----------------------

    def parse_expr_raw_old(self, stop_tokens: set) -> str:
        """Collect tokens until a stop token is encountered."""
        parts = []
        depth_paren   = 0
        depth_bracket = 0
        while True:
            tk, tv = self.peek()
            if tk == TK_EOF:
                break
            if tk == TK_LPAREN:
                depth_paren += 1
            elif tk == TK_RPAREN:
                if depth_paren == 0:
                    break
                depth_paren -= 1
            elif tk == TK_LBRACKET:
                depth_bracket += 1
            elif tk == TK_RBRACKET:
                if depth_bracket == 0 and (TK_RBRACKET in stop_tokens
                                            or 'PROB' in stop_tokens):
                    break
                depth_bracket -= 1
            elif tk in stop_tokens and depth_paren == 0 and depth_bracket == 0:
                break
            parts.append(tv)
            self.pos += 1
        raw= ' '.join(parts)
        raw = re.sub(r'&(?!&)', '&&', raw)
        raw = re.sub(r'\|(?!\|)', '||', raw)
        return raw

    def parse_expr_raw(self, stop_tokens: set) -> str:
        """Collect tokens until a stop token is encountered."""
        parts         = []
        depth_paren   = 0
        depth_brace   = 0          # ADD: track brace depth
        depth_bracket = 0
        while True:
            tk, tv = self.peek()
            if tk == TK_EOF:
                break
            if tk == TK_LPAREN:
                depth_paren += 1
            elif tk == TK_RPAREN:
                if depth_paren == 0:
                    break
                depth_paren -= 1
            elif tk == TK_LBRACE:          # ADD: stop at { when not inside parens
                if depth_paren == 0:
                    break
                depth_brace += 1
            elif tk == TK_RBRACE:          # ADD: stop at } when not inside parens
                if depth_paren == 0:
                    break
                depth_brace -= 1
            elif tk == TK_LBRACKET:
                depth_bracket += 1
            elif tk == TK_RBRACKET:
                if depth_bracket == 0 and (TK_RBRACKET in stop_tokens
                                            or 'PROB' in stop_tokens):
                    break
                depth_bracket -= 1
            elif tk in stop_tokens and depth_paren == 0 and depth_bracket == 0:
                break
            tv = tv.replace('not', '!')
            parts.append(tv)
            self.pos += 1
        return ' '.join(parts)

    # --- rvalue -----------------------------------------------------------

    def parse_rvalue(self) -> tuple[str, str]:
        _KEYWORDS = {
            'if', 'else', 'while', 'observe', 'skip',
            'abort', 'nat', 'rparam', 'nparam', 'real',
            'bool', 'int', 'tick', 'return'
        }

        tk, tv = self.peek()

        # unif(a, b)
        if tk == TK_IDENT and tv == 'unif':
            self.pos += 1
            self.consume(TK_LPAREN)
            lo = self.parse_expr_raw({TK_COMMA})
            self.consume(TK_COMMA)
            hi = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            return ('sample', f'unif[{lo.strip()}, {hi.strip()}]')

        # bernoulli(p) — ProbLang has a real bernoulli(p) distribution,
        # so preserve the exact probability expression instead of
        # discarding it and approximating with a 50/50 unif[0,1].
        if tk == TK_IDENT and tv == 'bernoulli':
            self.pos += 1
            self.consume(TK_LPAREN)
            p = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            return ('sample', f'bernoulli({p.strip()})')

        # other known distributions with no ProbLang equivalent — best
        # effort placeholder (probability information is genuinely lost
        # here, since ProbLang has no geometric/poisson/binomial/etc.)
        if tk == TK_IDENT and tv in (
                'geometric', 'poisson', 'iid',
                'binomial', 'negativebinomial', 'hypergeometric'):
            self.pos += 1
            self.consume(TK_LPAREN)
            _args = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            return ('sample', 'unif[0, 1]')

        # plain expression — stop at keywords, braces, semicolons,
        # or IDENT followed by := (start of next assignment)
        expr_parts  = []
        depth_paren = 0
        while True:
            tk, tv = self.peek()
            if tk == TK_EOF:
                break
            if tk == TK_LPAREN:
                depth_paren += 1
                expr_parts.append(tv)
                self.pos += 1
                continue
            if tk == TK_RPAREN:
                if depth_paren == 0:
                    break
                depth_paren -= 1
                expr_parts.append(tv)
                self.pos += 1
                continue
            if depth_paren == 0:
                # stop at statement boundaries
                if tk in (TK_SEMI, TK_RBRACE, TK_LBRACE, TK_EOF):
                    break
                # stop at keywords
                if tk == TK_IDENT and tv in _KEYWORDS:
                    break
                # stop at IDENT := (start of next assignment)
                if tk == TK_IDENT:
                    next_pos = self.pos + 1
                    if (next_pos < len(self.tokens) and
                            self.tokens[next_pos][0] == TK_ASSIGN):
                        break
            expr_parts.append(tv)
            self.pos += 1

        return ('assign', ' '.join(expr_parts).strip())
        # --- statement --------------------------------------------------------

    def parse_stmt(self) -> list[str]:
        """Return a list of output lines for one statement."""
        tk, tv = self.peek()

        # declarations: nat x; rparam p; nparam n; real x; bool x; int x;
        if tk == TK_IDENT and tv in ('nat', 'rparam', 'nparam', 'real', 'bool', 'int'):
            self.pos += 1
            self.consume(TK_IDENT)
            if self.peek()[0] == TK_LBRACKET:
                self.pos += 1
                self.parse_expr_raw({TK_RBRACKET})
                self.consume(TK_RBRACKET)
            self._try_consume_semi()
            return []

        # abort — divergence, translate to nonterminating loop
        if tk == TK_IDENT and tv == 'abort':
            self.pos += 1
            self._try_consume_semi()
            return ['while (true) do', '  skip;', 'end']

        # skip
        if tk == TK_IDENT and tv == 'skip':
            self.pos += 1
            self._try_consume_semi()
            return ['skip;']

        # tick(E) — drop cost annotations
        if tk == TK_IDENT and tv == 'tick':
            self.pos += 1
            self.consume(TK_LPAREN)
            self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            self._try_consume_semi()
            return ['skip;']

        # observe(B)
        if tk == TK_IDENT and tv == 'observe':
            self.pos += 1
            self.consume(TK_LPAREN)
            cond = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            self._try_consume_semi()
            return [f'observe({cond.strip()});']

        # if (B) { P } else { Q }
        if tk == TK_IDENT and tv == 'if':
            self.pos += 1
            self.consume(TK_LPAREN)
            cond = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            then_lines = self.parse_block()
            else_lines = []
            if self.peek() == (TK_IDENT, 'else'):
                self.pos += 1
                else_lines = self.parse_block()
            if not else_lines:
                else_lines = ['  skip;']
            return (
                [f'if ({cond.strip()}) then']
                + [f'  {l}' for l in then_lines]
                + ['else']
                + [f'  {l}' for l in else_lines]
                + ['end']
            )

        # while (B) { P }
        if tk == TK_IDENT and tv == 'while':
            self.pos += 1
            self.consume(TK_LPAREN)
            cond = self.parse_expr_raw({TK_RPAREN})
            self.consume(TK_RPAREN)
            print(f"DEBUG while: cond={cond!r}, next={self.peek()}", flush=True)

            body_lines = self.parse_block()
            print(f"DEBUG while: body={body_lines}", flush=True)

            return (
                [f'while ({cond.strip()}) do']
                + [f'  {l}' for l in body_lines]
                + ['end']
            )

        # { P } [p] { Q } — probabilistic choice
        if tk == TK_LBRACE:
            left_lines = self.parse_block()
            print(f"DEBUG after left block, next token: {self.peek()}", flush=True)

            while self.peek()[0] == TK_LBRACKET:
                print(f"DEBUG entering prob choice", flush=True)

                left_lines = self.parse_prob_choice(left_lines)
                print(f"DEBUG after prob choice, next token: {self.peek()}", flush=True)

            return left_lines

        # x := rvalue
        if tk == TK_IDENT:
            var = tv
            self.pos += 1
            if self.peek()[0] == TK_ASSIGN:
                self.pos += 1
                kind, expr = self.parse_rvalue()
                self._try_consume_semi()
                if kind == 'sample':
                    return [f'{var} :~ {expr};']
                else:
                    return [f'{var} := {expr};']
            self._try_consume_semi()
            return []

        # unrecognised token — skip
        self.pos += 1
        return []

    def parse_block(self) -> list[str]:
        """Parse { stmt* } and return lines (without outer braces)."""
        self.consume(TK_LBRACE)
        lines = []
        while self.peek()[0] not in (TK_RBRACE, TK_EOF):
            lines.extend(self.parse_stmt())
        if self.peek()[0] == TK_RBRACE:
            self.consume(TK_RBRACE)
        return lines if lines else ['skip;']
    
    def parse_prob_choice(self, left_lines: list[str]) -> list[str]:
        self.pos += 1  # consume [
        prob = self.parse_expr_raw({TK_RBRACKET})
        print(f"DEBUG prob_choice: prob={prob!r}, next={self.peek()}", flush=True)

        self.consume(TK_RBRACKET)
        print(f"DEBUG prob_choice: after ], next={self.peek()}", flush=True)

        right_lines = self.parse_block()
        print(f"DEBUG prob_choice: right_lines={right_lines}", flush=True)

        return (
            ['_coin :~ unif[0, 1];',
            f'if (_coin <= {prob.strip()}) then']
            + [f'  {l}' for l in left_lines]
            + ['else']
            + [f'  {l}' for l in right_lines]
            + ['end']
        )
    def parse_program(self) -> list[str]:
        """Parse the full program (no outer braces)."""
        lines = []
        while not self.at_eof():
            lines.extend(self.parse_stmt())
        return lines


# ---------------------------------------------------------------------------
# Top-level translation
# ---------------------------------------------------------------------------

_QUERY_VAR_RE = re.compile(r'\?\s*Pr\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]')


def _extract_query_var(src: str) -> str | None:
    """
    pGCL programs end with a `?Pr[var]` (or `?Pr[var1, var2]`) directive
    naming the actual query variable(s). The tokeniser treats `?...`
    lines as directives and skips them, so without this the criterion
    heuristic falls back to "last assigned variable before end of
    program" — which is often a throwaway/reset variable, not the real
    query variable. Return the first named variable, if any.
    """
    m = _QUERY_VAR_RE.search(src)
    return m.group(1) if m else None


def translate(src: str, name: str) -> str:
    """Translate a pGCL source string to prob-slicer language."""
    try:
        tokens = tokenise(src)
        parser = Parser(tokens)
        lines  = parser.parse_program()

        # Prefer the pGCL source's own `?Pr[var]` query directive as the
        # criterion; fall back to the last-assigned-variable heuristic
        # only if no such directive is present.
        criterion = _extract_query_var(src) or _infer_criterion(lines)

        # Strip trailing skips and add return
        while lines and lines[-1].strip() == 'skip':
            lines.pop()
        lines.append(f'return {criterion};')

        return '\n'.join(lines)
    except Exception as e:
        print(f"DEBUG translate ERROR in {name}: {type(e).__name__}: {e}",
              flush=True)
        print(traceback.format_exc(), flush=True)
        raise

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

def _infer_criterion(lines: list[str]) -> str:
    """
    Heuristic: return the last variable that appears on the LHS
    of an assignment or sample statement.
    """
    last_var = 'x'
    for line in lines:
        m = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*(:=|:~)', line)
        if m:
            last_var = m.group(1)
    return last_var


# ---------------------------------------------------------------------------
# File discovery and batch conversion
# ---------------------------------------------------------------------------

def find_pgcl_files(root: Path) -> list[Path]:
    """Recursively find all .pgcl files under root."""
    return sorted(root.rglob('*.pgcl'))


def convert_file(
    src_path:     Path,
    out_dir:      Path,
    prodigy_root: Path,
) -> str | None:
    try:
        src  = src_path.read_text(encoding='utf-8', errors='replace')

        # Build a name that encodes the subdirectory path:
        # e.g. inference/coin.pgcl -> prodigy_inference_coin
        rel_path   = src_path.relative_to(prodigy_root)
        parts      = list(rel_path.with_suffix('').parts)
        flat_name  = '_'.join(parts)
        bench_name = f'prodigy_{flat_name}'
        print("DEBUG: converting", src_path, "->", bench_name, flush=True)
        translated = translate(src, flat_name)
        criterion  = _extract_query_var(src) or _infer_criterion(translated.splitlines())

        content = _format_benchmark_source(
            name        = bench_name,
            description = f'Translated from Prodigy pGCL: {rel_path}',
            reference   = 'LKlinke/Prodigy (github.com/LKlinke/Prodigy)',
            criterion   = criterion,
            expected    = 'unknown',
            source      = translated,
            tags        = f'prodigy translated {parts[0]}',
        )
        out_path = out_dir / f'{bench_name}.prob'
        out_path.write_text(content, encoding='utf-8')
        return str(out_path)
    
    except TypeError as e:
        print(f"DEBUG convert_file TypeError in {src_path.name}: {e}",
              flush=True)
        print(f"  type(src)={type(src)}", flush=True)
        print(f"  type(translated)={type(translated) if 'translated' in dir() else 'not yet assigned'}",
              flush=True)
        print(traceback.format_exc(), flush=True)
        return None
    
    except Exception as e:
        print(f'  WARNING: failed to translate {src_path}: {e}',
              file=sys.stderr)
        return None

def translate_prodigy_benchmarks(
    prodigy_root: Path,
    out_dir:      Path = PRODIGY_OUT,
) -> None:
    """
    Translate all .pgcl files found under prodigy_root and
    save results to out_dir so load_benchmarks() picks them up.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    pgcl_files = find_pgcl_files(prodigy_root)
    if not pgcl_files:
        print(f'No .pgcl files found under {prodigy_root}')
        return

    print(f'Found {len(pgcl_files)} .pgcl files — translating...')
    ok, failed = [], []
    for path in pgcl_files:
        result = convert_file(path, out_dir, prodigy_root)
        if result:
            ok.append(path.name)
        else:
            failed.append(path.name)

    print(f'\nTranslated {len(ok)}/{len(pgcl_files)} programs '
          f'-> {out_dir}')
    if failed:
        print(f'Failed ({len(failed)}):')
        for f in failed:
            print(f'  {f}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # Point directly at your imported-benchmarks/prodigy directory
    prodigy_root = REPO_ROOT / 'imported-benchmarks' / 'prodigy'
    out_dir      = BENCHMARKS_DIR / 'prodigy'
    translate_prodigy_benchmarks(prodigy_root, out_dir)