"""
cfg_builder.py
==============
Constructs a Control Flow Graph (CFG) from an AST.

CFG edges carry a 'label' attribute: 'seq' | 'true' | 'false' | 'back'

  'seq'   — sequential flow between statements
  'true'  — taken when branch/loop condition is true
  'false' — taken when branch/loop condition is false (loop exit)
  'back'  — back-edge from loop body exit to loop head
"""

from __future__ import annotations
from typing import Tuple
import networkx as nx
from .ast_nodes import (
    Cmd, CSkip, CAssign, CSample, CSeq, CIf, CWhile,
    CObserve, CReturn, Program
)

ENTRY =  0
EXIT  = -1


class CFGBuilder:
    def __init__(self):
        self.g = nx.DiGraph()
        self._add_node(ENTRY, None, 'entry', 'ENTRY')
        self._add_node(EXIT,  None, 'exit',  'EXIT')

    def _add_node(self, nid: int, ast_node, kind: str, label: str):
        self.g.add_node(nid, ast=ast_node, kind=kind, label=label)

    def _add_edge(self, src: int, dst: int, label: str = 'seq'):
        self.g.add_edge(src, dst, label=label)

    def build(self, prog: Program) -> nx.DiGraph:
        entries, exits = self._build_cmd(prog.body, [ENTRY])

        # Add return node
        return_node = CReturn(expr=prog.return_expr)
        return_nid  = return_node.node_id
        self.g.add_node(return_nid, ast=return_node,
                        kind='return', label=str(return_node))

        # Connect exits to return node
        for last_nid in exits:
            kind = self.g.nodes[last_nid].get('kind', '')
            if kind == 'loop_head':
                # Loop exit is the 'false' branch
                self._add_edge(last_nid, return_nid, label='false')
            else:
                self._add_edge(last_nid, return_nid, label='seq')

        self._add_edge(return_nid, EXIT, label='seq')

        # Finalize any remaining unresolved while exit edges
        self._finalize_while_exits()

        return self.g

    def _build_cmd(
        self,
        cmd:      Cmd,
        pred_ids: list[int],
    ) -> Tuple[list[int], list[int]]:
        if isinstance(cmd, CSkip):
            return self._build_atomic(cmd, pred_ids, 'skip',    str(cmd))
        if isinstance(cmd, CAssign):
            return self._build_atomic(cmd, pred_ids, 'assign',  str(cmd))
        if isinstance(cmd, CSample):
            return self._build_atomic(cmd, pred_ids, 'sample',  str(cmd))
        if isinstance(cmd, CObserve):
            return self._build_atomic(cmd, pred_ids, 'observe', str(cmd))
        if isinstance(cmd, CSeq):
            return self._build_seq(cmd, pred_ids)
        if isinstance(cmd, CIf):
            return self._build_if(cmd, pred_ids)
        if isinstance(cmd, CWhile):
            return self._build_while(cmd, pred_ids)
        raise NotImplementedError(f"Unknown command type: {type(cmd)}")

    def _build_atomic(
        self, cmd: Cmd, pred_ids: list[int], kind: str, label: str
    ) -> Tuple[list[int], list[int]]:
        nid = cmd.node_id
        self._add_node(nid, cmd, kind, label)
        for p in pred_ids:
            self._add_edge(p, nid, label='seq')
        return [nid], [nid]

    def _build_seq(
        self, cmd: CSeq, pred_ids: list[int]
    ) -> Tuple[list[int], list[int]]:
        # Flatten left-recursive CSeq tree iteratively to avoid
        # RecursionError on large programs (e.g. 14k-node chess benchmark)
        commands = []
        node = cmd
        while isinstance(node, CSeq):
            commands.append(node.right)
            node = node.left
        commands.append(node)
        commands.reverse()

        entry_ids        = None
        current_pred_ids = pred_ids
        for c in commands:
            e_ids, current_pred_ids = self._build_cmd(c, current_pred_ids)
            if entry_ids is None:
                entry_ids = e_ids

        return entry_ids, current_pred_ids

    '''
    def _build_seq(
        self, cmd: CSeq, pred_ids: list[int]
    ) -> Tuple[list[int], list[int]]:
        entry_ids, mid_ids  = self._build_cmd(cmd.left,  pred_ids)
        _,         exit_ids = self._build_cmd(cmd.right, mid_ids)
        return entry_ids, exit_ids
    '''
    def _build_if(
        self, cmd: CIf, pred_ids: list[int]
    ) -> Tuple[list[int], list[int]]:
        nid = cmd.node_id
        self._add_node(nid, cmd, 'branch', f"if {cmd.cond}")
        for p in pred_ids:
            self._add_edge(p, nid, label='seq')

        # Then branch: initial edges from nid are 'seq', relabel to 'true'
        _, then_exits = self._build_cmd(cmd.then_branch, [nid])
        for succ in list(self.g.successors(nid)):
            if self.g[nid][succ].get('label') == 'seq':
                self.g[nid][succ]['label'] = 'true'

        # Else branch: new 'seq' edges from nid, relabel to 'false'
        _, else_exits = self._build_cmd(cmd.else_branch, [nid])
        for succ in list(self.g.successors(nid)):
            if self.g[nid][succ].get('label') == 'seq':
                self.g[nid][succ]['label'] = 'false'

        return [nid], then_exits + else_exits

    def _build_while(
        self, cmd: CWhile, pred_ids: list[int]
    ) -> Tuple[list[int], list[int]]:
        head_id = cmd.node_id
        self._add_node(head_id, cmd, 'loop_head', f"while {cmd.cond}")
        for p in pred_ids:
            self._add_edge(p, head_id, label='seq')

        # Body: edge from head to body entry is 'true'
        _, body_exits = self._build_cmd(cmd.body, [head_id])
        for succ in list(self.g.successors(head_id)):
            if self.g[head_id][succ].get('label') == 'seq':
                self.g[head_id][succ]['label'] = 'true'

        # Back-edges from body exits to loop head
        for e in body_exits:
            self._add_edge(e, head_id, label='back')

        # Loop head is returned as exit — false edge added in build()
        # or _finalize_while_exits()
        return [head_id], [head_id]

    def _finalize_while_exits(self):
        """
        Any remaining unresolved edges from loop_head nodes that are
        not 'true', 'false', 'back', or 'seq' (sequential predecessor)
        should be 'false' — these are loop exit edges.
        """
        for u, v, data in list(self.g.edges(data=True)):
            if (self.g.nodes[u].get('kind') == 'loop_head' and
                    data.get('label') not in ('true', 'false', 'back')):
                data['label'] = 'false'


def build_cfg(prog: Program) -> nx.DiGraph:
    """Build and return the CFG for a parsed program."""
    return CFGBuilder().build(prog)


def cfg_to_dot(cfg: nx.DiGraph) -> str:
    """Export CFG to Graphviz dot format."""
    lines = ['digraph CFG {', '  rankdir=TB;']
    for nid, data in cfg.nodes(data=True):
        raw_label = data.get('label', '').replace('"', '\\"')
        label = f"id: {nid}\\n{raw_label}" if raw_label else f"id: {nid}"
        kind  = data.get('kind', '')
        shape = {
            'entry':     'diamond',
            'exit':      'diamond',
            'branch':    'diamond',
            'loop_head': 'diamond',
            'assign':    'box',
            'sample':    'box',
            'observe':   'parallelogram',
            'skip':      'box',
            'return':    'ellipse',
        }.get(kind, 'ellipse')
        color = {
            'sample':    'lightblue',
            'observe':   'lightyellow',
            'branch':    'lightgreen',
            'loop_head': 'lightgreen',
            'entry':     'gray',
            'exit':      'gray',
            'return':    'lightyellow',
        }.get(kind, 'white')
        lines.append(
            f'  {nid} [label="{label}", shape={shape}, '
            f'style=filled, fillcolor={color}];'
        )
    for u, v, data in cfg.edges(data=True):
        el = data.get('label', '')
        lines.append(f'  {u} -> {v} [label="{el}"];')
    lines.append('}')
    return '\n'.join(lines)