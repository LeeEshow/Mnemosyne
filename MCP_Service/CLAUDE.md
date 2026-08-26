# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Mnemosyne MCP Server — a Python/FastAPI-based MCP (Model Context Protocol) server that gives AI assistants
a long-term memory layer backed by Google Cloud Firestore (vector search) and Vertex AI (embeddings +
Gemini Flash for write-time judgment). It exposes 8 MCP tools (`save_memory`, `search_memories`,
`forget_memory`, `pin_memory`, `unpin_memory`, `load_pinned_memories`, `list_domains`, `register_domain`).

The authoritative design spec is `../Docs/Mnemosyne_MCP_Proposal.md` (one level up, outside this directory).
It is actively maintained as part of a broader project and should be treated as read-only reference from
here — don't edit it as part of work in `MCP_Service/`. **`Task.md`** in this directory is the living
task list/changelog for this service: it tracks what's implemented vs. pending per Proposal section, and
records the reasoning behind non-obvious implementation decisions and code-review fixes. Read it before
starting new work here — it usually has more current context than this file.

## Commands

There is no build step; this is a plain Python package.

```bash
# Environment (Windows; .venv already exists in this directory)
.venv/Scripts/python.exe -m pip install -e .

# Run the server locally
.venv/Scripts/python.exe -m interface.mcp_server
# or: python interface/mcp_server.py  (equivalent, uses `if __name__ == "__main__"`)

# One-time deploy prerequisite: seed the Domain Registry from existing memories
# (must run before restarting the service with domain-registration enforcement live)
.venv/Scripts/python.exe -m scripts.seed_domain_registry
```

Required environment variables (see `config.py`): `MNEMOSYNE_MCP_KEY` (no default — the key-auth
middleware fails closed if unset), `MNEMOSYNE_GOOGLE_CLOUD_PROJECT_ID` (default `mnemosyne-cb868`),
`MNEMOSYNE_GOOGLE_CLOUD_LOCATION` (default `asia-east1`). Note `python-dotenv` is a declared dependency
but nothing in the code calls `load_dotenv()` — env vars must actually be exported in the shell (or
supplied via the systemd `.env` unit on the deploy host), not just placed in a `.env` file locally.
Firestore/Vertex AI access uses Application Default Credentials (no key files) — run
`gcloud auth application-default login` for local dev against the real project.

**No test suite exists in this repo yet.** Verification during development has been done ad hoc: a quick
`ast.parse` sweep over all `.py` files to catch syntax errors, then throwaway scripts (deleted after use,
not committed) that inject fake `repository`/`embedding_provider`/`gate_classifier`/`domain_repository`
objects directly into the `application/*_use_case.py` classes to exercise branches without touching real
Firestore/Vertex AI. If you add real tests, mirror that same fake-object-at-the-port-boundary approach
rather than mocking framework internals.

## Architecture

**Hexagonal Architecture (Ports & Adapters).** Three hard rules, enforced by convention (no linter checks
this):
1. `domain/` must not import any third-party framework/SDK (no FastAPI, Pydantic, firebase-admin,
   google-genai) — standard library only. It's meant to be portable to another language/runtime later.
2. Ports (`domain/ports/*.py`) are `typing.Protocol` definitions, not ABCs.
3. `interface/` (the MCP tool handlers in `mcp_server.py`) only does "parse input → call use case →
   format output" — no business logic there.

Layers: `domain/` (pure logic + Protocol ports) → `application/` (use cases, orchestrate ports, still
framework-free) → `infrastructure/` (Firestore/Vertex AI/Gemini adapters implementing the ports) →
`interface/` (MCP tool registration, Pydantic schemas, ASGI middleware). Dependencies are wired manually
in `interface/mcp_server.py`'s `_dependencies()` (an `lru_cache`d factory), not via a DI framework.

**MCP SDK gotcha — exception handling.** This project uses `mcp.server.mcpserver.MCPServer` (note: this
is *not* the commonly-documented `mcp.server.fastmcp.FastMCP` API; method names and exception types
differ). Inside a tool handler, raising a bare `Exception` gets wrapped by the SDK into
`UnexpectedToolError`, which **discards the original message** and returns only `"Error executing tool
<name>"` to the AI client (full traceback is still logged server-side). To surface a real error message
to the calling AI, you must catch it and re-raise `mcp.server.mcpserver.exceptions.ToolError(str(error))`
in `interface/mcp_server.py` — see how `DomainNotRegisteredError` and the write-gate's `ValueError` are
handled in each tool handler for the pattern to copy for any new exception path.

**Domain Registry.** `domain` is a required string parameter on `save_memory`/`search_memories`/
`load_pinned_memories` (not bound to the connection), normalized via `domain/domain_naming.py`
(`strip().lower()`) and validated against the `domains` Firestore collection (doc ID = the normalized
name itself, so `find_by_name` is an O(1) lookup — always try that before `list_all()`). Two different
failure shapes by design: `save_memory` returns a structured `decision="requires_registration"` (see
`SaveMemoryDecision` in `application/save_memory_use_case.py`), while `search_memories`/
`load_pinned_memories` `raise DomainNotRegisteredError` (caught and converted to `ToolError` at the
interface layer, per the gotcha above) — because those two only return a memory list and have no
"decision" field to carry state in. `interface/mcp_server.py` also overrides `list_tools()` (via the
`_MnemosyneMCPServer` subclass) to inject the live registered-domain list into the `domain` parameter's
description, cached with a TTL (`config.DOMAIN_LIST_CACHE_TTL_SECONDS`) — it deep-copies the schema dict
before mutating it, because `ToolManager` reuses the same schema dict object across calls and mutating it
in place would make the injected text accumulate on every `list_tools` call.

**Write gate / causal memory model** (`domain/write_gate_policy.py` +
`application/save_memory_use_case.py`). Each memory is `premise` (因) + `conclusion` (果), not a single
free-text field. On `save_memory`, candidates are gathered on two tracks in parallel — vector nearest-
neighbor (`WRITE_GATE_CANDIDATE_LIMIT`) and tag-intersection (`array-contains-any`, uncapped by
similarity) — then merged by document ID. **The merge must preserve `is_tag_hit` even when a document is
also found by the vector track** (`GateCandidate.is_tag_hit`): a plain `dict.setdefault`-style merge that
only fills in tag hits when the ID is new will silently lose "this candidate also matched on tags" for a
document that both tracks found, letting a low-vector-similarity/tag-matching candidate wrongly
short-circuit to `ADD` and bypass the LLM conflict check entirely (this exact bug shipped and was caught
in review — see Task.md's code-review notes for the regression test shape). Candidates that don't need
LLM adjudication are decided in `decide_preliminary()` (pure function, no LLM call); otherwise
`GateClassifier.classify()` sends the *whole* candidate list to Gemini Flash in one call, which returns
which one (if any) it matched via `matched_memory_id` plus `NOOP`/`UPDATE`/`SUPERSEDE`/
`CONFLICT_DETECTED`/`ADD`. `SUPERSEDE` and `UPDATE` must apply the LLM's `merged_title`/`merged_premise`/
`merged_conclusion` (a re-summarization, not concatenation) and recompute the embedding from the merged
text before writing — copy `_apply_update`'s shape if you touch this.

**Two decision enums, intentionally split.** `WriteGateDecision` (`domain/write_gate_policy.py`) is the
internal write-gate classification only. `SaveMemoryDecision` (`application/save_memory_use_case.py`) is
the outward-facing result and adds `REQUIRES_REGISTRATION`/`CONFLICT_DETECTED` with deliberately
lowercase `.value`s (`"requires_registration"`, `"conflict_detected"`) to match the wire contract in the
Proposal doc, while the write-gate values stay uppercase (`"NOOP"`, `"ADD"`, ...) — this casing
inconsistency is intentional, not a typo to "fix."

**Python version constraint.** The deploy host (GCE, via `fintarck-backend`) is pinned to Python 3.10.12
and cannot be upgraded (unrelated service depends on that exact version). Don't use stdlib features newer
than 3.10 (e.g. no `datetime.UTC` — use `datetime.timezone.utc`).
