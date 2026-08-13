"""Jarvis — real-time grounded voice assistant.

Package layout is pinned in docs/DESIGN.md §Architecture:

    pipeline/    LiveKit Agents worker: STT -> LLM -> TTS      (T-1)
    server/      FastAPI app: /api/token, health, static SPA   (T-1, T-10)
    llm/         Model routing, ANSWER_POLICY, synthesis        (T-2)
    tools/       ToolResult-producing tools + registry          (T-2..T-4)
    memory/      MemoryStore on mem0 + pgvector                 (T-5)
    cadence/     Fillers, barge-in, stop phrases                (T-6)
    auth/        Clerk JWT verification                         (T-10)
    agents_pr/   Background issue -> PR job runner              (T-11)
"""

__version__ = "0.1.0"
