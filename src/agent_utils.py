"""
agent_utils.py
==============
Terminal trace UI for `test` / `chat` runs: ANSI colour constants, a
sys.stdout proxy that keeps parallel-retrieval output from interleaving,
and AgentTrace - the object agent_orchestrator.py calls at each routing
step so a session's WorkflowState transitions are visible as they happen.

None of this affects what the agents actually do - it is purely
presentational, and safe to skip entirely by not calling setup_logging()
with a terminal attached.
"""

import re
import sys


class _Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GRY = "\033[90m"
    OK = "\033[32m"
    ORCH = "\033[36m"   # orchestrator
    INV = "\033[34m"    # inventory agent
    REF = "\033[33m"    # refund agent
    POL = "\033[35m"    # policy agent
    COM = "\033[32m"    # communication agent
    W = 70               # trace UI width, in characters


_C = _Colors()

# column name (WorkflowState) -> (colour, display label)
_AGENT_META = {
    "inventory_agent": (_C.INV, "InventoryAgent"),
    "policy_agent": (_C.POL, "PolicyAgent"),
    "refund_agent": (_C.REF, "RefundAgent"),
    "communication_agent": (_C.COM, "CommunicationAgent"),
}

_XML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_xml_tags(text: str) -> str:
    """Strip any stray XML/HTML-style tags a model response might include."""
    return _XML_TAG_RE.sub("", text or "").strip()


class _TraceWriter:
    """A sys.stdout stand-in. While `_suppress_parallel` is set (during the
    PolicyAgent's three-way parallel retrieval) everything written through it
    is dropped, so the three retriever sub-agents' interleaved output doesn't
    scramble the terminal; AgentTrace.kb_result() prints a clean summary once
    all three have finished instead.
    """

    _suppress_parallel = False

    def __init__(self, real_stdout):
        self._real = real_stdout

    def write(self, text):
        if not self._suppress_parallel:
            self._real.write(text)

    def flush(self):
        self._real.flush()


_real_stdout = sys.stdout
_trace_writer = _TraceWriter(_real_stdout)


def _trace_print(*args, **kwargs):
    """Print to the real stdout even while `_trace_writer` is installed."""
    print(*args, file=_real_stdout, **kwargs)


class AgentTrace:
    """Prints each WorkflowState transition as the orchestrator routes a
    request, so running `test` or `chat` shows the multi-agent graph in
    motion instead of just a final answer."""

    def __init__(self, read_state_fn):
        self._read_state = read_state_fn

    def new_turn(self) -> None:
        _TraceWriter._suppress_parallel = False

    def step_start(self, column: str) -> None:
        _, label = _AGENT_META.get(column, (_C.GRY, column.upper()))
        _trace_print(f"  {_C.GRY}-> routing to {label}...{_C.RESET}")

    def agent_section(self, label: str) -> None:
        _trace_print(f"  {_C.BOLD}[{label}]{_C.RESET}")

    def step_done(self, column: str, old_version: int) -> None:
        color, label = _AGENT_META.get(column, (_C.GRY, column.upper()))
        _trace_print(f"  {color}[OK]{_C.RESET} {label} wrote WorkflowState v{old_version + 1}")

    def kb_start(self, kb_ids: dict) -> None:
        _TraceWriter._suppress_parallel = True
        _trace_print(f"  {_C.GRY}[PARALLEL RETRIEVAL - START] "
                      f"Spawning {len(kb_ids)} sub-agents concurrently via "
                      f"ThreadPoolExecutor{_C.RESET}")
        for domain, kb_id in kb_ids.items():
            _trace_print(f"    +-- [{domain}Retriever]  KB-ID: {kb_id}")

    def kb_done(self, count: int) -> None:
        _TraceWriter._suppress_parallel = False
        _trace_print(f"  {_C.GRY}[PARALLEL RETRIEVAL - END] All {count} KBs responded{_C.RESET}")

    def kb_result(self, domain: str, text: str) -> None:
        preview = text if len(text) <= 200 else text[:200] + "…"
        _trace_print(f"    [{domain}] {preview}")

    def summary(self, session_id: str, elapsed: float) -> None:
        state = self._read_state(session_id) or {}
        _trace_print(f"\n  {_C.GRY}WorkflowState v{state.get('version', 0)} "
                      f"(session {session_id}, {elapsed:.1f}s){_C.RESET}")
        for column in ("inventory_agent", "policy_agent", "refund_agent", "communication_agent"):
            mark = "x" if state.get(column) else " "
            _trace_print(f"    [{mark}] {column}")
