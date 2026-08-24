# zai-vision-mcp.Dockerfile file for docker-compose.yaml build
FROM node:22-slim

WORKDIR /app

# Install dependencies locally in the workspace directory 
RUN npm install @z_ai/mcp-server@latest @modelcontextprotocol/sdk@latest express

# Copy your local bridge file into the container root directory
COPY server.js .

EXPOSE 8000

# Execute the local server file using the local package directories
CMD ["node", "server.js"]
