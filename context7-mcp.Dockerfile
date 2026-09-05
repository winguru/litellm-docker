# context7-mcp.Dockerfile file for docker-compose.yaml build
#
# Self-hosted Context7 MCP server (https://github.com/upstash/context7).
# Runs the npm package with its native HTTP (streamable) transport so no
# stdio->HTTP bridge (server.js) is needed for this service.
FROM node:22-slim

WORKDIR /app

# Pin the package version instead of @latest so builds are reproducible.
RUN npm install @upstash/context7-mcp@4.0.4

# Optional: set at runtime via compose environment. When unset, the server
# runs anonymously against the public Context7 index with lower rate limits.
# ENV CONTEXT7_API_KEY=...

ENV NODE_ENV=production

EXPOSE 8000

# The npm package accepts --transport http (a.k.a. http-streamable) and --port.
CMD ["node", "node_modules/@upstash/context7-mcp/dist/index.js", "--transport", "http", "--port", "8000"]
