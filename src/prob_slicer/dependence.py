"""
dependence.py
=============
Static dependence analysis for probabilistic programs.

Supports three slicing variants:
  (1) Nontermination-sensitive, distribution-sensitive
        cd = scd,  R = obsntd(scd)
  (2) Nontermination-insensitive, distribution-sensitive
        cd = wcd,  R = obsntd(wcd)
  (3) Nontermination-insensitive, distribution-insensitive
        cd = wcd,  R = obsd(wcd)

Slice set (Definition 6.5):
  slice_G(C, cd, R) = union_{n in C union D} {m | m (cd union dd)* n}
  where D = {m' | exists n' in C. m' (R)* n'}
"""

from __future__ import annotations
from typing import Dict, Set, Tuple, Literal
import networkx as nx
from collections import defaultdict, deque
from .ast_nodes import CAssign, CReturn, CSample, CObserve, CIf, CWhile, CSkip, Cmd
from .cfg_builder import ENTRY, EXIT
import numpy as np
import gc

SliceVariant = Literal['ns', 'nids', 'ni']
SLICE_DEBUG = False

def set_debug(enabled: bool) -> None:
    """Enable or disable slice computation progress output."""
    global SLICE_DEBUG
    SLICE_DEBUG = enabled

# ═══════════════════════════════════════════════════════════════════════════════
# Reaching Definitions
# ═══════════════════════════════════════════════════════════════════════════════

class ReachingDefinitions:
    """
    Classic reaching-definitions dataflow analysis (forward, may).
    A definition is a (variable, def_node_id) pair.
    """

    def __init__(self, cfg: nx.DiGraph):
        self.cfg   = cfg
        self.gen:  Dict[int, Set[Tuple[str, int]]] = {}
        self.kill: Dict[int, Set[Tuple[str, int]]] = {}
        self.IN:   Dict[int, Set[Tuple[str, int]]] = {}
        self.OUT:  Dict[int, Set[Tuple[str, int]]] = {}

    def _defs_of(self, nid: int) -> Set[str]:
        ast = self.cfg.nodes[nid].get('ast')
        if isinstance(ast, (CAssign, CSample)):
            return {ast.var}
        return set()

    def _all_defs(self) -> Dict[str, Set[Tuple[str, int]]]:
        result: Dict[str, Set] = {}
        for nid in self.cfg.nodes:
            for var in self._defs_of(nid):
                result.setdefault(var, set()).add((var, nid))
        return result

    def compute(self):
        all_defs = self._all_defs()
        for nid in self.cfg.nodes:
            defs = self._defs_of(nid)
            self.gen[nid]  = {(v, nid) for v in defs}
            self.kill[nid] = set()
            for v in defs:
                self.kill[nid] |= (all_defs.get(v, set()) - {(v, nid)})
            self.IN[nid]  = set()
            self.OUT[nid] = set()

        worklist = (list(nx.topological_sort(self.cfg))
                    if nx.is_directed_acyclic_graph(self.cfg)
                    else list(self.cfg.nodes))
        changed = True
        while changed:
            changed = False
            for nid in worklist:
                new_in  = set().union(*(self.OUT.get(p, set())
                                        for p in self.cfg.predecessors(nid)))
                new_out = self.gen[nid] | (new_in - self.kill[nid])
                if new_in != self.IN[nid] or new_out != self.OUT[nid]:
                    self.IN[nid]  = new_in
                    self.OUT[nid] = new_out
                    changed = True


# ═══════════════════════════════════════════════════════════════════════════════
# Post-Dominator Tree
# ═══════════════════════════════════════════════════════════════════════════════

def build_post_dominator_tree(cfg: nx.DiGraph) -> Dict[int, int]:
    """
    Returns dict: node_id -> immediate post-dominator node_id.
    EXIT is its own post-dominator.
    """
    rev = nx.DiGraph()
    rev.add_nodes_from(cfg.nodes())          # no attribute data copied
    rev.add_edges_from(
        (v, u, data)
        for u, v, data in cfg.edges(data=True)
    )
    try:
        return nx.immediate_dominators(rev, EXIT)
    except nx.NetworkXError:
        return {n: EXIT for n in cfg.nodes}

# ═══════════════════════════════════════════════════════════════════════════════
# Strong Control Dependence (scd) — nontermination-sensitive
# ═══════════════════════════════════════════════════════════════════════════════
def _maximal_paths_always_reach(
    cfg: nx.DiGraph, start: int, target: int, pivot: int
) -> bool:
    """
    target occurs on all maximal paths from start, and precedes pivot.
    Avoids cfg.copy() by using node filtering directly.
    """
    if start == target:
        return True

    # Nodes reachable from start, excluding target
    def reachable_excluding(source: int, excluded: set) -> set:
        """BFS/DFS reachability excluding a set of nodes."""
        visited = set()
        stack   = [source]
        while stack:
            node = stack.pop()
            if node in visited or node in excluded:
                continue
            visited.add(node)
            for succ in cfg.successors(node):
                if succ not in visited and succ not in excluded:
                    stack.append(succ)
        return visited

    excluded_target = {target}

    # 1. Check target is unavoidable from start:
    #    If EXIT is reachable from start without going through target -> False
    reachable_no_target = reachable_excluding(start, excluded_target)

    if EXIT in reachable_no_target:
        return False

    # Check for infinite path avoiding target:
    # If there is a cycle in the subgraph reachable from start \ {target}
    subgraph = cfg.subgraph(reachable_no_target)
    if not nx.is_directed_acyclic_graph(subgraph):
        return False

    # 2. Check ordering: target must precede pivot
    #    If pivot is reachable from start without going through target -> False
    if pivot in reachable_no_target:
        return False

    return True

def _maximal_paths_always_reach_old(cfg: nx.DiGraph, start: int, target: int, pivot: int) -> bool:
    """target occurs on all maximal paths from start, and precedes pivot."""
    if start == target:
        return True

    # 1. Target is unavoidable (your current logic, slightly cleaned)
    cfg_no_target = cfg.copy()
    cfg_no_target.remove_node(target)

    if EXIT in cfg_no_target and nx.has_path(cfg_no_target, start, EXIT):
        return False

    # Better cycle check on reachable part only
    reachable_no_target = nx.descendants(cfg_no_target, start) | {start}
    subgraph = cfg_no_target.subgraph(reachable_no_target)
    if not nx.is_directed_acyclic_graph(subgraph):
        return False  # infinite path avoiding target

    # 2. Now check ordering: target precedes pivot on relevant paths
    # Idea: from start, can we reach pivot *without* going through target?
    cfg_no_target_pivot = cfg.copy()
    if target != start:
        cfg_no_target_pivot.remove_node(target)

    if pivot in cfg_no_target_pivot and nx.has_path(cfg_no_target_pivot, start, pivot):
        return False  # There is a path to pivot that avoids target entirely

    return True

def build_forward_dominator_tree(cfg: nx.DiGraph) -> dict[int, int]:
    """
    Build the immediate forward dominator tree.
    n immediately dominates m if n is on every path from ENTRY to m.
    Uses networkx dominance_frontiers / immediate_dominators.
    """
    return nx.immediate_dominators(cfg, ENTRY)


def build_scd_wrong(cfg: nx.DiGraph) -> nx.DiGraph:
    """
    Nontermination-sensitive control dependence (Definition 3.5).

    Efficient implementation mirroring build_wcd but using forward
    dominators instead of post-dominators.

    m --scd--> n iff m has at least two successors n1, n2 such that:
      (1) n is on ALL maximal paths from n1 and precedes m
      (2) there exists a maximal path from n2 where n does not appear
          before m

    Key insight: condition (1) is equivalent to:
      n forward-dominates n1 in the subgraph that excludes back-edges
      beyond m (i.e. n is an unavoidable node from n1 before reaching m).

    We mirror build_wcd exactly:
      For each CFG edge (m -> n1), walk UP the forward dominator tree
      from n1 until idom(m), adding scd edges m -> runner.
      Then check condition (2) by verifying not all successors
      have n in their dominator chain.
    """
    scd  = nx.DiGraph()
    scd.add_nodes_from(cfg.nodes(data=True))
    idom = build_forward_dominator_tree(cfg)

    def strictly_dominates(n: int, m: int) -> bool:
        """Does n strictly forward-dominate m?"""
        if n == m:
            return False
        runner = idom.get(m, ENTRY)
        while runner != ENTRY:
            if runner == n:
                return True
            if runner == idom.get(runner, ENTRY):
                break
            runner = idom.get(runner, ENTRY)
        return runner == n

    for m, n1 in cfg.edges():
        if m == n1:
            continue
        successors = list(cfg.successors(m))
        if len(successors) < 2:
            continue

        # Walk up forward dominator tree from n1 to idom(m)
        # Every node on this walk satisfies condition (1)
        runner = n1
        stop   = idom.get(m, ENTRY)
        while runner != stop and runner != ENTRY:
            if runner != m:
                # Check condition (2): exists another successor n2
                # where runner is NOT in the dominator chain from n2
                for n2 in successors:
                    if n2 == n1:
                        continue
                    if not strictly_dominates(runner, n2):
                        scd.add_edge(m, runner, dep_type='scd')
                        break
            runner = idom.get(runner, ENTRY)

    return scd

def build_scd(cfg: nx.DiGraph) -> nx.DiGraph:
    """
    Efficient nontermination-sensitive control dependence (NTSCD).
    Based on Chalupa et al. (CAV 2021) Algorithm 2.
    O(N^2) instead of O(N^3).
    """
    scd = nx.DiGraph()
    scd.add_nodes_from(cfg.nodes(data=True))
    if not cfg.nodes():
        return scd

    predecessors = {u: list(cfg.predecessors(u)) for u in cfg.nodes()}
    successors   = {u: list(cfg.successors(u))   for u in cfg.nodes()}
    predicates   = [m for m in cfg.nodes() if len(successors[m]) >= 2]

    def compute_red_nodes(n: int) -> set:
        """
        Returns set of nodes m such that ALL maximal paths from m
        contain n. Iterative version to avoid recursion limit.
        """
        counter = {u: len(successors[u]) for u in cfg.nodes()}
        red     = set()
        queue   = deque()

        # Seed: n itself is red
        red.add(n)
        queue.append(n)

        while queue:
            node = queue.popleft()
            for pred in predecessors[node]:
                if pred in red:
                    continue
                counter[pred] -= 1
                if counter[pred] == 0:
                    red.add(pred)
                    queue.append(pred)

        return red

    for n in cfg.nodes():
        red_nodes = compute_red_nodes(n)
        for m in predicates:
            if scd.has_edge(m, n):
                continue
            succs       = successors[m]
            has_red     = any(s in red_nodes for s in succs)
            has_non_red = any(s not in red_nodes for s in succs)
            if has_red and has_non_red:
                scd.add_edge(m, n, dep_type='scd')

    return scd
    
def build_scd_slow(cfg: nx.DiGraph) -> nx.DiGraph:
    """
    Nontermination-sensitive control dependence (Definition 3.5).

    m --scd--> n iff m has at least two successors n1, n2 such that:
      (1) on ALL maximal paths from n1, n occurs and either
          n == m or n strictly precedes m; AND
      (2) there EXISTS a maximal path from n2 on which either
          n does not occur, or m strictly precedes n.

    This correctly handles infinite (nonterminating) paths unlike
    the standard post-dominator approach.
    """
    scd = nx.DiGraph()
    scd.add_nodes_from(cfg.nodes(data=True))

    all_nodes = list(cfg.nodes())

    # --- Precompute reachability once ------------------------------------
    # reachable[u] = set of nodes reachable from u
    print("Precomputing reachability for all nodes...")
    reachable: dict[int, set] = {
        u: (nx.descendants(cfg, u) | {u})
        for u in all_nodes
    }

    # --- must_reach_set --------------------------------------------------
    def must_reach_set(start: int, pivot: int) -> set[int]:
        """
        Compute the set of nodes that appear on ALL maximal paths
        from start, and precede pivot.

        A node n is in this set iff:
          - removing n makes EXIT unreachable AND
          - removing n leaves no infinite path AND
          - n precedes pivot (pivot not reachable without n)

        Efficient: iterate over reachable nodes from start,
        check each as a candidate in O(reachable) per candidate.
        Only nodes reachable from start are candidates.
        """
        candidates = reachable[start] - {start, pivot, ENTRY, EXIT}
        result     = set()

        for n in candidates:
            # Check if n is unavoidable from start:
            # i.e. every maximal path from start goes through n

            # BFS from start excluding n
            visited = set()
            stack   = [start]
            while stack:
                node = stack.pop()
                if node in visited or node == n:
                    continue
                visited.add(node)
                for succ in cfg.successors(node):
                    if succ not in visited and succ != n:
                        stack.append(succ)

            # Condition: EXIT not reachable without n
            if EXIT in visited:
                continue   # n is avoidable

            # Condition: no infinite path avoiding n
            sub = cfg.subgraph(visited)
            if not nx.is_directed_acyclic_graph(sub):
                continue   # infinite path avoids n

            # Condition: n precedes pivot (pivot not reachable without n)
            if pivot in visited:
                continue   # can reach pivot without n -> n doesn't precede it

            result.add(n)

        return result

    # --- Main loop -------------------------------------------------------
    for m in all_nodes:
        successors = list(cfg.successors(m))
        if len(successors) < 2:
            continue

        # For each successor, compute must-reach set once
        must_sets = {
            n1: must_reach_set(n1, m)
            for n1 in successors
        }

        for i, n1 in enumerate(successors):
            for n in must_sets[n1]:
                print(f"Checking candidate scd edge {m} -> {n} via successor {n1}")
                # Condition (2): exists n2 where n is NOT must-reached
                for j, n2 in enumerate(successors):
                    if i == j:
                        continue
                    if n not in must_sets[n2]:
                        print(f"Adding scd edge {m} -> {n} via successor {n1}")
                        scd.add_edge(m, n, dep_type='scd')
                        break

    return scd
def build_scd_not_working(cfg: nx.DiGraph) -> nx.DiGraph:
    """
    Nontermination-sensitive control dependence (Definition 3.5).

    m --scd--> n iff m has at least two successors n1, n2 such that:
      (1) on ALL maximal paths from n1, n occurs and either
          n == m or n strictly precedes m; AND
      (2) there EXISTS a maximal path from n2 on which either
          n does not occur, or m strictly precedes n.

    This correctly handles infinite (nonterminating) paths unlike
    the standard post-dominator approach.
    """
    scd = nx.DiGraph()
    scd.add_nodes_from(cfg.nodes(data=True))

    all_nodes = list(cfg.nodes())

    # 1. reachable[u] = set of nodes reachable from u (including u)
    reachable: dict[int, set] = {
        u: (nx.descendants(cfg, u) | {u})
        for u in all_nodes
    }

    # 2. reachable_excl[u][v] = nodes reachable from u excluding v
    #    Only compute for (u, v) pairs we actually need:
    #    u = successor of some branching node, v = candidate n or m
    #    We build lazily with a cache
    _excl_cache: dict[tuple, set] = {}

    def reachable_excl(source: int, excluded: int) -> set:
        key = (source, excluded)
        if key not in _excl_cache:
            visited = set()
            stack   = [source]
            while stack:
                node = stack.pop()
                if node in visited or node == excluded:
                    continue
                visited.add(node)
                for succ in cfg.successors(node):
                    if succ not in visited and succ != excluded:
                        stack.append(succ)
            _excl_cache[key] = visited
        return _excl_cache[key]

    # 3. has_cycle_excl[u][v] = does subgraph reachable from u excl v have a cycle?
    _cycle_cache: dict[tuple, bool] = {}

    def has_cycle_excl(source: int, excluded: int) -> bool:
        key = (source, excluded)
        if key not in _cycle_cache:
            nodes  = reachable_excl(source, excluded)
            sub    = cfg.subgraph(nodes)
            _cycle_cache[key] = not nx.is_directed_acyclic_graph(sub)
        return _cycle_cache[key]

    # --- Optimised version of _maximal_paths_always_reach -----------------

    def always_reach(start: int, target: int, pivot: int) -> bool:
        if start == target:
            return True
        r_excl = reachable_excl(start, target)
        # If EXIT reachable without target -> not always reached
        if EXIT in r_excl:
            return False
        # If infinite path avoiding target exists -> not always reached
        if has_cycle_excl(start, target):
            return False
        # If pivot reachable without target -> target doesn't precede pivot
        if pivot in r_excl:
            return False
        return True


    for m in all_nodes:
        successors = list(cfg.successors(m))
        if len(successors) < 2:
            continue

        for n in all_nodes:
            if n == m:
                continue
            # Quick pre-filter: if n not reachable from any successor, skip
            if not any(n in reachable[s] for s in successors):
                continue

            found = False
            for i, n1 in enumerate(successors):
                if found:
                    break
                cond1 = always_reach(n1, n, m)
                if not cond1:
                    continue
                for j, n2 in enumerate(successors):
                    if i == j:
                        continue
                    cond2 = not always_reach(n2, n, m)
                    if cond2:
                        scd.add_edge(m, n, dep_type='scd')
                        found = True
                        break

    return scd

# ═══════════════════════════════════════════════════════════════════════════════
# Weak Control Dependence (wcd) — nontermination-insensitive
# ═══════════════════════════════════════════════════════════════════════════════

def build_wcd(cfg: nx.DiGraph) -> nx.DiGraph:
    """
    Weak (nontermination-insensitive) control dependence.
    Definition 3.4: m --wcd--> n iff:
      (1) exists nontrivial path from m to n where every
          intermediate node is postdominated by n, AND
      (2) m is not strictly postdominated by n.

    Standard implementation:
      For each CFG edge (m -> b), if b is NOT a strict
      postdominator of m, walk up the post-dominator tree
      from b until ipdom(m), adding wcd edges m -> runner.
      This correctly captures condition (1) and (2).
    """
    wcd   = nx.DiGraph()
    wcd.add_nodes_from(cfg.nodes(data=True))
    ipdom = build_post_dominator_tree(cfg)

    # Precompute strict postdominators of each node:
    # n strictly postdominates m if n postdominates m and n != m.
    # n is in the post-dominator chain of m if we can reach n
    # by following ipdom links from m.
    def strict_postdominates(n: int, m: int) -> bool:
        """Does n strictly postdominate m?"""
        if n == m:
            return False
        runner = ipdom.get(m, EXIT)
        while runner != EXIT:
            if runner == n:
                return True
            if runner == ipdom.get(runner, EXIT):
                break
            runner = ipdom.get(runner, EXIT)
        return runner == n

    for m, b in cfg.edges():
        # Condition (2): m must NOT be strictly postdominated by n.
        # Walk from b up to ipdom(m), adding wcd edges.
        # Each runner on this walk satisfies:
        #   - reachable from m via b (condition 1 path exists)
        #   - not strictly postdominating m (condition 2)
        #     because if runner strictly postdominated m,
        #     it would be ipdom(m) or above, and we stop there.
        if m==b:
            continue  # self-loop, ignore
        runner = b
        stop   = ipdom.get(m, EXIT)
        while runner != stop and runner != EXIT:
            if runner != m:
                wcd.add_edge(m, runner, dep_type='wcd')
            runner = ipdom.get(runner, EXIT)

    return wcd
# ═══════════════════════════════════════════════════════════════════════════════
# Data Dependence Graph (DDG)
# ═══════════════════════════════════════════════════════════════════════════════

def _uses_of(cfg: nx.DiGraph, nid: int) -> Set[str]:
    ast = cfg.nodes[nid].get('ast')
    if ast is None:
        return set()
    if isinstance(ast, (CAssign, CSample, CObserve, CIf, CWhile, CReturn)):
        return ast.uses()
    return set()


def build_ddg(cfg: nx.DiGraph, rd: ReachingDefinitions) -> nx.DiGraph:
    """
    Data Dependence Graph.
    Edge (def_node -> use_node) labelled 'data' or 'stoch_data'.
    """
    ddg = nx.DiGraph()
    ddg.add_nodes_from(cfg.nodes(data=True))

    for nid in cfg.nodes:
        for var in _uses_of(cfg, nid):
            for (dvar, def_nid) in rd.IN.get(nid, set()):
                if dvar == var:
                    def_ast  = cfg.nodes[def_nid].get('ast')
                    dep_type = ('stoch_data' if isinstance(def_ast, CSample)
                                else 'data')
                    ddg.add_edge(def_nid, nid, dep_type=dep_type, var=var)
    return ddg


# ═══════════════════════════════════════════════════════════════════════════════
# Observe-Nontermination Dependence  obsntd(cd)
# ═══════════════════════════════════════════════════════════════════════════════

def _nodes_reaching_m(m: int, combined: nx.DiGraph) -> Set[int]:
    """Nodes n' that can reach m via (cd ∪ dd)*"""
    rev = combined.reverse(copy=False)
    try:
        return nx.ancestors(rev, m) | {m}
    except nx.NetworkXError:
        return {m}


def _nodes_reachable_from_set(sources: Set[int], combined: nx.DiGraph) -> Set[int]:
    """All nodes reachable from any node in sources"""
    influenced: Set[int] = set()
    for s in sources:
        try:
            influenced |= nx.descendants(combined, s)
            influenced.add(s)
        except nx.NetworkXError:
            pass
    return influenced

def _obsntd_sources(cfg: nx.DiGraph) -> Set[int]:
    """
    Identify all observe-nontermination source nodes:
      (a) CObserve nodes
      (b) while predicate nodes that may not terminate
    """
    sources: Set[int] = set()
    for nid in cfg.nodes:
        ast = cfg.nodes[nid].get('ast')
        if isinstance(ast, CObserve):
            sources.add(nid)
        elif _is_nonterminating_while(cfg, nid, ast):
            sources.add(nid)
    return sources


def _obsd_sources(cfg: nx.DiGraph) -> Set[int]:
    """
    Identify observe dependence source nodes:
    CObserve nodes only.
    """
    return {
        nid for nid in cfg.nodes
        if isinstance(cfg.nodes[nid].get('ast'), CObserve)
    }


def _is_nonterminating_while(cfg: nx.DiGraph,
                              nid: int,
                              ast) -> bool:
    """
    Check if a while predicate node may not terminate.
    """
    if not isinstance(ast, CWhile):
        return False

    succs      = list(cfg.successors(nid))
    true_succ  = next(
        (s for s in succs
         if cfg.edges[nid, s].get('label') == 'true'), None
    )
    false_succ = next(
        (s for s in succs
         if cfg.edges[nid, s].get('label') == 'false'), None
    )

    if true_succ is None or false_succ is None:
        return False

    cfg_no_false = cfg.copy()
    cfg_no_false.remove_node(false_succ)

    return (
        true_succ in cfg_no_false
        and nid in cfg_no_false
        and nx.has_path(cfg_no_false, true_succ, nid)
    )


def _build_combined(cfg: nx.DiGraph,
                    cd: nx.DiGraph,
                    ddg: nx.DiGraph) -> nx.DiGraph:
    """
    Build combined (cd union dd) graph.
    Strictly cd union dd — NO CFG edges.
    Previously included cfg.edges() which caused incorrect
    forward flow from observe/while nodes to post-loop nodes.
    """
    combined = nx.DiGraph()
    combined.add_nodes_from(cfg.nodes(data=True))
    for u, v in cd.edges():
        combined.add_edge(u, v, kind='cd')
    for u, v in ddg.edges():
        combined.add_edge(u, v, kind='dd')
    return combined

def _reachable_from(m: int, combined: nx.DiGraph) -> Set[int]:
    """Return all n such that exists n' with n' ->* m and n' ->* n"""
    try:
        common_ancestors = nx.ancestors(combined, m) | {m}
    except nx.NetworkXError:
        common_ancestors = {m}

    # Pass 2: all descendants reachable from ANY ancestor — one forward BFS
    # from a virtual super-source that connects to all ancestors.
    # Equivalent but faster: just do BFS from all ancestors simultaneously.
    influenced = set()
    visited    = set()
    stack      = list(common_ancestors)
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for succ in combined.successors(node):
            influenced.add(succ)
            if succ not in visited:
                stack.append(succ)

    influenced -= common_ancestors
    influenced -= {m, ENTRY, EXIT}
    return influenced

def build_obsntd(cfg: nx.DiGraph,
                 ddg: nx.DiGraph,
                 cd:  nx.DiGraph) -> nx.DiGraph:
    obsntd   = nx.DiGraph()
    obsntd.add_nodes_from(cfg.nodes(data=True))
    combined = _build_combined(cfg, cd, ddg)
    sources  = _obsntd_sources(cfg)

    if not sources or combined.number_of_nodes() == 0:
        return obsntd

    print(f"  [OBSNTD] {len(sources)} sources, "
          f"{combined.number_of_nodes()} nodes, "
          f"{combined.number_of_edges()} edges", flush=True)

    skip       = {ENTRY, EXIT}
    all_nodes  = list(combined.nodes())
    N          = len(all_nodes)
    node_index = {node: i for i, node in enumerate(all_nodes)}

    # --- SCC condensation + reachability on DAG ---
    print("  [OBSNTD] condensing SCCs...", flush=True)
    sccs       = list(nx.strongly_connected_components(combined))
    cond_graph = nx.condensation(combined, sccs)
    scc_index  = {
        node: idx
        for idx, component in enumerate(sccs)
        for node in component
    }
    n_scc = len(sccs)

    # Reachability on condensed DAG
    print("  [OBSNTD] computing SCC reachability...", flush=True)
    topo      = list(nx.topological_sort(cond_graph))
    scc_reach = np.zeros((n_scc, n_scc), dtype=np.bool_)
    for scc_id in reversed(topo):
        scc_reach[scc_id, scc_id] = True
        for succ_scc in cond_graph.successors(scc_id):
            scc_reach[scc_id] |= scc_reach[succ_scc]

    # Map to full node reachability — vectorised, no Python loop
    print("  [OBSNTD] mapping to node reachability...", flush=True)
    scc_of_node = np.array([scc_index[u] for u in all_nodes])
    reach       = scc_reach[np.ix_(scc_of_node, scc_of_node)]

    print(f"  [OBSNTD] reachability density={reach.mean():.4f}", flush=True)
    del scc_reach, scc_of_node
    gc.collect()

    # --- Process sources ---
    skip_idx = {node_index[s] for s in skip if s in node_index}

    for m in sources:
        if m not in node_index:
            continue
        ast      = cfg.nodes[m].get('ast')
        dep_type = ('obsntd' if isinstance(ast, CObserve) else 'obsntd_nt')
        m_idx    = node_index[m]

        anc_m  = reach[:, m_idx]     # who reaches m
        desc_m = reach[m_idx, :]     # what m reaches

        # Union of descendants of all ancestors — vectorised OR over rows
        influenced       = reach[anc_m].any(axis=0)
        influenced      &= ~anc_m
        influenced      &= ~desc_m
        influenced[m_idx] = False
        for s_idx in skip_idx:
            influenced[s_idx] = False

        for j in np.nonzero(influenced)[0]:
            obsntd.add_edge(m, all_nodes[j], dep_type=dep_type)

    return obsntd

def build_obsntd_old(cfg: nx.DiGraph,
                 ddg: nx.DiGraph,
                 cd:  nx.DiGraph) -> nx.DiGraph:
    """
    Observe-nontermination dependence: obsntd(cd) — Definition 6.1.

    m --obsntd(cd)--> n  iff  exists n' s.t.
      n' (cd union dd)* m  AND  n' (cd union dd)* n

    Combined graph: strictly (cd union dd), no CFG edges.
    Sources: CObserve nodes + nonterminating while predicates.

    The difference between obsntd(scd) and obsntd(wcd) is purely
    in the cd relation passed in — scd has more edges than wcd
    for nonterminating loops, so obsntd(scd) discovers more
    influenced nodes than obsntd(wcd).
    """
    obsntd   = nx.DiGraph()
    obsntd.add_nodes_from(cfg.nodes(data=True))
    combined = _build_combined(cfg,cd, ddg)
    sources  = _obsntd_sources(cfg)
    print(f"obsntd sources: {len(sources)} nodes:")
    print("Combined graph has", combined.number_of_edges(), "edges")
    print("cd has", cd.number_of_edges(), "edges")
    print("ddg has", ddg.number_of_edges(), "edges")
    for m in sources:
        ast      = cfg.nodes[m].get('ast')
        dep_type = ('obsntd' if isinstance(ast, CObserve)
                    else 'obsntd_nt')
        n_prime=_reachable_from(m, combined)
        #print(f"  source {m} ({ast}) reaches {len(n_prime)} nodes")
        for n in n_prime:
            if n not in (ENTRY, EXIT):
                obsntd.add_edge(m, n, dep_type=dep_type)

    return obsntd


def build_obsd(cfg: nx.DiGraph,
               ddg: nx.DiGraph,
               cd:  nx.DiGraph) -> nx.DiGraph:
    """
    Observe dependence: obsd(cd) — Definition 6.1, observe case only.

    Same computation as obsntd but sources are CObserve nodes only.
    Nonterminating while predicates are NOT sources for obsd.
    Used for the nontermination-insensitive distribution-insensitive
    slice variant (ni).
    """
    obsd     = nx.DiGraph()
    obsd.add_nodes_from(cfg.nodes(data=True))
    combined = _build_combined(cfg, cd, ddg)
    sources  = _obsd_sources(cfg)

    for m in sources:
        for n in _reachable_from(m, combined):
            if n not in (ENTRY, EXIT):
                obsd.add_edge(m, n, dep_type='obsd')

    return obsd

# ═══════════════════════════════════════════════════════════════════════════════
# Slice Set (Definition 6.5)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_slice_set_old(cfg:       nx.DiGraph,
                      criterion: Set[int],
                      cd:        nx.DiGraph,
                      ddg:       nx.DiGraph,
                      R:         nx.DiGraph) -> Set[int]:

    # Step 1: D = {m' | m' (R)* n' for some n' in C}
    # = nodes that can reach C via R*
    # Direct approach: BFS/DFS backward from C in R
    R_rev = R.reverse(copy=False)
    D: Set[int] = set()
    for n in criterion:
        # Walk backward in R from n
        queue   = [n]
        visited = set()
        while queue:
            curr = queue.pop()
            if curr in visited:
                continue
            visited.add(curr)
            for pred in R_rev.successors(curr):  # successors in R_rev = predecessors in R
                if pred not in visited:
                    D.add(pred)
                    queue.append(pred)

    # Step 2: Build (cd union dd)
    cd_dd = nx.DiGraph()
    cd_dd.add_nodes_from(cfg.nodes())
    for u, v in cd.edges():
        cd_dd.add_edge(u, v)
    for u, v in ddg.edges():
        cd_dd.add_edge(u, v)

    # Step 3: Backward reachability from C union D in (cd union dd)
    seeds     = criterion | D
    cd_dd_rev = cd_dd.reverse(copy=True)

    slice_set: Set[int] = set()
    for n in seeds:
        slice_set.add(n)
        queue   = [n]
        visited = set()
        while queue:
            curr = queue.pop()
            if curr in visited:
                continue
            visited.add(curr)
            for pred in cd_dd_rev.successors(curr):
                if pred not in visited and pred not in (ENTRY, EXIT):
                    slice_set.add(pred)
                    queue.append(pred)

    slice_set.discard(ENTRY)
    slice_set.discard(EXIT)
    return slice_set

# Global debug flag — set to True to enable progress output

def partial_slice(cfg:   nx.DiGraph,
                  seeds: set[int],
                  cd:    nx.DiGraph,
                  ddg:   nx.DiGraph,
                  label: str = '') -> set[int]:
    """
    Algorithm 1: PartialSlice.
    Compute transitive closure of seeds under cd and dd relations.
    """
    S    = set(seeds)
    prev = None
    itr  = 0

    while prev != S:
        prev = S.copy()
        itr += 1

        # closure_G(dd, S)
        dd_closure = set()
        queue      = deque(S)
        visited    = set()
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            dd_closure.add(n)
            for pred in ddg.predecessors(n):
                if pred not in visited:
                    queue.append(pred)

        # closure_G(cd, dd_closure)
        cd_closure = set()
        queue      = deque(dd_closure)
        visited    = set()
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            cd_closure.add(n)
            for pred in cd.predecessors(n):
                if pred not in visited:
                    queue.append(pred)

        S = cd_closure

        if SLICE_DEBUG and label:
            print(f"    [PartialSlice{label}] iter={itr} "
                  f"size={len(S)} added={len(S)-len(prev)}",
                  flush=True)

    return S - {ENTRY, EXIT}


def compute_slice_set(cfg:       nx.DiGraph,
                      criterion: set[int],
                      cd:        nx.DiGraph,
                      ddg:       nx.DiGraph,variant:str) -> set[int]:
    """
    Algorithm 2: CompleteSlice.
    """
    import time
    t0      = time.perf_counter()

    if variant == 'ni':
        N_obsnt  = _obsd_sources(cfg)
    else:
        N_obsnt = _obsntd_sources(cfg)
    total   = cfg.number_of_nodes() - 2  # exclude ENTRY/EXIT

    if SLICE_DEBUG:
        print(f"  [Slice] criterion={len(criterion)} nodes, "
              f"obsntd_sources={len(N_obsnt)}, "
              f"total_nodes={total}",
              flush=True)

    # Line 2: S_C <- PartialSlice(G, C, cd, dd)
    if SLICE_DEBUG:
        print(f"  [Slice] Step 1: PartialSlice from criterion...",
              flush=True)
    t1  = time.perf_counter()
    S_C = partial_slice(cfg, criterion, cd, ddg,
                        label=' [criterion]' if SLICE_DEBUG else '')
    if SLICE_DEBUG:
        print(f"  [Slice] Step 1 done: |S_C|={len(S_C)} "
              f"({100*len(S_C)/max(total,1):.1f}%) "
              f"in {time.perf_counter()-t1:.2f}s",
              flush=True)

    # Line 3: precompute V(n) for all obsntd nodes
    if SLICE_DEBUG:
        print(f"  [Slice] Step 2: precomputing V(n) for "
              f"{len(N_obsnt)} obsntd sources...", flush=True)
    t2 = time.perf_counter()
    V  = {}
    for i, n in enumerate(N_obsnt):
        V[n] = partial_slice(cfg, {n}, cd, ddg)
        if SLICE_DEBUG and ((i + 1) % 10 == 0 or (i + 1) == len(N_obsnt)):
            elapsed = time.perf_counter() - t2
            rate    = (i + 1) / max(elapsed, 0.001)
            eta     = (len(N_obsnt) - i - 1) / max(rate, 0.001)
            print(f"  [Slice] Step 2: {i+1}/{len(N_obsnt)} "
                  f"({100*(i+1)/len(N_obsnt):.1f}%) "
                  f"elapsed={elapsed:.1f}s ETA={eta:.1f}s",
                  flush=True)
    if SLICE_DEBUG:
        print(f"  [Slice] Step 2 done in "
              f"{time.perf_counter()-t2:.2f}s", flush=True)

    # Lines 4-7: fixpoint
    if SLICE_DEBUG:
        print(f"  [Slice] Step 3: fixpoint expansion...", flush=True)
    t3        = time.perf_counter()
    remaining = set(N_obsnt)
    changed   = True
    fp_iter   = 0

    while changed:
        changed  = False
        fp_iter += 1
        triggered = {n for n in remaining if V[n] & S_C}
        if triggered:
            for n in triggered:
                S_C     |= V[n]
                remaining.discard(n)
                changed   = True
            if SLICE_DEBUG:
                print(f"  [Slice] Step 3 iter={fp_iter}: "
                      f"triggered={len(triggered)} "
                      f"|S_C|={len(S_C)} "
                      f"({100*len(S_C)/max(total,1):.1f}%) "
                      f"remaining={len(remaining)} "
                      f"elapsed={time.perf_counter()-t3:.2f}s",
                      flush=True)

    if SLICE_DEBUG:
        print(f"  [Slice] Step 3 done: {fp_iter} iterations "
              f"in {time.perf_counter()-t3:.2f}s", flush=True)
        print(f"  [Slice] Total: |S_C|={len(S_C)} "
              f"({100*len(S_C)/max(total,1):.1f}%) "
              f"in {time.perf_counter()-t0:.2f}s",
              flush=True)

    return S_C
def partial_slice_with_progress(cfg: nx.DiGraph,
                  seeds:  set[int],
                  cd:     nx.DiGraph,
                  ddg:    nx.DiGraph,
                  label:  str = '') -> set[int]:
    """
    Algorithm 1: PartialSlice.
    Compute transitive closure of seeds under cd and dd relations.
    """
    S    = set(seeds)
    prev = None
    itr  = 0

    while prev != S:
        prev = S.copy()
        itr += 1

        # closure_G(dd, S)
        dd_closure = set()
        queue      = deque(S)
        visited    = set()
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            dd_closure.add(n)
            for pred in ddg.predecessors(n):
                if pred not in visited:
                    queue.append(pred)

        # closure_G(cd, dd_closure)
        cd_closure = set()
        queue      = deque(dd_closure)
        visited    = set()
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            cd_closure.add(n)
            for pred in cd.predecessors(n):
                if pred not in visited:
                    queue.append(pred)

        S = cd_closure

        if label:
            print(f"    [PartialSlice{label}] iter={itr} "
                  f"size={len(S)} added={len(S)-len(prev)}",
                  flush=True)

    return S - {ENTRY, EXIT}


def compute_slice_set_with_progress(cfg:       nx.DiGraph,
                      criterion: set[int],
                      cd:        nx.DiGraph,
                      ddg:       nx.DiGraph) -> set[int]:
    """
    Algorithm 2: CompleteSlice.
    """
    import time
    t0 = time.perf_counter()

    N_obsnt = _obsntd_sources(cfg)
    total   = cfg.number_of_nodes() - 2  # exclude ENTRY/EXIT

    print(f"  [Slice] criterion={len(criterion)} nodes, "
          f"obsntd_sources={len(N_obsnt)}, "
          f"total_nodes={total}",
          flush=True)

    # Line 2: S_C <- PartialSlice(G, C, cd, dd)
    print(f"  [Slice] Step 1: PartialSlice from criterion...", flush=True)
    t1  = time.perf_counter()
    S_C = partial_slice(cfg, criterion, cd, ddg, label=' [criterion]')
    print(f"  [Slice] Step 1 done: |S_C|={len(S_C)} "
          f"({100*len(S_C)/max(total,1):.1f}%) "
          f"in {time.perf_counter()-t1:.2f}s",
          flush=True)

    # Line 3: precompute V(n) for all obsntd nodes
    print(f"  [Slice] Step 2: precomputing V(n) for "
          f"{len(N_obsnt)} obsntd sources...", flush=True)
    t2 = time.perf_counter()
    V  = {}
    for i, n in enumerate(N_obsnt):
        V[n] = partial_slice(cfg, {n}, cd, ddg)
        if (i + 1) % 10 == 0 or (i + 1) == len(N_obsnt):
            elapsed = time.perf_counter() - t2
            rate    = (i + 1) / max(elapsed, 0.001)
            eta     = (len(N_obsnt) - i - 1) / max(rate, 0.001)
            print(f"  [Slice] Step 2: {i+1}/{len(N_obsnt)} "
                  f"({100*(i+1)/len(N_obsnt):.1f}%) "
                  f"elapsed={elapsed:.1f}s ETA={eta:.1f}s",
                  flush=True)
    print(f"  [Slice] Step 2 done in "
          f"{time.perf_counter()-t2:.2f}s", flush=True)

    # Lines 4-7: fixpoint
    print(f"  [Slice] Step 3: fixpoint expansion...", flush=True)
    t3        = time.perf_counter()
    remaining = set(N_obsnt)
    changed   = True
    fp_iter   = 0

    while changed:
        changed  = False
        fp_iter += 1
        triggered = {
            n for n in remaining
            if V[n] & S_C
        }
        if triggered:
            for n in triggered:
                before   = len(S_C)
                S_C     |= V[n]
                remaining.discard(n)
                changed   = True
            print(f"  [Slice] Step 3 iter={fp_iter}: "
                  f"triggered={len(triggered)} "
                  f"|S_C|={len(S_C)} "
                  f"({100*len(S_C)/max(total,1):.1f}%) "
                  f"remaining={len(remaining)} "
                  f"elapsed={time.perf_counter()-t3:.2f}s",
                  flush=True)

    print(f"  [Slice] Step 3 done: {fp_iter} iterations "
          f"in {time.perf_counter()-t3:.2f}s", flush=True)
    print(f"  [Slice] Total: |S_C|={len(S_C)} "
          f"({100*len(S_C)/max(total,1):.1f}%) "
          f"in {time.perf_counter()-t0:.2f}s",
          flush=True)

    return S_C

def partial_slice_progressless(cfg: nx.DiGraph,
                  seeds:  set[int],
                  cd:     nx.DiGraph,
                  ddg:    nx.DiGraph) -> set[int]:
    """
    Algorithm 1: PartialSlice.
    Compute transitive closure of seeds under cd and dd relations.
    """
    # Build combined graph once
    combined = nx.DiGraph()
    combined.add_nodes_from(cfg.nodes())
    combined.add_edges_from(cd.edges())
    combined.add_edges_from(ddg.edges())

    # Reverse: we want nodes that seeds depend ON
    # i.e. follow edges backwards from seeds
    rev  = combined.reverse(copy=False)
    S    = set()
    prev = None

    while prev != S:
        prev = S.copy()
        # closure_G(dd, S_O)
        worklist = list(seeds | S)
        visited  = set()
        queue    = deque(worklist)
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            for pred in ddg.predecessors(n):
                if pred not in visited:
                    queue.append(pred)
        dd_closure = visited

        # closure_G(cd, dd_closure)
        visited2 = set()
        queue2   = deque(dd_closure)
        while queue2:
            n = queue2.popleft()
            if n in visited2:
                continue
            visited2.add(n)
            for pred in cd.predecessors(n):
                if pred not in visited2:
                    queue2.append(pred)

        S = visited2

    return S - {ENTRY, EXIT}


def compute_slice_set_progressless(cfg:      nx.DiGraph,
                      criterion: set[int],
                      cd:        nx.DiGraph,
                      ddg:       nx.DiGraph) -> set[int]:
    """
    Algorithm 2: CompleteSlice.
    """
    # Line 2: S_C <- PartialSlice(G, C, cd, dd)
    S_C = partial_slice(cfg, criterion, cd, ddg)

    # Line 3: precompute V(n) for all obsntd nodes
    N_obsnt = _obsntd_sources(cfg)
    V = {
        n: partial_slice(cfg, {n}, cd, ddg)
        for n in N_obsnt
    }

    # Lines 4-7: while exists n in N_obsnt s.t. V(n) ∩ S_C ≠ ∅
    remaining = set(N_obsnt)
    changed   = True
    while changed:
        changed = False
        triggered = {
            n for n in remaining
            if V[n] & S_C          # V(n) ∩ S_C ≠ ∅
        }
        for n in triggered:
            S_C      |= V[n]       # line 5
            remaining.discard(n)   # line 6
            changed = True

    return S_C
# ═══════════════════════════════════════════════════════════════════════════════
# Dependence Analysis Facade
# ═══════════════════════════════════════════════════════════════════════════════

class DependenceAnalysis:
    """
    Facade for all three slicing variants.

    variant='ns'   — nontermination-sensitive, distribution-sensitive
                     cd=scd, R=obsntd(scd)
    variant='nids' — nontermination-insensitive, distribution-sensitive
                     cd=wcd, R=obsntd(wcd)
    variant='ni'   — nontermination-insensitive, distribution-insensitive
                     cd=wcd, R=obsd(wcd)
    """

    def __init__(self, cfg: nx.DiGraph, variant: SliceVariant = 'ns'):
        self.cfg     = cfg
        self.variant = variant
        self.rd:     ReachingDefinitions | None = None
        self.ddg:    nx.DiGraph | None = None
        self.cd:     nx.DiGraph | None = None  # scd or wcd depending on variant
        self.R:      nx.DiGraph | None = None  # obsntd or obsd depending on variant

    def compute(self):
        """Run all analyses."""
        import time
        t = time.perf_counter()
        # 1. Reaching definitions
        self.rd = ReachingDefinitions(self.cfg)
        self.rd.compute()

        # 2. DDG
        self.ddg = build_ddg(self.cfg, self.rd)

        print(f"  [TIMER] ddg: {time.perf_counter()-t:.2f}s  nodes={self.cfg.number_of_nodes()}", flush=True)

        # 3. Control dependence: scd for ns, wcd for nids and ni
        
        t = time.perf_counter()

        if self.variant == 'ns':
            self.cd = build_scd(self.cfg)
        else:
            self.cd = build_wcd(self.cfg)
        
        print(f"  [TIMER] cd:  {time.perf_counter()-t:.2f}s", flush=True)
        '''
        # 4. Observe(-nontermination) dependence R
        t = time.perf_counter()
        if self.variant == 'ns':
            print("building obsntd with scd...")
            self.R = build_obsntd(self.cfg, self.ddg, self.cd)
        elif self.variant == 'nids':
            print("building obsntd with wcd...")
            self.R = build_obsntd(self.cfg, self.ddg, self.cd)
        else:  # 'ni'
            print("building obsd with wcd...")
            self.R = build_obsd(self.cfg, self.ddg, self.cd)
        print(f"  [TIMER] R:   {time.perf_counter()-t:.2f}s", flush=True)
    '''
    def slice(self, criterion: Set[int]) -> Set[int]:
        """
        Compute the slice set for the given criterion nodes,
        using Definition 6.5.
        """
        if self.rd is None:
            raise RuntimeError("Call compute() before slice()")
        return compute_slice_set(
            self.cfg, criterion, self.cd, self.ddg, self.variant
        )

    def observe_nodes(self) -> Set[int]:
        """Return all CFG node IDs that are CObserve commands."""
        return {
            nid for nid, data in self.cfg.nodes(data=True)
            if isinstance(data.get('ast'), CObserve)
        }

    # ── Reporting ─────────────────────────────────────────────────────────────

    def print_report(self):
        print(f"=== Variant: {self.variant} ===")
        print(f"    cd  = {'scd' if self.variant == 'ns' else 'wcd'}")
        print(f"    R   = {'obsntd' if self.variant != 'ni' else 'obsd'}")

        print("\n=== Reaching Definitions ===")
        for nid in sorted(self.cfg.nodes):
            if nid in (ENTRY, EXIT):
                continue
            label = self.cfg.nodes[nid].get('label', str(nid))
            print(f"  [{nid}] {label}")
            print(f"       IN = {sorted(self.rd.IN.get(nid, set()))}")

        print("\n=== DDG edges ===")
        for u, v, d in sorted(self.ddg.edges(data=True)):
            print(f"  {u} --[{d['dep_type']}:{d.get('var','')}]--> {v}")

        print(f"\n=== CD edges ({'scd' if self.variant == 'ns' else 'wcd'}) ===")
        for u, v, d in sorted(self.cd.edges(data=True)):
            print(f"  {u} --[{d['dep_type']}]--> {v}")

        print(f"\n=== R edges ({'obsntd' if self.variant != 'ni' else 'obsd'}) ===")
        for u, v, d in sorted(self.R.edges(data=True)):
            print(f"  {u} --[{d['dep_type']}:{d.get('var','')}]--> {v}")

    def pdg_to_dot(self) -> str:
        """Visualize the combined dependence graph as DOT."""
        combined = nx.DiGraph()
        combined.add_nodes_from(self.cfg.nodes(data=True))
        for g, color in [(self.ddg, 'black'), (self.cd, 'red'),
                         (self.R, 'orange')]:
            for u, v, d in g.edges(data=True):
                combined.add_edge(u, v, **d)

        lines = ['digraph PDG {', '  rankdir=TB;']
        for nid, data in combined.nodes(data=True):
            label = data.get('label', str(nid)).replace('"', '\\"')
            lines.append(f'  {nid} [label="{label}"];')
        for u, v, d in combined.edges(data=True):
            dt    = d.get('dep_type', '')
            color = {'data': 'black', 'stoch_data': 'blue',
                     'scd': 'red', 'wcd': 'darkred',
                     'obsntd': 'orange',
                     'obsd': 'goldenrod'}.get(dt, 'gray')
            var    = d.get('var', '')
            elabel = f"{dt}({var})" if var else dt
            lines.append(
                f'  {u} -> {v} [label="{elabel}", color={color}];'
            )
        lines.append('}')
        return '\n'.join(lines)