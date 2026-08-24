# litellm-docker
LiteLLM Docker container config

## local .env file
Make sure there is an `.env` file that contains environmental settings like the following:
```
# .env file for docker-compose.yaml
LITELLM_MASTER_KEY="some_strong_password"
LITELLM_SALT_KEY="openssl rand -hex 32"
# LITELLM_LOG="DEBUG"
LITELLM_LOG="INFO"
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