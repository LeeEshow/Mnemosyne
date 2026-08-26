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
`config.EMBEDDING_MODEL` resolves to `text-embedding-004` (Google AI Studio) when `GEMINI_API_KEY` is set,
or `text-multilingual-embedding-002` (Vertex AI) otherwise — these produce vectors in different embedding
spaces, so **switching `GEMINI_API_KEY` on or off after any memories already exist invalidates every stored
`embedding` field**: old vectors were computed by the other model and are no longer comparable to new
queries. There is no migration path for this — the fix is wiping and re-adding memories after a switch, not
recomputing embeddings in place. If billing is ever a concern again, deciding whether to touch
`GEMINI_API_KEY` should factor in this cost, not just the Vertex AI dollar amount.
