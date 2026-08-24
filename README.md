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

Uncomment or add the PGVector values in your local `.env` file or in `stack.env`:

```env
PGVECTOR_DATABASE_URL="postgresql://llmproxy:dbpassword9090@pgvector-db:5432/litellm_vector?schema=public"
PGVECTOR_SERVER_API_KEY="replace-with-a-long-random-secret"
EMBEDDING__MODEL="text-embedding-ada-002"
EMBEDDING__BASE_URL="http://litellm:4000"
EMBEDDING__API_KEY="sk-1234"
EMBEDDING__DIMENSIONS="1536"
```

Keep these values separate from the main LiteLLM database settings.

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