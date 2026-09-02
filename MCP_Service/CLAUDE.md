# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Mnemosyne MCP Server — a Python/FastAPI-based MCP (Model Context Protocol) server that gives AI assistants
a long-term memory layer backed by Google Cloud Firestore (vector search) and Gemini (embeddings +
write-time judgment, via a personal Google AI Studio subscription — see "Gemini API" section below for why
this isn't plain Vertex AI). It exposes 7 MCP tools (`save_memory`, `search_memories`, `forget_memory`,
`pin_memory`, `load_pinned_memories`, `list_domains`, `register_domain`).

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

# Retrieval accuracy benchmark against production Firestore (Phase 2.9) — see the harness gotcha
# in the Architecture section before running many cases back-to-back (shared Gemini quota).
.venv/Scripts/python.exe -m scripts.run_retrieval_benchmark
```

Environment variables (see `config.py` / `interface/mcp_server.py` / `infrastructure/firebase_app.py`):
- `MNEMOSYNE_MCP_KEY` — required, no default; the key-auth middleware fails closed if unset.
- `GEMINI_API_KEY` — personal Google AI Studio key; when set, both the embedding provider and the
  write-gate classifier use it instead of Vertex AI (see "Gemini API" section below — this is the actual
  production configuration, not a fallback).
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` — base64-encoded service account key JSON (falls back to raw
  JSON if base64 decoding fails), used by `firebase_app.py` for Firestore access. Takes priority over GCE
  attached identity when present (see "Deployment gotchas" — GCE attached identity has a library
  compatibility bug with Firestore's query API, so this is also the actual production configuration).
- `MNEMOSYNE_GOOGLE_CLOUD_PROJECT_ID` (default `mnemosyne-cb868`), `MNEMOSYNE_GOOGLE_CLOUD_LOCATION`
  (default `asia-east1`, used for Firestore/embedding), `MNEMOSYNE_GEMINI_CLASSIFIER_LOCATION` (default
  `us-central1`, Vertex-mode only — Gemini models aren't available in every Vertex region).
- `MNEMOSYNE_DISABLE_DNS_REBINDING_PROTECTION` / `MNEMOSYNE_ALLOWED_HOSTS` — see "Deployment gotchas."

Note `python-dotenv` is a declared dependency but nothing in the code calls `load_dotenv()` — env vars must
actually be exported in the shell (or supplied via the systemd `.env` unit on the deploy host), not just
placed in a `.env` file locally. For local dev without a service account key, `gcloud auth
application-default login` sets up ADC as a fallback, but expect the Firestore query issue described in
"Deployment gotchas" unless a key file is used.

**No test suite exists in this repo yet.** Verification during development has been done ad hoc: a quick
`ast.parse` sweep over all `.py` files to catch syntax errors, then throwaway scripts (deleted after use,
not committed) that inject fake `repository`/`embedding_provider`/`gate_classifier`/`domain_repository`
objects directly into the `application/*_use_case.py` classes to exercise branches without touching real
Firestore/Vertex AI. If you add real tests, mirror that same fake-object-at-the-port-boundary approach
rather than mocking framework internals.

## Architecture

**Hexagonal Architecture (Ports & Adapters).** Chosen because the tech stack (Python + FastAPI) was picked
for deployment-parity with `NoCode_Project`/`fintarck-backend` (shared GCE host, same deploy pattern), not
because Python has an edge in the MCP ecosystem — this layering keeps the option open to rewrite just the
outer layers in another language/runtime later without touching the business logic. Three hard rules,
enforced by convention (no linter checks this):
1. `domain/` must not import any third-party framework/SDK (no FastAPI, Pydantic, firebase-admin,
   google-genai) — standard library only. It's meant to be portable to another language/runtime later.
2. Ports (`domain/ports/*.py`) are `typing.Protocol` definitions, not ABCs.
3. `interface/` (the MCP tool handlers in `mcp_server.py`) only does "parse input → call use case →
   format output" — no business logic there.

```
MCP_Service/
├── domain/                     # zero framework deps, portable to another language later
│   ├── models.py                # Memory etc. — immutable value objects (frozen dataclass)
│   ├── scoring.py                # decay-ranking formula (Proposal 6.1) — pure function
│   ├── write_gate_policy.py      # write-gate decision rules (Proposal 5.1) — pure function + thresholds
│   └── ports/                    # abstract interfaces (typing.Protocol)
│       ├── memory_repository.py
│       ├── embedding_provider.py
│       └── gate_classifier.py
├── application/                # use-case orchestration layer, injects ports, still framework-free
├── infrastructure/              # concrete adapters, tied to Python/GCP — a future migration only rewrites this layer
│   ├── firestore_memory_repository.py
│   ├── vertex_embedding_provider.py
│   └── gemini_gate_classifier.py
└── interface/                   # MCP/FastAPI entry point, I/O translation only
    ├── mcp_server.py             # tool registration, Streamable HTTP transport
    ├── key_auth_middleware.py    # MNEMOSYNE_MCP_KEY check
    └── tool_schemas.py           # the only layer where Pydantic models live
```

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
"decision" field to carry state in. `domain` parameter descriptions are static strings; the AI discovers
the registered domain list from error responses (which include the list) rather than dynamic injection.

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

**`UPDATE`/`SUPERSEDE` must not silently drop fields the LLM verdict doesn't carry.** The verdict only
ever returns `merged_title`/`merged_premise`/`merged_conclusion` — everything else on the surviving memory
(`tags`, `is_pinned`, `access_count`) has to be carried forward explicitly by `_apply_update`/
`_apply_supersede`, or it reverts to a default and the loss is silent (no error, no log). Two shipped
instances of this (Phase 2.7): (1) `tags` — `_apply_update` overwrites in place and previously ignored
this call's new `tags` entirely, while `_apply_supersede` builds a brand-new document and previously
ignored the *old* memory's `tags` entirely (opposite failure, same root cause). Fixed by a deterministic
union (`tuple(sorted(set(old) | set(new)))`, not LLM-judged — tags are a safety net for tag-intersection
conflict detection and exact-tag search, so over-keeping is the safe direction) via the `_merge_tags()`
helper. (2) `is_pinned`/`access_count` — `_apply_supersede` builds the new `Memory` via `_build_memory()`,
which has no way to know about the old document, so both silently reset to their dataclass defaults
(`is_pinned=False`, `access_count=None`≈0) unless explicitly copied from `candidate.memory` with
`dataclasses.replace()` after `_build_memory()` runs. A pinned or frequently-accessed memory that gets
SUPERSEDEd would otherwise lose that state with no error. Because of this, `_apply_verdict` passes the
whole `GateCandidate` (not just `matched.memory.id`) down to `_apply_update`/`_apply_supersede` — both need
the full old `Memory`, not just its ID. `MemoryRepository.overwrite_content()` takes a `MemoryContentUpdate`
value object (`domain/models.py`) rather than 5 positional params, since adding `tags` to the old signature
would have pushed it past the team's 4-parameter limit. `SaveMemoryResult`/`SaveMemoryResponse` also carry
the resulting `memory`/`merged_memory` (a `MemoryView`) for `ADD`/`UPDATE`/`SUPERSEDE`/`NOOP`, so a caller
can see what a merge actually produced without a follow-up `search_memories` call.

**`premise`/`conclusion` length is enforced in `interface/mcp_server.py`, not via Pydantic
`Field(max_length=...)`.** A `max_length` constraint on the schema gives a vague overflow error with no
character count, and risks some MCP clients validating the JSON Schema `maxLength` as UTF-8 byte count
client-side (which would reject far below 500 for CJK text) before the request ever reaches the server.
`save_memory`'s handler instead calls `_ensure_within_max_length()` — plain `len()` (Unicode codepoints,
so CJK counts as 1 char each) against `config.SAVE_MEMORY_TEXT_MAX_LENGTH` — and raises `ToolError` with
the actual/limit counts. Don't reintroduce `max_length` on those two schema fields.

**`type` is normalized (`.strip().lower()`) at write time** in `SaveMemoryUseCase.execute()`, and matched
case-insensitively (both sides normalized) in `SearchMemoriesUseCase._apply_type_filter`, so old
mixed-case documents stay findable. This is normalization only, not an enum — enumerating the historically
used `type` values first is a deliberately deferred follow-up, not attempted here.

**Tool response payloads are not actually null-stripped, despite Phase 2.6's changelog entry claiming
otherwise.** `save_memory`'s handler returns the `SaveMemoryResponse` Pydantic model directly; the MCP
SDK's own serialization (`func_metadata.py`: `validated.model_dump(mode="json", by_alias=True)` for
structured content, `pydantic_core.to_json(...)` for the text content) does not pass `exclude_none=True`
anywhere in this vendored SDK version, so `None` fields (`registered_domains`, `conflicting_memory`,
`merged_memory`) are serialized as explicit `null`s today. Don't assume the "no null fields" behavior
Task.md's Phase 2.6 section describes is actually in place — verify against `interface/mcp_server.py` and
the SDK before relying on it.

**`UPDATE` overwrites in place but archives a pre-overwrite snapshot first (Phase 2.8).** `_apply_update`
still mutates the active document in place — `doc_id`/`created_at`/`importance_score`/`is_pinned`/
`access_count` never change, matching `SUPERSEDE`'s semantics for those fields — but before calling
`overwrite_content()` it now calls `_archive_pre_update_snapshot(memory)`, which saves a *second* document:
a copy of the candidate's content as it stood immediately before this overwrite, with `id=None` (new doc),
`status=SUPERSEDED`, `superseded_by=memory.id` (pointing at the still-active original). This gives `UPDATE`
an audit trail without adopting `SUPERSEDE`'s forward-pointer/new-`doc_id` model — a full "make `UPDATE` use
`SUPERSEDE`'s storage path" proposal was evaluated and rejected (see Task.md's Phase 2.8 section) because it
would flatten `superseded_by`'s causal-chain semantics (Proposal 2.5/6.4) from a chain into a star (every
snapshot pointing at the same central active ID) and break the invariant that a `SUPERSEDE`-produced `doc_id`'s
content never changes again. If you touch `_apply_update`, keep the snapshot as the *pre*-overwrite state —
it must capture `memory` (the candidate as fetched), not the merged title/premise/conclusion being written.

**`SUPERSEDE` inherits `importance_score` from the old memory unless this call explicitly overrides it
(Phase 2.8 bug fix).** `_apply_supersede` computes
`importance_score = request.importance_score if request.importance_score is not None else old_memory.importance_score`
before building the new document — mirroring the existing `is_pinned`/`access_count` inheritance pattern.
Before this fix, `merged_request` never touched `importance_score`, so it silently fell through to
`_build_memory`'s `request.importance_score or config.DEFAULT_IMPORTANCE_SCORE` fallback (5) any time the
caller's `save_memory` call for a SUPERSEDE didn't explicitly pass one — quietly degrading a memory's
importance on every LLM-triggered correction. Same root cause and shape as the tags/is_pinned/access_count
bugs documented above: fields the LLM verdict doesn't carry must be threaded through explicitly, or they
revert to a default with no error.

**`pin_memory`/`forget_memory` resolve a superseded `memory_id` to its current active one before acting
(Phase 2.8, chain-walk fixed post-review).** Both use cases previously operated directly on the
caller-supplied `memory_id` with no check — if that ID had since been superseded (by a `SUPERSEDE` write, or
now also by an `UPDATE`'s pre-overwrite snapshot), the pin/forget silently landed on the archived, dead
document while the real active memory was untouched. `application/superseded_resolution.py::resolve_active_memory_id()`
(a plain async function, same shape as `application/domain_validation.py::ensure_domain_registered` — no
need for a class since it's not an MCP tool) fetches the memory via the new `MemoryRepository.get_by_id()`
port method and, if its `status == SUPERSEDED`, follows `superseded_by`.

**Must walk the full chain, not just one hop — the first Phase 2.8 implementation shipped a single-hop
version and it was wrong, caught in post-implementation review.** `mark_superseded()` (called from
`_apply_supersede`) only ever updates the one document being directly superseded in that call — it never
revisits earlier ancestors. So a memory superseded twice (`A`'s `superseded_by` set to `B` on the first
`SUPERSEDE`, then `B` itself superseded to `C` on a second `SUPERSEDE`) leaves `A.superseded_by` stale at
`"B"`, and `B` is no longer active. A caller still holding the original `"A"` id — the exact stale-ID
scenario this function exists to fix — would have one-hop resolution land back on superseded `B` instead of
active `C`. The same applies to a `superseded`-snapshot-then-`SUPERSEDE` combination (an `UPDATE` snapshot
whose target subsequently gets superseded). `resolve_active_memory_id()` therefore loops, following
`superseded_by` until it reaches a document that isn't `SUPERSEDED` (or has no `superseded_by`), with a
`visited` set to bail out instead of infinite-looping if a cycle ever exists in the data. `PinMemoryUseCase.execute()`
and `ForgetMemoryUseCase.execute()` both call this before touching the repository.

**Startup fail-fast: embedding model/dimension consistency (Phase 2.9).** Two independent guards, both
raising `domain/exceptions.py::ConfigurationError` (never `assert` — stripped under `python -O`, and must
not live at `config.py` import time since that module is imported by AI-key-agnostic offline scripts like
`scripts/seed_domain_registry.py` and would drag them down too):
1. **Static.** `VertexEmbeddingProvider.__init__` — when `GEMINI_API_KEY` is unset (falls back to Vertex
   AI's `text-multilingual-embedding-002`, fixed 768-dim output, no truncation support), it checks
   `config.EMBEDDING_DIMENSION` actually equals that model's native dimension before constructing the
   client. `GEMINI_API_KEY` is read via `(os.environ.get("GEMINI_API_KEY") or "").strip() or None`, not
   `bool(api_key)` — a whitespace-only env var (`GEMINI_API_KEY=" "`) is truthy under `bool()` and would
   silently skip both this check and the API-key branch, only failing later at the Google API auth step.
2. **Dynamic.** `application/startup_checks.py::verify_stored_embedding_dimension()` samples one *active*
   memory (`MemoryRepository.sample_one()` — filtered to `status=="active"`; `limit(1)` with no `order_by`
   means Firestore can return any document, and an unfiltered sample could land on a stale/malformed
   snapshot and `KeyError` in `_to_memory()` instead of raising the intended `ConfigurationError`) and
   compares its stored `len(embedding)` against `config.EMBEDDING_DIMENSION`, catching the case where the
   *index* was built at one dimension but the currently-configured model would produce another. Wired via
   `mcp_server.py`'s `_lifespan` async context manager, passed as `MCPServer(..., lifespan=_lifespan)` —
   not the `@lru_cache`d `_dependencies()` factory, which only runs lazily on the first tool call. Tracing
   the vendored MCP SDK confirms `streamable_http_app()` threads this `lifespan` into Starlette's ASGI
   `lifespan` via `session_manager.run()`, so it runs exactly once at process startup; a raised
   `ConfigurationError` here aborts uvicorn startup outright ("Application startup failed. Exiting.") rather
   than surfacing as a confusing failure on the first real tool call.

**`search_memories` has an internal-only `record_access` opt-out, and a retrieval benchmark harness now
exists (Phase 2.9).** `SearchMemoriesRequest.record_access: bool = True` — when `False`, `execute()` skips
the `_record_access(top)` write that increments hit memories' `access_count` (which feeds the decay
formula's `access_frequency` term, 6.1 in the Proposal doc). The MCP tool handler in `interface/mcp_server.py`
never passes this field, so the real `search_memories` tool call always keeps the default `True` — this
exists purely so `scripts/run_retrieval_benchmark.py` can repeatedly query production Firestore without
each run inflating the very scores it's trying to measure (a benchmark that mutates the data it evaluates
against isn't reproducible run-to-run). The harness reads cases from `scripts/retrieval_benchmark_cases.py`
(a `.py` module, not `.json` — the repo-root `.gitignore` blanket-blocks `*.json` to keep credential files
out of version control, and a `.json` case file would be silently git-ignored too), each a `domain`/`query`/
`expected_order` (doc IDs in expected rank order — a single element only checks recall, multiple also check
relative ordering) tuple plus optional `type`/`exact_tags`/`limit`. Run via
`python -m scripts.run_retrieval_benchmark` from `MCP_Service/`. Case `limit` defaults to
`config.SEARCH_MEMORIES_DEFAULT_LIMIT` (not some more generous number) — a case that only recalls under a
looser limit doesn't prove the real tool (which uses the production default) would surface it. See the
Gemini quota gotcha below before running many cases back-to-back.

**Two decision enums, intentionally split.** `WriteGateDecision` (`domain/write_gate_policy.py`) is the
internal write-gate classification only. `SaveMemoryDecision` (`application/save_memory_use_case.py`) is
the outward-facing result and adds `REQUIRES_REGISTRATION`/`CONFLICT_DETECTED` with deliberately
lowercase `.value`s (`"requires_registration"`, `"conflict_detected"`) to match the wire contract in the
Proposal doc, while the write-gate values stay uppercase (`"NOOP"`, `"ADD"`, ...) — this casing
inconsistency is intentional, not a typo to "fix."

**Python version constraint.** The deploy host (GCE, via `fintarck-backend`) is pinned to Python 3.10.12
and cannot be upgraded (unrelated service depends on that exact version). Don't use stdlib features newer
than 3.10 (e.g. no `datetime.UTC` — use `datetime.timezone.utc`).

## Deployment gotchas (learned the hard way during Phase 2.5)

These five issues stacked on top of each other during the first real deploy to GCE — none of them were
config mistakes on our end, each was a genuine environment/library trap. Fix order matters less than
knowing all five exist, since fixing one just uncovers the next.

**`google-api-core==2.35.0` breaks all Firestore queries.** That specific version has a regression that
mis-encodes the default database id `(default)` into gRPC requests, so every query (even a plain
`.collection().get()`) fails with `400 Invalid database id %28default%29` — happens identically whether
using GCE-attached identity or a service account key file, so don't waste time suspecting credentials.
The version was pulled from PyPI shortly after release (doesn't show up in `pip index versions` anymore,
but a venv that installed it before the pull keeps it). `pyproject.toml` pins `google-api-core==2.34.0`
explicitly — don't remove this pin without confirming a newer release actually fixes the regression.

**GCE VM access scopes are a separate gate from IAM roles.** Granting `Cloud Datastore User` in IAM is not
enough — the GCE instance's own OAuth access scopes (set at VM creation, `gcloud compute instances
describe --format="value(serviceAccounts.scopes)"`) must also include `cloud-platform` (or the specific
Firestore scope), otherwise every Firestore call fails with `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT` before
IAM is even checked. Changing scopes requires stopping and restarting the whole VM
(`gcloud compute instances stop/set-service-account/start`) — this is shared with `fintarck-backend`
(NoCode_Project), so it causes a brief outage of that service too; confirm before doing it.

**`firebase_admin.initialize_app()` needs an explicit `projectId` when the app runs cross-project.**
Without it, the SDK falls back to the ambient project from GCE metadata credentials — which on this shared
host is the GCE host's own project, not `mnemosyne-cb868`. `infrastructure/firebase_app.py` passes
`options={"projectId": config.GOOGLE_CLOUD_PROJECT_ID}` explicitly; don't drop this when touching that file.

**MCP SDK's `sse_app()` has DNS rebinding protection on by default, and it blocks the Cloud Run proxy
path entirely.** The SDK only accepts requests whose `Host` header is `localhost`/`127.0.0.1`/`::1`. Since
`fintarck-proxy`'s Nginx forwards the real external Host header (`fintarck-proxy-*.run.app`), every request
routed through the proxy gets rejected with `421` — and confusingly, this only shows up *after* the key
passes `KeyAuthMiddleware` (a wrong key returns `401` from our own middleware before ever reaching the
SDK's check, which made this look like it was somehow key-value-dependent during debugging — it isn't).
Fix: set `MNEMOSYNE_DISABLE_DNS_REBINDING_PROTECTION=true` in `.env` (safe here since `MNEMOSYNE_MCP_KEY`
already gates access), or `MNEMOSYNE_ALLOWED_HOSTS=<comma-separated hosts>` to keep the protection but
allowlist the proxy's hostname. Both are read in `interface/mcp_server.py`'s `_get_transport_security()`.

**MCP transport: use `streamable_http_app()`, not `sse_app()`.** The classic SSE transport (`sse_app()`,
two endpoints: GET `/sse` to open the stream + POST `/messages/?session_id=...` for follow-up messages)
breaks behind a path-prefixed reverse proxy: the server emits the POST endpoint as a path relative to its
own mount root, which the client resolves relative to the *origin*, not to `/mnemosyne/` — so the POST
lands on Nginx's catch-all `location /` (the NoCode backend) instead of us, and NoCode's app returns `405`
for a path it doesn't recognize. Modern MCP clients (Claude Desktop's connector UI) default to expecting
the newer Streamable HTTP transport anyway and treat an unexpected `405` from an SSE-only server as "the
server wants auth," which is a red herring that wastes time debugging OAuth/key settings that were never
the problem. `streamable_http_app()` uses a single endpoint (`/mcp` by default) for both directions, has no
relative-URL redirect step, and takes the same `transport_security` kwarg as `sse_app()` did — swap one
for the other in the final `app = key_auth_middleware.KeyAuthMiddleware(mcp_server.streamable_http_app(...))`
assembly, nothing else changes.

**Nginx path routing for `/mnemosyne/`**: the backend's real route is `/mcp` (both GET and POST, Streamable
HTTP transport — see above). The `location /mnemosyne/` block's `proxy_pass` **must** end with a trailing slash
(`proxy_pass http://<GCE_IP>:8001/;`) so Nginx strips the `/mnemosyne/` prefix before forwarding —
without it the backend receives `/mnemosyne/sse` verbatim and 404s. Don't copy the WebSocket-style
`proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` pair into this block
unconditionally — SSE isn't a WebSocket upgrade, and forcing a literal `Connection: upgrade` on a request
that has no matching `Upgrade` header is malformed; use `proxy_set_header Connection ""; proxy_buffering
off;` instead. This config lives in the `fintarck-proxy` Cloud Run service's own repo/source, not in this
one — redeploy via `gcloud run deploy fintarck-proxy --source . --region asia-east1
--allow-unauthenticated --port 8080` from that source directory after editing `nginx.conf`.

## Gemini API: personal API key vs Vertex AI

Both `infrastructure/gemini_gate_classifier.py` and `infrastructure/vertex_embedding_provider.py` check
`os.environ.get("GEMINI_API_KEY")` first — if set, they construct `genai.Client(api_key=...)` against
Google AI Studio (billed against the personal subscription tied to that key) instead of
`genai.Client(vertexai=True, project=..., location=...)` (billed against `mnemosyne-cb868`'s GCP billing
account). This was a deliberate switch to move AI usage cost off GCP billing and onto an existing personal
subscription — not a fallback/dev-mode toggle, so don't "clean it up" by removing the Vertex branch.

**Embedding model is tied to which client mode is active and the two are not interchangeable.**
`config.EMBEDDING_MODEL` resolves to `gemini-embedding-001` (Google AI Studio — the older `text-embedding-004`
name doesn't exist on this API surface; `client.models.list()` filtered to `embedContent` support is the way
to check what's actually available) when `GEMINI_API_KEY` is set, or `text-multilingual-embedding-002`
(Vertex AI, 768 dimensions) otherwise. `gemini-embedding-001` natively outputs **3072** dimensions, but
**Firestore's vector index has a hard cap of 2048 dimensions** — exceeding it fails index creation with
`INVALID_ARGUMENT: Invalid dimension ... must be ... less than or equal to 2048`. `VertexEmbeddingProvider`
requests a Matryoshka-truncated `output_dimensionality=config.EMBEDDING_DIMENSION` (1536) via
`types.EmbedContentConfig` when using the API-key client, and the Firestore composite index (`memories`
collection, `domain (ASC) + status (ASC) + embedding (Vector)`) was recreated at `dimension: 1536` to match.
**This means the Vertex AI fallback branch is currently non-functional**: `text-multilingual-embedding-002`
only outputs 768 dimensions (no truncation support), which no longer matches the 1536-dim index. It's kept
in the code for structural symmetry and as a starting point if this ever needs reworking, but don't expect
the "unset `GEMINI_API_KEY`" path to actually work without also reverting the index (delete the current one,
`gcloud firestore indexes composite create` with `dimension: "768"`) or finding a Vertex model/config that
supports matching output dimensionality.

**`config.GATE_CLASSIFIER_MODEL` is also split the same way**: `gemini-2.5-flash` returns
`404 "This model ... is no longer available to new users"` on the Google AI Studio Developer API (that
model was retired from new-account access there; Vertex AI's enterprise lifecycle is separate and still
serves it), so the API-key branch uses `gemini-3.6-flash` instead. If a similar model-retirement error shows
up again, check `client.models.list()` for what's actually still servable before assuming it's a code bug.

Switching `GEMINI_API_KEY` on/off, or changing which embedding model is active, invalidates every stored
`embedding` field — old vectors were computed by a different model/dimensionality and aren't comparable to
new queries. There is no in-place migration for this; the fix is wiping and re-adding memories after a
switch, not recomputing embeddings. Given the above, in practice this project is currently committed to the
Google AI Studio path — treat that as the standing assumption, not a togglable option.

**The personal Google AI Studio key has a shared per-minute embedding quota, and it *will* get exhausted
under concurrent testing (first hit 2026-09-01/02, during Phase 2.9 development).** Both `save_memory` and
`search_memories` call `VertexEmbeddingProvider.embed()`, which hits
`generativelanguage.googleapis.com/.../gemini-embedding-001:batchEmbedContents`. That endpoint's quota
(`aiplatform.googleapis.com/global_embed_content_requests_per_minute_per_base_model` — note the
`aiplatform.googleapis.com` quota namespace even though the actual call goes through the AI-Studio-key
client, not Vertex; that's just how Google buckets this quota internally) is shared across *every* caller
using this one key — multiple Claude sessions (PM, SE, or anyone else) testing at the same time can burn
through it within seconds. When exhausted, `google.genai.errors.ClientError` (HTTP 429
`RESOURCE_EXHAUSTED`) propagates up through `search_memories`'s handler as a bare exception, and per the
MCP SDK gotcha above, the AI client only ever sees the generic `"Error executing tool search_memories"` —
the real 429 is visible only in `sudo journalctl -u mnemosyne.service` server-side.
**This is not a fixed/broken binary state — it's live contention.** Requests made seconds apart in the same
testing burst can alternate between success and 429 depending on exactly when each one lands relative to
the rolling per-minute window; don't conclude "it's fixed" from one successful retry, or "it's still broken"
from one more failure — check a short, recent log window (`--since '5 min ago'`) to see the actual pattern
before drawing a conclusion. `scripts/run_retrieval_benchmark.py` throttles between cases
(`_CASE_THROTTLE_SECONDS = 3.0`) and classifies 429s as `CaseStatus.QUOTA_EXCEEDED` (separate from a real
`FAIL`) for exactly this reason — see the harness gotcha above. No code-level fix exists yet for the
interactive `save_memory`/`search_memories` tool paths themselves; options noted but not yet acted on:
request a quota increase from Google (the 429 error body includes the request link), or add longer 429-
specific backoff to the `tenacity` retry already wrapping `google-genai` calls.
