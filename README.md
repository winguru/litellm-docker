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
- a starter LiteLLM config for local LM Studio use and Z.AI models

The goal is to give you a working local stack for routing agent traffic to either local or remote models, while also exposing MCP-based tools for memory and vision tasks.

## Quick start

1. Create a local `.env` file with the values you need.
2. Start the stack with Docker Compose.
3. Open the LiteLLM proxy on port `4000`.
4. Connect your app or UI to the proxy as needed.

## Local environment file

Create an `.env` file with values similar to the following:

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

You can also use the provided `stack.env` file as a template for defaults.

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

This stack includes two MCP services that are available to LiteLLM internally:

- `zai-vision-mcp`: connects to the Z.AI vision MCP service and exposes it through a local HTTP endpoint.
- `memory-mcp`: provides a separate memory service for persistent context and memory-based agent workflows.

The relevant section in `config.yaml` looks like this:

```yaml
mcp_servers:
  zai_vision_mcp:
    url: "http://zai-vision-mcp:8000/mcp"
  memory_mcp:
    url: "http://memory-mcp:8000/mcp"
```

This gives LiteLLM access to both:
- vision-capable Z.AI tools
- memory-backed tools for agent sessions and long-lived context

These services stay inside the Docker network and are not intended to be exposed publicly.

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