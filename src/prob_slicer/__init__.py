"""prob_slicer — Static dependence-based slicing for probabilistic programs."""
from .parser      import parse
from .cfg_builder import build_cfg, cfg_to_dot
from .dependence  import DependenceAnalysis
from .slicer      import slice_program, pretty_print

__all__ = [
    'parse', 'build_cfg', 'cfg_to_dot',
    'DependenceAnalysis',
    'slice_program', 'pretty_print',
]
