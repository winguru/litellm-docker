# LiteLLM Docker Setup

This project runs a local LiteLLM stack with a few common add-ons for model routing and MCP-based tools. It is designed to be easy to run locally while keeping the setup flexible for future upgrades.

## What this project includes

This repo sets up:
- LiteLLM as the main model router and proxy
- PostgreSQL for the main LiteLLM database
- Redis for caching and auth cache support
- Prometheus for monitoring
- a Z.AI vision MCP server
- a memory MCP server
- a Serena MCP server for semantic code understanding and editing (opt-in, `serena` Compose profile; use local stdio Serena for live code)
- a Context7 MCP server for up-to-date library documentation
- a starter LiteLLM config for local LM Studio use and Z.AI models

The goal is to give you a working local stack for routing agent traffic to either local or remote models, while also exposing MCP-based tools for memory and vision tasks.

## Quick start

1. Create a local `.env` file with the values you need.
2. Start the stack with Docker Compose.
3. Open the LiteLLM proxy on port `4000`.
4. Connect your app or UI to the proxy as needed.

## Local environment file

The committed `stack.env` holds safe defaults, and `.env` (gitignored) holds your real secrets and overrides. `.env` is optional for compose commands — a fresh clone works before you create one — but the stack needs it for real keys.

Create it from the template:

```bash
cp .env.example .env
```

Then fill in the values you need:

```env
# .env file for docker-compose.yaml
LITELLM_MASTER_KEY="some_strong_password"
LITELLM_SALT_KEY="openssl rand -hex 32"
LITELLM_LOG="INFO"
REDIS_PASSWORD="another_strong_password"
LANGFUSE_SECRET_KEY="sk-lf-..."
LANGFUSE_PUBLIC_KEY="pk-lf-..."
LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"
LANGFUSE_OTEL_HOST="https://us.cloud.langfuse.com"
ZAI_API_KEY="..."
VOYAGE_API_KEY="pa-..."
CHUBAI_API_KEY="CHK-..."
NOVELAI_API_KEY="pst-..."
LMSTUDIO_API_KEY="sk-lm-..."
```

Settings in `.env` override the same keys from `stack.env`; anything you leave out keeps its `stack.env` default. See `.env.example` for the full annotated list, including the optional Serena and Context7 variables.

## Langfuse user and session metadata

For better trace quality in Langfuse, send stable identity and session metadata with each request. This is especially useful when multiple users or multiple chat sessions share the same LiteLLM proxy.

The default config already maps a few custom headers into LiteLLM user metadata, including:

- `X-OpenWebUI-User-Id`
- `X-OpenWebUI-User-Email`
- `X-Session-Id`
- `X-Conversation-Id`
- `X-App-Name`

These headers help Langfuse group requests by user, chat thread, and app context instead of treating every model call as an unrelated anonymous event.

A good request pattern looks like this:

```http
X-OpenWebUI-User-Id: alice
X-OpenWebUI-User-Email: alice@example.com
X-Session-Id: session-123
X-Conversation-Id: convo-456
X-App-Name: custom-agent-ui
```

This gives you cleaner traces for:

- user-level visibility in Langfuse
- per-session debugging
- multi-turn conversations across the same app
- better correlation between chat events and tool invocations

If your UI already has a user ID, session ID, or conversation ID, pass it through to LiteLLM instead of relying only on the default API key metadata.

### Example request using the same metadata pattern

```bash
curl "http://localhost:4000/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenWebUI-User-Id: alice" \
  -H "X-OpenWebUI-User-Email: alice@example.com" \
  -H "X-Session-Id: session-123" \
  -H "X-Conversation-Id: convo-456" \
  -H "X-App-Name: my-custom-ui" \
  -d '{
    "model": "zai-org/glm-5.2-coding",
    "messages": [
      {"role": "user", "content": "Help me summarize the session."}
    ]
  }'
```

This is the easiest way to make the Langfuse traces naturally group by user and conversation while still using the normal LiteLLM model routing.

## Included MCP servers

This stack includes four MCP services that are available to LiteLLM internally:

- `zai-vision-mcp`: connects to the Z.AI vision MCP service and exposes it through a local HTTP endpoint.
- `memory-mcp`: provides a separate memory service for persistent context and memory-based agent workflows.
- `serena-mcp`: semantic code navigation, search, reference-finding, and editing tools backed by language servers. Runs the official `ghcr.io/oraios/serena` image with its native streamable-http transport on port `9121`. **Opt-in** — gated behind the `serena` Compose profile (see [Serena: local vs. remote](#serena-local-vs-remote)).
- `context7-mcp`: up-to-date, version-specific library documentation for LLMs. Self-hosted from `@upstash/context7-mcp` with its native HTTP transport on port `8000`.

The relevant section in `config.yaml` looks like this:

```yaml
mcp_servers:
  zai_vision_mcp:
    url: "http://zai-vision-mcp:8000/mcp"
  memory_mcp:
    url: "http://memory-mcp:8000/mcp"
  serena_mcp:
    url: "http://serena-mcp:9121/mcp"
  context7_mcp:
    url: "http://context7-mcp:8000/mcp"
```

### Serena: local vs. remote

Serena's MCP server operates **directly on the filesystem it runs on** — there is no agent or proxy layer that could shuttle code access to a remote instance. That means the right way to run it depends on the state of the code:

| | Local Serena (stdio) | Remote Serena (this stack) |
| --- | --- | --- |
| **Best for** | Live, uncommitted, unpushed code — everyday coding and debugging | Already-pushed repos, analyzed remotely by stack users |
| **Where it runs** | On your machine, launched by your editor/agent as a subprocess | In Docker, behind the `serena` Compose profile |
| **Transport** | stdio (Serena's default) | streamable-http on `:9121/mcp` |
| **Code access** | Direct — any local path, including dirty working trees | Git clones in the `serena_projects` volume (via `SERENA_GIT_REPOS`) |
| **How to start** | See the `uvx` command below | `COMPOSE_PROFILES=serena docker compose up -d` |

The two instances are fully independent and can run at the same time — e.g. a local stdio Serena while you debug, plus the remote one serving pushed repos through LiteLLM.

**Local (default for coding agents).** Run Serena next to your code without installing it:

```bash
uvx -p 3.13 --from git+https://github.com/oraios/serena serena start-mcp-server --project-from-cwd
```

Use `--project /path/to/code` to target a specific directory, or `--project-from-cwd` from the repo root (it auto-detects the nearest `.serena/project.yml` or `.git`). Editors and agents that support MCP stdio servers can launch this command themselves — e.g. in VS Code Copilot / Claude Code / Cursor MCP config:

```json
{
  "servers": {
    "serena": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "-p", "3.13",
        "--from", "git+https://github.com/oraios/serena",
        "serena", "start-mcp-server", "--project-from-cwd"
      ]
    }
  }
}
```

**Remote (opt-in).** The stack's `serena-mcp` service is disabled by default so it is not dead weight in local-debugging mode. Enable it with the `serena` Compose profile:

```bash
COMPOSE_PROFILES=serena docker compose up -d
```

Or uncomment `COMPOSE_PROFILES="serena"` in `stack.env` / `.env` (Portainer reads it from `stack.env` too). Starting the stack without the profile simply leaves `serena-mcp` out; LiteLLM connects to MCP servers lazily, so nothing else breaks.

### Serena notes (remote instance)

Serena is a stateful server: only **one project can be active at a time**, and projects must live inside the container at `/workspaces/projects/<name>` to be accessible.

Because this stack may run on a different machine than your codebase, Serena does not use host bind mounts. Instead, a one-shot `serena-projects-init` job clones your repositories into a Docker volume before the MCP server starts:

1. Set `SERENA_GIT_REPOS` in your `.env`/`stack.env` — space-separated git URLs, optionally pinned to a branch with `<url>|<branch>`:

   ```bash
   SERENA_GIT_REPOS="https://github.com/your-org/repo.git https://github.com/your-org/other.git|develop"
   ```

2. For private repositories, set `SERENA_GIT_TOKEN` to a GitHub personal access token; it is injected into `github.com` clone URLs only.
3. On every `docker compose up`, the init job clones missing repos and fast-forward-updates existing ones (set `SERENA_GIT_RESET="true"` to also discard local edits).
4. Optionally set `SERENA_ACTIVE_PROJECT="repo"` to pre-activate a project at startup; otherwise agents activate one by its in-container path via the `activate_project` tool, e.g. `/workspaces/projects/repo`.
5. `serena/serena_config.yml` (Docker-required settings) seeds a writable config volume on first run, so project registrations persist across restarts.
6. The web dashboard listens on container port `24282`; publish it in `docker-compose.yml` if you want to reach `http://localhost:24282/dashboard`.

Running the stack on the same machine as your code? You can skip git and swap the `serena_projects` volume for a bind mount (`${SERENA_PROJECTS_DIR:-./serena/projects}:/workspaces/projects`) as noted in `docker-compose.yml`.

### Context7 notes

- The MCP server is a thin proxy to Context7's cloud index; self-hosting pins the version but does not make it offline.
- Set `CONTEXT7_API_KEY` in your `.env`/`stack.env` (free key from [context7.com/dashboard](https://context7.com/dashboard)) for higher rate limits. It works anonymously without a key.

This gives LiteLLM access to:

- vision-capable Z.AI tools
- memory-backed tools for agent sessions and long-lived context
- Serena semantic code tools (find symbol, references, editing, and more) — when the `serena` profile is enabled
- Context7 documentation lookup tools (`resolve-library-id`, `get-library-docs`)

These services stay inside the Docker network and are not intended to be exposed publicly.

## Vision proxy hook and failed image processing

Text-only coding models (for example the Z.AI GLM coding routes) cannot ingest `image_url` parts. The custom hook in `custom_hooks/litellm_vision_proxy.py` gives them transparent vision: before the request reaches the model, every image part is sent to the `zai-vision-mcp` sidecar for transcription, and the image part is replaced in place with a `<vision_transcript>` text part. Transcripts are cached by image digest, so conversation history re-sends do not re-run the MCP call.

Image processing never fails the whole request. There are three outcomes:

| Outcome | What the model sees | Cached? |
| --- | --- | --- |
| Transcription succeeds | `<vision_transcript>` block with the description | Yes, by sha256 digest |
| Image rejected by the upstream content filter | `<vision_unavailable>` note: the image was blocked by a content safety filter, the model should tell the user and not guess at the contents | Yes (negative cache, no re-attempt) |
| Any other failure (MCP outage, HTTP 5xx, timeout) | `<vision_unavailable>` note with a plain-language reason (timed out, service unreachable, server error, rate-limited) and a retry hint | No — retried on the next send |

Details worth knowing:

- Content-filter rejections are detected by stable markers in the provider error (Z.AI error code `1301` / `contentFilter`) rather than exact string matching, so minor wording changes upstream will not break detection.
- The `<vision_unavailable>` note intentionally does not use the authoritative-evidence preamble from the success path. A blocked image must not be described as if it were seen, and provider error internals are never leaked into the conversation.
- Blocked images log a single warning line per digest; hard failures log the full traceback for debugging.
- Failure notes carry a user-safe reason category (timeout, service unreachable, server error, rate-limited, request rejected); raw exception details and internal hostnames stay in the proxy logs.
- Coding-agent clients attach an `<attached_files>` placeholder next to each image. Once the image part is processed, that block references a file id that no longer exists, so the hook strips it from the same message. This applies to all three outcomes above, keeping conversation history free of dead tokens.

## LiteLLM configuration overview

The default `config.yaml` is already set up for several useful patterns:

- local LM Studio routing
- Z.AI coding models
- Z.AI multimodal and vision models
- Langfuse telemetry callbacks
- Redis-backed caching and auth cache support

### Local LM Studio support

This config uses `host.docker.internal` so LiteLLM can reach a local LM Studio server running on the host:

```yaml
model_list:
  - model_name: lm_studio/*
    litellm_params:
      model: openai/*
      api_base: http://host.docker.internal:1234/v1
      api_key: os.environ/LMSTUDIO_API_KEY
      mode: completion
    model_info:
      supports_function_calling: true
      supports_tool_choice: true
```

This is useful if you want to run local models from LM Studio while still routing all requests through LiteLLM.

### Z.AI coding support

The config includes models such as `glm-5.2`, `glm-5.1`, and `glm-4.7` with the Z.AI coding API endpoint:

```yaml
  - model_name: zai-org/glm-5.2-coding
    litellm_params:
      model: openai/glm-5.2
      api_base: https://api.z.ai/api/coding/paas/v4
      api_key: os.environ/ZAI_API_KEY
      drop_params: true
      allowed_openai_params: ["tools"]
```

This gives you a simple starter setup for coding-oriented tasks and tool calling through Z.AI models.

### Other Z.AI models

The config also includes multimodal and vision-enabled Z.AI models, such as `glm-5v-turbo`, `glm-4.6v`, and similar variants. These are useful for image-capable and general reasoning workflows.

## Generic embedding aliases

A good pattern for LiteLLM is to keep the model name the app calls stable and let the backend mapping vary by environment. For example, your application can request a generic alias like `text-embedding-small`, while the actual configured backend is either:

- a cloud-backed model such as `openai/text-embedding-3-small`
- a local provider such as the LM Studio wildcard route
- a self-hosted Hugging Face model exposed through an OpenAI-compatible endpoint

This keeps app code simpler and avoids hardcoding a single vendor-specific model name into every integration.

Example patterns in `config.yaml`:

```yaml
- model_name: text-embedding-small
  litellm_params:
    model: openai/text-embedding-3-small
    api_base: https://api.openai.com/v1
    api_key: os.environ/OPENAI_API_KEY
    mode: embedding

- model_name: local-text-embedding-small
  litellm_params:
    model: openai/sentence-transformers/all-MiniLM-L6-v2
    api_base: os.environ/LOCAL_EMBEDDING_BASE_URL
    api_key: os.environ/LOCAL_EMBEDDING_API_KEY
    mode: embedding
```

This is usually better than forcing a single embedding model across all environments. The alias remains stable for the app, while the actual route can be swapped based on the deployment.

### Recommended embedding examples by provider

Use the model that matches the backend you are actually calling:

- OpenAI: `text-embedding-3-small` or `text-embedding-3-large`
- Google: `gemini-embedding-001`
- Voyage: `voyage-3-large` or another supported Voyage embedding model
- Anthropic: use the embedding endpoint or provider route that Anthropic exposes in your deployment
- Local / self-hosted: `sentence-transformers/all-MiniLM-L6-v2`, `BAAI/bge-small-en-v1.5`, or another Hugging Face model exposed through a local OpenAI-compatible server

This gives you a clean app-facing alias without locking the project to one vendor or one embedding model family.

## Optional PGVector support

This repo also includes an optional PGVector setup for future vector-store use cases, such as story ingestion, lore retrieval, and semantic search.

Keep in mind:
- PGVector is optional and not required for the default setup
- it should use a separate database from the main LiteLLM Postgres instance
- the database and API should normally stay internal to the Docker network

The shipped files for this are:
- `docker-compose.pgvector.yml`
- `pgvector-runtime.Dockerfile`
- `docker-entrypoint-initdb.d/init-pgvector.sql`

The init script enables the vector extension automatically:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

This is preferable to manually connecting to the database and running the command by hand.

### Important networking caution

Do not expose the PGVector PostgreSQL service on the host port `5432` unless you explicitly need remote access. The main LiteLLM Postgres instance already uses the same internal port, and publishing both to the host would cause a conflict.

The safer approach is:
- keep the PGVector DB internal-only
- connect to it by service name such as `pgvector-db`
- keep the PGVector API internal-only unless you intentionally want external access

## Enabling PGVector in this project

If you want to turn on the optional PGVector feature, follow these steps.

### 1. Update your environment variables

Uncomment or add the PGVector values in your local `.env` file or in `stack.env`. These values are specifically for the LiteLLM vector-store / embedding side of the stack, not the LM Studio host or any other local agent endpoint.

```env
PGVECTOR_DATABASE_URL="postgresql://llmproxy:dbpassword9090@pgvector-db:5432/litellm_vector?schema=public"
PGVECTOR_SERVER_API_KEY="replace-with-a-long-random-secret"
PGVECTOR_BASE_URL="http://pgvector:8000"
# Recommended pattern: choose an embedding model that matches your provider.
# OpenAI example: text-embedding-3-small
# Google example: gemini-embedding-001
# Voyage example: voyage-3-large
# Local example: sentence-transformers/all-MiniLM-L6-v2
EMBEDDING__MODEL="text-embedding-3-small"
EMBEDDING__BASE_URL="http://litellm:4000"
EMBEDDING__API_KEY="sk-1234"
EMBEDDING__DIMENSIONS="1536"
LOCAL_EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2"
LOCAL_EMBEDDING_BASE_URL="http://host.docker.internal:11434/v1"
LOCAL_EMBEDDING_API_KEY="sk-local"
```

Keep these values separate from the main LiteLLM database settings.

The important point is that there are two different concepts here:

- the LiteLLM model routing configuration in `config.yaml`, including the generic alias names such as `text-embedding-small` and `local-text-embedding-small`
- the LiteLLM embedding settings for the optional PGVector service, which are configured here as `EMBEDDING__*`
- the local LM Studio endpoint, which is configured separately via `LMSTUDIO_HOST` and `LMSTUDIO_API_KEY`

Those are different layers and should not be confused with one another. The `LMSTUDIO_HOST` values are for the local model runner; the `EMBEDDING__*` values are for the vector-store embedding service that LiteLLM uses for retrieval work.

Do not put `api_key` inside `ingest_options.vector_store` when creating a vector store from the LiteLLM Admin UI or API. LiteLLM rejects request-supplied vector-store credentials with an error like `'api_key' cannot be set in ingest_options.vector_store`. The vector-store credential belongs server-side in `config.yaml` under `vector_store_registry[].litellm_params.api_key`, with `api_key: os.environ/PGVECTOR_SERVER_API_KEY`, and the runtime service must receive the same value as `SERVER_API_KEY`.

The important point is that there is no single required embedding model for this stack. For provider-based embeddings, choose a model appropriate to the provider you are using. For local or self-hosted workflows, use a local OpenAI-compatible embedding endpoint or a Hugging Face model exposed through a local server.

### 2. Start the combined stack

Run the main stack plus the optional PGVector override together:

```bash
docker compose -f docker-compose.yml -f docker-compose.pgvector.yml up -d
```

This starts:
- the main LiteLLM stack
- the PGVector database container
- the PGVector runtime service

### 3. Verify the vector extension is enabled

The Postgres container initializes the extension automatically through the startup SQL file:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

You can confirm it is active with:

```bash
docker exec -it litellm_pgvector_db psql -U llmproxy -d litellm_vector -c "SELECT extname FROM pg_extension;"
```

You should see `vector` listed.

### 4. Use the PGVector service internally

The PGVector runtime is meant to be used from inside the Docker network, not via the host. The default internal URL should be:

```text
http://pgvector:8000
```

If you are testing directly from the container host, you can map a temporary host port, but this is not the recommended default configuration.

### 5. Shut it down later

To stop the optional setup later:

```bash
docker compose -f docker-compose.yml -f docker-compose.pgvector.yml down
```

If you want to keep the default stack only, leave the override out and run the normal Compose command without the PGVector file.

## Typical runtime flow

In normal use, the flow is:
1. A UI or app connects to LiteLLM.
2. LiteLLM routes requests to either local LM Studio or Z.AI models.
3. MCP tools are available through the internal MCP services.
4. The memory MCP server handles persistent context, while the Z.AI vision MCP server adds vision-related capabilities.

This makes the stack useful for local model routing, tool use, and basic agent workflows without needing a large production setup.

## Notes for local development

- `host.docker.internal` is used to reach a host-based LM Studio instance from inside Docker.
- MCP services are meant to run on the internal Docker network only.
- The environment variables in `.env` or `stack.env` must be populated before starting the stack.
- The default setup is deliberately conservative: it keeps the main stack simple and adds more advanced tools only when needed.

## Summary

This project is a practical local LiteLLM environment with:
- local model routing through LM Studio
- Z.AI model support
- MCP memory and vision integrations
- an optional PGVector layer for future semantic retrieval needs

It is a good starting point for experimenting with local AI routing, tool use, and MCP-based workflows without overcomplicating the base setup.