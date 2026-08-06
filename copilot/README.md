# dxb copilot

An LLM agent that answers questions about the Dubai real estate data **and**
reconfigures the UI to illustrate its answers. See
[../docs/UI_PLAN.md](../docs/UI_PLAN.md) §5 for the design.

## Why this is its own service

It needs an outbound model API key and a tool-calling loop, and it reaches data
through the MCP server — so it never needs database access at all. Keeping it
out of `api/` preserves the property that the API only reads the database and
does nothing else. Same reasoning that made `mcp/` separate.

```
ui/  ──SSE──▶  copilot/  ──tools──▶  mcp/  ──HTTP──▶  api/  ──▶  Postgres
```

## Configuration

Required before it can answer anything:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without it the service still boots and serves `/health` (reporting
`"configured": false`), and every chat request returns one clear explanation
rather than a crash loop that looks like a broken deployment.

Also expected:

| Variable | Default | What |
|---|---|---|
| `DXB_COPILOT_MCP_URL` | `http://mcp:8100/mcp` | The MCP server |
| `DXB_MCP_CLIENT_API_KEY` | — | Key this service presents to MCP |
| `DXB_COPILOT_CLIENT_API_KEYS` | — | Keys callers must present to *this* service |
| `DXB_COPILOT_MODEL` | `claude-sonnet-5` | |
| `DXB_COPILOT_MAX_TURNS` | `8` | Hard ceiling on tool round trips per question |

`DXB_COPILOT_CLIENT_API_KEYS` uses the same shape and hashing as the API's
`DXB_API_KEYS` and the MCP server's `DXB_MCP_CLIENT_API_KEYS`, so one
key-generation command works for all three:

```
python -c "import hashlib,secrets; k=secrets.token_urlsafe(32); print('key:',k); print('hash:',hashlib.sha256(k.encode()).hexdigest())"
```

**Auth fails closed.** With no keys configured every request is rejected. This
service spends money per request, so refusing is the only acceptable direction
to be wrong in. `DXB_COPILOT_AUTH_DISABLED=1` exists for local development and
must never be set anywhere else.

## Surface

- `GET /health` — public, static, reports whether a model key is present.
- `POST /chat` — authenticated. Takes `{messages, view_state}` and returns
  Server-Sent Events:

| Event | Payload | Meaning |
|---|---|---|
| `text` | `{text}` | Prose for the user |
| `tool` | `{name, input}` | A data tool is being called (drives a "thinking" indicator) |
| `view_state` | `{patch, explanation}` | Apply this ViewState patch |
| `done` | `{stop_reason}` | Turn finished |
| `error` | `{message}` | Something failed; the message is user-facing |

## Safety properties, and why each exists

- **Data only through MCP.** No SQL, no DB credentials. The model can call
  exactly the seven curated tools and nothing else.
- **UI changes only through a ViewState patch**, applied by the client's own
  reducer and validated by the same zod schema a user's click goes through.
  There is no privileged path, so nothing the copilot does is un-undoable.
- **Tool output is data, not instructions.** The system prompt says so
  explicitly, because tool results contain free-text database fields (property
  and project names) that an attacker could have influenced.
- **Bounded loop.** `max_turns` caps tool round trips; exceeding it returns an
  explanation rather than presenting a partial state as a finished answer.
- **Honesty rules are in the prompt**, not left to chance: always state sample
  size, never present a gross yield as achievable, always carry caveats
  through, never give investment advice.

## Development

```
uv sync --extra dev
uv run pytest -q
uv run ruff format . && uv run ruff check .
```

Tests fake both Anthropic and MCP, so the suite needs no API key and no
network.
