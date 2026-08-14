"""Wires the existing tool registry + grounding pipeline into the LiveKit
AgentSession (T-14; docs/DESIGN.md §Interfaces).

Nothing here reimplements a tool, the grounding policy, or the routing
decision -- it only adapts them to the shapes the installed livekit-agents
SDK (1.6.10) wants:

* ``build_registry`` registers the *real* ``ToolSpec``s from T-2/T-3/T-4
  (``jarvis.tools.weather``, ``jarvis.tools.github``,
  ``jarvis.tools.self_manifest``) verbatim, plus one new ``ToolSpec`` for
  memory recall that is a thin shape-adapter over the real T-5
  ``MemoryStore.search`` (see ``LazyMemoryStore`` / ``_memory_tool_spec``
  below) -- no search/embedding/dedup logic lives here, only packing its
  ``list[ToolResult]`` into the single ``ToolResult`` shape
  ``ToolRegistry``/``ToolSpec`` are built around.
* ``livekit_tools`` adapts each ``ToolSpec`` into a livekit
  ``RawFunctionTool`` via ``function_tool(..., raw_schema=...)`` -- schemas
  are forwarded verbatim (confirmed against the installed Anthropic
  provider adapter: a ``RawFunctionTool``'s ``raw_schema`` is sent to the
  model unchanged, no re-derivation), so ``ToolSpec.parameters`` (already a
  JSON Schema) needs no translation.

**How a tool call becomes the spoken reply -- and why it does NOT reuse the
SDK's default "tool result feeds back into the LLM for a second turn" flow:**

The pinned contract (docs/DESIGN.md §Interfaces) is that ``ANSWER_POLICY``
governs the text of any turn where a tool fired, and CONVO_MODEL's own
``instructions`` govern tool *selection* and plain chit-chat -- the two
system prompts are never merged into one call (confirmed against
``llm/synthesis.py``: ``synthesize_grounded`` makes its own standalone
Anthropic call with ``system=ANSWER_POLICY``, independent of whatever
chat_ctx/instructions the voice-persona pass used). Letting the SDK's normal
post-tool continuation turn run would hand the *tool-selection* pass (under
Jarvis's persona instructions, on CONVO_MODEL only, no ANSWER_POLICY) a
second, ungrounded chance to phrase the answer -- exactly what R6 exists to
prevent.

So each tool dispatcher below, after running the real handler:
  1. builds the answer itself via the real ``synthesize_grounded`` (which
     picks the escalated model for `heavy` tools via the real
     ``llm.routing.route_model`` -- see module docstring there: this is a
     plain function returning a model-id string, a standalone Anthropic
     call, NOT a livekit-level LLM swap. An earlier design pass explicitly
     checked ``Agent.update_options(llm=...)``/``Agent.llm_node`` override
     and found neither applicable here: both would re-point the
     tool-selection pass, not the answer-synthesis pass, which is the only
     one DESIGN.md's routing section actually escalates),
  2. speaks that text directly via ``AgentSession.say()`` (bypasses the LLM
     entirely for the spoken output), then
  3. raises ``llm.StopResponse`` so the SDK's tool-executor does not also
     schedule its default post-tool continuation turn (confirmed against
     the installed SDK: ``StopResponse`` -> ``fnc_call_out=None`` ->
     ``generate_tool_reply`` stays ``False`` in ``agent_activity.py`` --
     both public, documented mechanisms, not private internals).

This is how "persona holds, grounding wins" is actually achieved: not by
concatenating ``ANSWER_POLICY`` into ``JarvisAgent.instructions``, but by the
pipeline layer choosing, per turn, which of the two passes' output becomes
the spoken reply -- Jarvis's instructions when no tool fires, the grounded
synthesis output when one does.

**T-15 addition -- the tool call no longer blocks the event loop, and a slow
`heavy` tool now gets a spoken acknowledgment:** ``spec.handler`` is plain
sync (``ToolHandler = Callable[..., ToolResult]``, ``tools/registry.py``) and
the real handlers (e.g. ``get_weather``'s ``httpx.Client``) are blocking. Each
dispatcher below now runs the handler via ``asyncio.to_thread`` instead of
calling it directly inline -- a direct call inside this ``async def`` would
block the *entire* event loop for the call's full duration (1.6-14.4s
measured on deployment), starving everything else, including the real T-6
``FillerGate``'s own timer task. ``asyncio.to_thread`` frees the loop so
that timer (armed only for ``spec.heavy`` tools, per the pinned
``ToolSpec.heavy``/``$TOOL_MODEL`` contract in docs/DESIGN.md §Interfaces)
can actually fire and speak one pre-recorded filler clip if the call outlives
``FILLER_THRESHOLD_MS`` -- see ``jarvis.cadence.gate.FillerGate.guard`` (T-6,
reused unmodified) and ``jarvis.pipeline.cadence_wiring`` (the SDK-facing
adapter that plays a clip through ``session.say()``).

**Known limitation, stated plainly:** if the CONVO_MODEL fires more than one
tool in the same turn (parallel tool calls), each fired tool's dispatcher
independently calls ``session.say()`` -- there is no cross-call
coordination to merge multiple ``ToolResult``s into one combined grounded
answer. Every acceptance scenario for this ticket is single-tool-per-turn;
fixing the multi-tool case would mean buffering across concurrent
``RunContext`` calls, a bigger change than this wiring ticket's scope.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from livekit.agents import RunContext
from livekit.agents.llm import RawFunctionTool, StopResponse, function_tool

from jarvis.cadence.gate import FillerGate
from jarvis.llm.routing import route_model
from jarvis.llm.synthesis import ModelClient, synthesize_grounded
from jarvis.llm.types import REFUSAL, ToolResult
from jarvis.memory.store import MemoryStore
from jarvis.tools.github import GITHUB_DIGEST_TOOL, GITHUB_QUERY_TOOL
from jarvis.tools.registry import ToolRegistry, ToolSpec
from jarvis.tools.self_manifest import SELF_MANIFEST_TOOL
from jarvis.tools.weather import WEATHER_TOOL

logger = logging.getLogger(__name__)

MEMORY_TOOL_NAME = "recall_memory"
_MEMORY_SOURCE = "memory:mem0"


class LazyMemoryStore:
    """Builds the real ``Mem0MemoryStore`` against ``database_url`` on first
    use only, and caches a connection failure so a dead Postgres is not
    retried on every turn.

    Constructing ``Mem0MemoryStore`` eagerly at session start would mean a
    down/misconfigured database takes the whole voice loop down before the
    first turn even happens; T-14's requirement is that only the memory
    *tool* degrades (a ``ToolResult`` with ``error`` set), never the
    process. ``mem0``/``fastembed`` are only imported transitively the
    first time ``get()`` actually tries to build the store.
    """

    def __init__(self, database_url: str | None) -> None:
        self._database_url = database_url
        self._store: MemoryStore | None = None
        self._failed = False

    def get(self) -> MemoryStore | None:
        if self._store is not None:
            return self._store
        if self._failed or not self._database_url:
            return None
        try:
            from jarvis.memory.store import Mem0MemoryStore

            self._store = Mem0MemoryStore(database_url=self._database_url)
        except Exception:
            logger.exception("memory store unavailable; degrading gracefully")
            self._failed = True
            return None
        return self._store

    def preferences(self, user_id: str) -> list[str]:
        """Best-effort ``get_preferences`` for ``synthesize_grounded``'s
        ``prefs`` argument -- an unavailable store degrades to no
        preferences rather than failing the turn.
        """
        store = self.get()
        if store is None:
            return []
        try:
            return store.get_preferences(user_id)
        except Exception:
            logger.exception("get_preferences failed; continuing without prefs")
            return []


def _memory_tool_spec(memory: LazyMemoryStore, user_id_getter: Callable[[], str]) -> ToolSpec:
    """Adapts the real ``MemoryStore.search`` (T-5) into one ``ToolSpec``.

    ``search`` returns ``list[ToolResult]`` (one per matched memory);
    ``ToolSpec.handler`` is pinned to return a single ``ToolResult`` (see
    ``jarvis.tools.registry.ToolHandler``), so this packs the matches into
    one result's ``data["memories"]`` -- no search/scoring/dedup logic is
    reimplemented here, only the packing.
    """

    def handler(*, query: str, k: int = 5) -> ToolResult:
        fetched_at = datetime.now(UTC).isoformat()
        store = memory.get()
        if store is None:
            return ToolResult(
                source=_MEMORY_SOURCE,
                fetched_at=fetched_at,
                data={},
                error="memory store unavailable",
            )
        user_id = user_id_getter()
        try:
            results = store.search(user_id=user_id, query=query, k=k)
        except Exception as exc:
            logger.exception("memory search failed")
            return ToolResult(source=_MEMORY_SOURCE, fetched_at=fetched_at, data={}, error=str(exc))

        memories = [r["data"] for r in results if not r.get("error")]
        errors = [r["error"] for r in results if r.get("error")]
        if not memories and errors:
            return ToolResult(
                source=_MEMORY_SOURCE, fetched_at=fetched_at, data={}, error="; ".join(errors)
            )
        return ToolResult(
            source=_MEMORY_SOURCE,
            fetched_at=fetched_at,
            data={"memories": memories, "fetched_at": fetched_at},
            error=None,
        )

    return ToolSpec(
        name=MEMORY_TOOL_NAME,
        description=(
            "Recall things the user has told Jarvis in past sessions -- "
            "preferences, facts, prior requests. Use this when the user "
            "refers to something from an earlier conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to recall, phrased in the user's own words.",
                },
                "k": {"type": "integer", "description": "Max memories to return (default 5)."},
            },
            "required": ["query"],
        },
        handler=handler,
        heavy=False,
    )


def build_registry(*, memory: LazyMemoryStore, user_id_getter: Callable[[], str]) -> ToolRegistry:
    """The real tool set for one session: weather (T-3), github (T-4),
    self_manifest (T-2), and memory recall (T-5, adapted above). Every
    ``ToolSpec`` except the memory one is registered completely unmodified.
    """
    registry = ToolRegistry()
    registry.register(WEATHER_TOOL)
    registry.register(GITHUB_QUERY_TOOL)
    registry.register(GITHUB_DIGEST_TOOL)
    registry.register(SELF_MANIFEST_TOOL)
    registry.register(_memory_tool_spec(memory, user_id_getter))
    return registry


def _last_user_utterance(chat_ctx: Any) -> str:
    """The most recent user turn's text, for ``synthesize_grounded``'s
    ``question`` argument. Walks backward from the tool-call turn; empty
    when (defensively) no prior user item carries text.
    """
    for item in reversed(chat_ctx.items):
        if getattr(item, "role", None) == "user":
            text = getattr(item, "text_content", None)
            if text:
                return text
    return ""


def _make_dispatcher(
    spec: ToolSpec,
    *,
    prefs_getter: Callable[[], list[str]],
    model_client: ModelClient | None,
    gate: FillerGate | None = None,
) -> Callable[[dict[str, Any], RunContext], Any]:
    """Builds the raw-function-tool callable for one ``ToolSpec``. See the
    module docstring for why this speaks the answer directly and raises
    ``StopResponse`` instead of returning text for the LLM to re-narrate,
    and for why the handler now runs via ``asyncio.to_thread`` under an
    optional ``FillerGate``.

    ``model_client`` is the same injection seam ``synthesize_grounded``
    already defines (``llm/synthesis.py``'s ``ModelClient`` protocol) --
    threaded through here so tests can drive a real turn through the real
    dispatcher without a network call, exactly like the existing
    tools/weather and grounding test suites do. Production callers
    (``worker.py``) never pass it, so this degrades to the real
    ``AnthropicModelClient`` unchanged.

    ``gate`` is the real T-6 ``FillerGate`` (one per session, built by
    ``jarvis.pipeline.cadence_wiring.build_filler_gate``) or ``None`` when
    the filler assets were unavailable at session start -- ``None`` degrades
    to running the tool call with no filler-eligibility at all, never a
    crash (see that module's docstring).
    """

    async def _dispatch(raw_arguments: dict[str, Any], ctx: RunContext) -> None:
        question = _last_user_utterance(ctx.session.history) or f"({spec.name} request)"

        try:
            call = asyncio.to_thread(spec.handler, **raw_arguments)
            result = await (gate.guard(call, heavy=spec.heavy) if gate is not None else call)
        except Exception as exc:  # a broken handler must not crash the voice loop
            logger.exception("tool handler %s raised", spec.name)
            result = ToolResult(
                source=spec.name, fetched_at=datetime.now(UTC).isoformat(), data={}, error=str(exc)
            )

        try:
            model = route_model([spec])
            prefs = prefs_getter()
            answer = synthesize_grounded(
                question, [result], prefs, model=model, client=model_client
            )
        except Exception:
            logger.exception("grounded synthesis failed for tool %s", spec.name)
            answer = REFUSAL

        ctx.session.say(answer, allow_interruptions=True)
        raise StopResponse()

    return _dispatch


def livekit_tools(
    registry: ToolRegistry,
    *,
    prefs_getter: Callable[[], list[str]],
    model_client: ModelClient | None = None,
    gate: FillerGate | None = None,
) -> list[RawFunctionTool]:
    """Adapts every ``ToolSpec`` in ``registry`` into a livekit
    ``RawFunctionTool`` ready to hand to ``Agent(tools=...)``.

    ``gate`` (the real T-6 ``FillerGate``) is threaded through to every
    dispatcher unchanged; see ``_make_dispatcher``.
    """
    tools: list[RawFunctionTool] = []
    for spec in registry:
        dispatcher = _make_dispatcher(
            spec, prefs_getter=prefs_getter, model_client=model_client, gate=gate
        )
        tools.append(
            function_tool(
                dispatcher,
                raw_schema={
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": dict(spec.parameters),
                },
            )
        )
    return tools
