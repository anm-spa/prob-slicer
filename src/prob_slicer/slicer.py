"""
slicer.py
=========
Two-phase slicing pipeline:

  Phase 1 — slice_program (Definition 6.6):
    Transforms code map while PRESERVING CFG structure.
      code2(n) = code1(n)  if n in S_C
      code2(n) = skip      if n not in S_C, code1(n) is assign/observe
      code2(n) = false     if n not in S_C, code1(n) is Boolean guard

  Phase 2 — postprocess (cleanup):
    Removes skips, simplifies unreachable branches, flattens structure.
    Simplifications applied bottom-up:
      skip ; c                    =>  c
      c ; skip                    =>  c
      if false then c1 else c2    =>  c2
      if true  then c1 else c2    =>  c1
      if _ then skip else skip    =>  skip
      while false do c            =>  skip
"""

from __future__ import annotations
from typing import Set
from .ast_nodes import (
    Cmd, CSkip, CAssign, CSample, CSeq, CIf, CWhile, CObserve,
    Program, BExpr, BFalse, BTrue
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _is_false(cond: BExpr) -> bool:
    return isinstance(cond, BFalse)

def _is_true(cond: BExpr) -> bool:
    return isinstance(cond, BTrue)

def _is_skip(cmd: Cmd) -> bool:
    return isinstance(cmd, CSkip)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Slice (Definition 6.6)
# ═══════════════════════════════════════════════════════════════════════════════

class Slicer:
    """
    Phase 1: Apply Definition 6.6 to transform the code map.
    Preserves CFG structure — only the code at each node changes.

    Rules:
      (1) code2(n) = code1(n)  if n in S_C
      (2) code2(n) = skip      if n not in S_C and code1(n) is
                                assign or observe
      (3) code2(n) = false     if n not in S_C and code1(n) is
                                a Boolean guard (if/while condition)
    """

    def __init__(self, slice_nodes: Set[int]):
        self.slice = slice_nodes

    def _in_slice(self, cmd: Cmd) -> bool:
        return getattr(cmd, 'node_id', None) in self.slice

    def slice_program(self, prog: Program) -> Program:
        """Apply Definition 6.6 to the program body."""
        sliced_body = self._slice_cmd(prog.body)
        return Program(body=sliced_body, return_expr=prog.return_expr)

    def _slice_cmd(self, cmd: Cmd) -> Cmd:
        if isinstance(cmd, CSeq):
            # Flatten left-recursive CSeq tree iteratively to avoid
            # RecursionError on large programs (e.g. 14k-node chess benchmark)
            commands = []
            node = cmd
            while isinstance(node, CSeq):
                commands.append(node.right)
                node = node.left
            commands.append(node)
            commands.reverse()

            sliced = [self._slice_cmd(c) for c in commands]
            result = sliced[0]
            for c in sliced[1:]:
                result = CSeq(left=result, right=c)
            return result

        if isinstance(cmd, CIf):
            # Rule (1): guard in S_C — keep condition.
            # Rule (3): guard not in S_C — replace with BFalse().
            # Always recurse into both branches regardless.
            new_cond = cmd.cond if self._in_slice(cmd) else BFalse()
            then_s   = self._slice_cmd(cmd.then_branch)
            else_s   = self._slice_cmd(cmd.else_branch)
            new_if   = CIf(cond=new_cond,
                           then_branch=then_s,
                           else_branch=else_s)
            new_if.node_id = cmd.node_id
            return new_if

        if isinstance(cmd, CWhile):
            # Rule (1): guard in S_C — keep condition.
            # Rule (3): guard not in S_C — replace with BFalse().
            # Always recurse into body regardless.
            new_cond = cmd.cond if self._in_slice(cmd) else BFalse()
            body_s   = self._slice_cmd(cmd.body)
            new_w    = CWhile(cond=new_cond, body=body_s)
            new_w.node_id = cmd.node_id
            return new_w

        if isinstance(cmd, (CAssign, CSample, CObserve)):
            if self._in_slice(cmd):
                # Rule (1): keep as is.
                return cmd
            else:
                # Rule (2): replace with skip.
                s = CSkip()
                s.node_id = cmd.node_id
                return s

        # CSkip: keep as is.
        return cmd
    
    '''
    def _slice_cmd(self, cmd: Cmd) -> Cmd:
        if isinstance(cmd, CSeq):
            # CSeq is structural (not a CFG node); recurse into both sides.
            return CSeq(
                left=self._slice_cmd(cmd.left),
                right=self._slice_cmd(cmd.right)
            )

        if isinstance(cmd, CIf):
            # Rule (1): guard in S_C — keep condition.
            # Rule (3): guard not in S_C — replace with BFalse().
            # Always recurse into both branches regardless.
            new_cond = cmd.cond if self._in_slice(cmd) else BFalse()
            then_s   = self._slice_cmd(cmd.then_branch)
            else_s   = self._slice_cmd(cmd.else_branch)
            new_if   = CIf(cond=new_cond,
                           then_branch=then_s,
                           else_branch=else_s)
            new_if.node_id = cmd.node_id
            return new_if

        if isinstance(cmd, CWhile):
            # Rule (1): guard in S_C — keep condition.
            # Rule (3): guard not in S_C — replace with BFalse().
            # Always recurse into body regardless.
            new_cond = cmd.cond if self._in_slice(cmd) else BFalse()
            body_s   = self._slice_cmd(cmd.body)
            new_w    = CWhile(cond=new_cond, body=body_s)
            new_w.node_id = cmd.node_id
            return new_w

        if isinstance(cmd, (CAssign, CSample, CObserve)):
            if self._in_slice(cmd):
                # Rule (1): keep as is.
                return cmd
            else:
                # Rule (2): replace with skip.
                s = CSkip()
                s.node_id = cmd.node_id
                return s

        # CSkip: keep as is.
        return cmd
'''

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Postprocessing (cleanup)
# ═══════════════════════════════════════════════════════════════════════════════

class Postprocessor:
    """
    Phase 2: Simplify the sliced AST by removing no-ops and
    unreachable code. Works purely on the AST — no knowledge of S_C.

    Simplification rules applied bottom-up:
      skip ; c                  =>  c
      c ; skip                  =>  c
      skip ; skip               =>  skip
      if false then c1 else c2  =>  c2   (then-branch unreachable)
      if true  then c1 else c2  =>  c1   (else-branch unreachable)
      if _ then skip else skip  =>  skip
      while false do c          =>  skip  (loop never executes)
    """

    def postprocess_program(self, prog: Program) -> Program:
        simplified = self._simplify(prog.body)
        return Program(body=simplified, return_expr=prog.return_expr)

    def _simplify(self, cmd: Cmd) -> Cmd:
        """Apply simplification rules bottom-up."""

        if isinstance(cmd, CSeq):
            # Flatten left-recursive CSeq tree iteratively to avoid
            # RecursionError on large programs (e.g. 14k-node chess benchmark)
            commands = []
            node = cmd
            while isinstance(node, CSeq):
                commands.append(node.right)
                node = node.left
            commands.append(node)
            commands.reverse()

            # Simplify each command, filter skips, rebuild
            simplified = [self._simplify(c) for c in commands]
            non_skips  = [c for c in simplified if not _is_skip(c)]
            if not non_skips:
                return CSkip()
            result = non_skips[0]
            for c in non_skips[1:]:
                result = CSeq(left=result, right=c)
            return result

        if isinstance(cmd, CIf):
            then_s = self._simplify(cmd.then_branch)
            else_s = self._simplify(cmd.else_branch)
            # if false then c1 else c2  =>  c2
            if _is_false(cmd.cond):
                return else_s
            # if true then c1 else c2  =>  c1
            if _is_true(cmd.cond):
                return then_s
            # if _ then skip else skip  =>  skip
            if _is_skip(then_s) and _is_skip(else_s):
                return CSkip()
            new_if = CIf(cond=cmd.cond,
                         then_branch=then_s,
                         else_branch=else_s)
            new_if.node_id = cmd.node_id
            return new_if

        if isinstance(cmd, CWhile):
            body_s = self._simplify(cmd.body)
            # while false do c  =>  skip
            if _is_false(cmd.cond):
                return CSkip()
            new_w = CWhile(cond=cmd.cond, body=body_s)
            new_w.node_id = cmd.node_id
            return new_w

        # Atomic commands: nothing to simplify.
        return cmd
    
'''
    def _simplify(self, cmd: Cmd) -> Cmd:
        """Apply simplification rules bottom-up."""

        if isinstance(cmd, CSeq):
            left  = self._simplify(cmd.left)
            right = self._simplify(cmd.right)
            if _is_skip(left) and _is_skip(right):
                return CSkip()
            if _is_skip(left):
                return right
            if _is_skip(right):
                return left
            return CSeq(left=left, right=right)

        if isinstance(cmd, CIf):
            then_s = self._simplify(cmd.then_branch)
            else_s = self._simplify(cmd.else_branch)
            # if false then c1 else c2  =>  c2
            if _is_false(cmd.cond):
                return else_s
            # if true then c1 else c2  =>  c1
            if _is_true(cmd.cond):
                return then_s
            # if _ then skip else skip  =>  skip
            if _is_skip(then_s) and _is_skip(else_s):
                return CSkip()
            new_if = CIf(cond=cmd.cond,
                         then_branch=then_s,
                         else_branch=else_s)
            new_if.node_id = cmd.node_id
            return new_if

        if isinstance(cmd, CWhile):
            body_s = self._simplify(cmd.body)
            # while false do c  =>  skip
            if _is_false(cmd.cond):
                return CSkip()
            new_w = CWhile(cond=cmd.cond, body=body_s)
            new_w.node_id = cmd.node_id
            return new_w

        # Atomic commands: nothing to simplify.
        return cmd
'''

# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def slice_program(prog: Program, slice_nodes: Set[int]) -> Program:
    """
    Full two-phase slicing pipeline:
      Phase 1: Apply Definition 6.6 (preserves CFG).
      Phase 2: Postprocess (remove skips, simplify unreachable code).
    """
    phase1 = Slicer(slice_nodes).slice_program(prog)
    phase2 = Postprocessor().postprocess_program(phase1)
    return phase2


def slice_only(prog: Program, slice_nodes: Set[int]) -> Program:
    """
    Phase 1 only — returns the Definition 6.6 slice before postprocessing.
    Useful for debugging or inspecting the intermediate CFG-preserving slice.
    """
    return Slicer(slice_nodes).slice_program(prog)


def postprocess_only(prog: Program) -> Program:
    """
    Phase 2 only — postprocess an already-sliced program.
    Useful for inspecting before and after cleanup separately.
    """
    return Postprocessor().postprocess_program(prog)


# ═══════════════════════════════════════════════════════════════════════════════
# Pretty Printer
# ═══════════════════════════════════════════════════════════════════════════════

def pretty_print(prog: Program) -> str:
    """Pretty-print a (sliced) program, omitting redundant skips."""
    lines = _pp_cmd(prog.body, indent=0)
    lines.append(f"return {prog.return_expr}")
    return '\n'.join(lines)

def count_statements(prog) -> int:
        src = pretty_print(prog)
        return sum(1 for line in src.splitlines() if line.strip())

def _pp_cmd(cmd: Cmd, indent: int) -> list[str]:
    pad = '  ' * indent
    if isinstance(cmd, CSkip):
        return []
    if isinstance(cmd, (CAssign, CSample, CObserve)):
        return [f"{pad}{cmd}"]
    if isinstance(cmd, CSeq):
        # Flatten left-recursive CSeq tree iteratively to avoid
        # RecursionError on large programs (e.g. 14k-node chess benchmark)
        commands = []
        node = cmd
        while isinstance(node, CSeq):
            commands.append(node.right)
            node = node.left
        commands.append(node)
        commands.reverse()
        return [line for c in commands for line in _pp_cmd(c, indent)]
    if isinstance(cmd, CIf):
        lines  = [f"{pad}if {cmd.cond} then"]
        lines += _pp_cmd(cmd.then_branch, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}else"]
        lines += _pp_cmd(cmd.else_branch, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}end"]
        return lines
    if isinstance(cmd, CWhile):
        lines  = [f"{pad}while {cmd.cond} do"]
        lines += _pp_cmd(cmd.body, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}end"]
        return lines
    return [f"{pad}{cmd}"]

'''
def _pp_cmd(cmd: Cmd, indent: int) -> list[str]:
    pad = '  ' * indent
    if isinstance(cmd, CSkip):
        return []
    if isinstance(cmd, (CAssign, CSample, CObserve)):
        return [f"{pad}{cmd}"]
    if isinstance(cmd, CSeq):
        return _pp_cmd(cmd.left, indent) + _pp_cmd(cmd.right, indent)
    if isinstance(cmd, CIf):
        lines  = [f"{pad}if {cmd.cond} then"]
        lines += _pp_cmd(cmd.then_branch, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}else"]
        lines += _pp_cmd(cmd.else_branch, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}end"]
        return lines
    if isinstance(cmd, CWhile):
        lines  = [f"{pad}while {cmd.cond} do"]
        lines += _pp_cmd(cmd.body, indent + 1) or [f"{pad}  skip"]
        lines += [f"{pad}end"]
        return lines
    return [f"{pad}{cmd}"]
'''