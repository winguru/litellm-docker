# memory-mcp.Dockerfile file for docker-compose.yaml build
FROM node:22-slim

WORKDIR /app

# Install dependencies locally in the workspace directory
RUN npm install @modelcontextprotocol/server-memory@latest @modelcontextprotocol/sdk@latest express

# Copy the shared stdio->HTTP bridge into the container root directory
COPY server.js .

# Directory holding the persisted knowledge graph
RUN mkdir -p /data
ENV MEMORY_FILE_PATH=/data/memory.json
ENV MCP_COMMAND=node
ENV MCP_ARGS='["./node_modules/@modelcontextprotocol/server-memory/dist/index.js"]'

EXPOSE 8000

CMD ["node", "server.js"]
