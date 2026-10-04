"""Graph construction.

Both workflows are declared once in ``_declare_*``. ``compile_graph`` uses
LangGraph's ``StateGraph`` when LangGraph is installed; otherwise it uses
``SequentialGraph``, a minimal executor with the same node/edge/conditional
semantics (used in environments where LangGraph cannot be installed, and to
unit-test the graph shape without it).

Durability: graph state contains only ids. All results are persisted by the
nodes themselves (SQLite + artifact store), and review decisions live in the
repository, so an interrupted run can be retried from the persisted session
status without losing user decisions. LangGraph checkpointers are therefore
not required for correctness.

Proposal graph::

    request_proposals ──(error)──► record_analysis_failure ─► END
            └──────────(ok)─────► END

Finalize graph::

    load_review ─(stale)─► record_finalization ─► END
        └─► apply_edits ─(failed)─► record_finalization
                 └─► evaluate_fidelity ─► record_finalization ─► END
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from functools import partial
from typing import Any

from app.workflow import nodes
from app.workflow.nodes import WorkflowDeps
from app.workflow.state import FinalizeState, ProposalState

END = "__end__"

try:  # pragma: no cover - exercised only where LangGraph is installed
    from langgraph.graph import END as LG_END
    from langgraph.graph import StateGraph

    HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover
    HAS_LANGGRAPH = False


class SequentialGraph:
    """Tiny StateGraph-compatible executor (subset: nodes, edges, conditional edges)."""

    def __init__(self, state_type: type) -> None:
        self.state_type = state_type
        self.nodes: dict[str, Callable[..., Any]] = {}
        self.edges: dict[str, str] = {}
        self.conditional: dict[str, tuple[Callable[[dict], str], dict[str, str]]] = {}
        self.entry: str | None = None

    def add_node(self, name: str, fn: Callable[..., Any]) -> None:
        self.nodes[name] = fn

    def add_edge(self, src: str, dst: str) -> None:
        self.edges[src] = dst

    def add_conditional_edges(self, src: str, router: Callable[[dict], str], mapping: dict[str, str]) -> None:
        self.conditional[src] = (router, mapping)

    def set_entry_point(self, name: str) -> None:
        self.entry = name

    def compile(self) -> SequentialGraph:
        if self.entry is None:
            raise ValueError("entry point not set")
        for src in list(self.edges) + list(self.conditional):
            if src not in self.nodes:
                raise ValueError(f"edge from unknown node {src}")
        return self

    async def ainvoke(self, state: dict, config: dict | None = None, max_steps: int = 50) -> dict:
        current = self.entry
        state = dict(state)
        steps = 0
        while current and current != END:
            steps += 1
            if steps > max_steps:
                raise RuntimeError("graph did not terminate")
            result = self.nodes[current](state)
            if inspect.isawaitable(result):
                result = await result
            state.update(result or {})
            if current in self.conditional:
                router, mapping = self.conditional[current]
                current = mapping[router(state)]
            else:
                current = self.edges.get(current, END)
        return state


def _async(fn: Callable[..., Any], deps: WorkflowDeps) -> Callable[[dict], Any]:
    """Wrap a node: coroutines run on the loop; blocking (CPU/subprocess) nodes run in a worker thread."""
    bound = partial(fn, deps=deps)
    is_coroutine = inspect.iscoroutinefunction(fn)

    async def runner(state: dict) -> Any:
        if is_coroutine:
            return await bound(state)
        return await asyncio.to_thread(bound, state)

    runner.__name__ = fn.__name__
    return runner


def _builder(state_type: type, use_langgraph: bool):
    if use_langgraph and HAS_LANGGRAPH:
        return StateGraph(state_type), LG_END
    return SequentialGraph(state_type), END


def build_proposal_graph(deps: WorkflowDeps, use_langgraph: bool = True):
    g, end = _builder(ProposalState, use_langgraph)
    g.add_node("request_proposals", _async(nodes.request_proposals, deps))
    g.add_node("record_analysis_failure", _async(nodes.record_analysis_failure, deps))
    g.set_entry_point("request_proposals")
    g.add_conditional_edges("request_proposals", nodes.route_after_proposals, {"failed": "record_analysis_failure", "done": end})
    g.add_edge("record_analysis_failure", end)
    return g.compile()


def build_finalize_graph(deps: WorkflowDeps, use_langgraph: bool = True):
    g, end = _builder(FinalizeState, use_langgraph)
    g.add_node("load_review", _async(nodes.load_review, deps))
    g.add_node("apply_edits", _async(nodes.apply_accepted_edits, deps))
    g.add_node("evaluate_fidelity", _async(nodes.evaluate_fidelity, deps))
    g.add_node("record_finalization", _async(nodes.record_finalization, deps))
    g.set_entry_point("load_review")
    g.add_conditional_edges("load_review", nodes.route_after_review, {"failed": "record_finalization", "apply": "apply_edits"})
    g.add_conditional_edges("apply_edits", nodes.route_after_apply, {"failed": "record_finalization", "evaluate": "evaluate_fidelity"})
    g.add_edge("evaluate_fidelity", "record_finalization")
    g.add_edge("record_finalization", end)
    return g.compile()
