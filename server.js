const { SSEServerTransport } = require('@modelcontextprotocol/sdk/server/sse.js');
const { StreamableHTTPServerTransport } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { spawn } = require('child_process');
const express = require('express');
const crypto = require('crypto');

const app = express();

// Maps to keep track of active sessions
const sseSessions = new Map();
const streamableSessions = new Map();

// Which stdio MCP server to bridge. Defaults to the Z.AI server for backwards compatibility.
const MCP_COMMAND = process.env.MCP_COMMAND || 'node';
const MCP_ARGS = process.env.MCP_ARGS
  ? JSON.parse(process.env.MCP_ARGS)
  : ['./node_modules/@z_ai/mcp-server/build/index.js'];
const PORT = parseInt(process.env.PORT || '8000', 10);

/**
 * Core bridge logic: Manually starts the transport, spawns the stdio MCP child process,
 * and pipes messages bidirectionally.
 */
async function createBridge(transport, onCloseCallback) {
  // CRITICAL FIX: Manually start the transport since we aren't using McpServer.connect()
  await transport.start();

  const child = spawn(MCP_COMMAND, MCP_ARGS, {
    env: { ...process.env, Z_AI_API_KEY: process.env.ZAI_API_KEY, Z_AI_MODE: 'ZAI' },
    stdio: ['pipe', 'pipe', 'inherit']
  });

  // Client -> MCP server
  transport.onmessage = (msg) => {
    child.stdin.write(JSON.stringify(msg) + '\n');
  };

  // MCP server -> Client
  child.stdout.on('data', (data) => {
    data.toString().split('\n').filter(Boolean).forEach(line => {
      try {
        const response = JSON.parse(line);
        transport.send(response).catch(e => console.error('Failed to send message:', e));
      } catch (e) {
        // Suppress parsing fragments
      }
    });
  });

  // Cleanup hooks
  child.on('close', (code) => {
    console.log(`MCP backend process disconnected with code ${code}`);
    if (transport.close) transport.close().catch(() => {});
  });

  transport.onclose = () => {
    if (!child.killed) child.kill();
    if (onCloseCallback) onCloseCallback();
  };
}

// ==========================================
// 1. SSE ENDPOINTS (Legacy Support)
// ==========================================

app.get('/sse', async (req, res) => {
  console.log('SSE handshake initiated...');
  const transport = new SSEServerTransport('/messages', res);
  sseSessions.set(transport.sessionId, transport);
  
  await createBridge(transport, () => sseSessions.delete(transport.sessionId));
});

app.post('/messages', (req, res) => {
  const sessionId = req.query.sessionId;
  const transport = sseSessions.get(sessionId);
  
  if (transport) {
    transport.handlePostMessage(req, res);
  } else {
    res.status(400).send('No active SSE session found');
  }
});

// ==========================================
// 2. STREAMABLE HTTP ENDPOINT (New Standard)
// ==========================================

const mcpRouter = express.Router();
// Required for Streamable HTTP to parse JSON bodies
mcpRouter.use(express.json());

mcpRouter.post('/', async (req, res) => {
  const sessionId = req.headers['mcp-session-id'];
  let transport;

  if (sessionId && streamableSessions.has(sessionId)) {
    // Existing session
    transport = streamableSessions.get(sessionId);
  } else if (!sessionId && req.body?.method === 'initialize') {
    // New session initialization
    transport = new StreamableHTTPServerTransport({ sessionIdGenerator: () => crypto.randomUUID() });
    
    await createBridge(transport, () => {
      if (transport.sessionId) streamableSessions.delete(transport.sessionId);
    });
  } else {
    // Invalid request
    return res.status(400).json({ error: 'Invalid request: missing session ID or initialize method' });
  }

  // Pass the parsed JSON body to the SDK
  await transport.handleRequest(req, res, req.body);

  // Save the transport to the map AFTER handleRequest generates the sessionId
  if (!sessionId && transport.sessionId) {
    streamableSessions.set(transport.sessionId, transport);
  }
});

// Handle optional GET requests (for server-to-client streaming notifications)
mcpRouter.get('/', async (req, res) => {
  const sessionId = req.headers['mcp-session-id'];
  if (!sessionId || !streamableSessions.has(sessionId)) {
    return res.status(400).json({ error: 'Invalid or missing session ID' });
  }
  const transport = streamableSessions.get(sessionId);
  await transport.handleRequest(req, res);
});

// Handle session deletion (clean shutdown)
mcpRouter.delete('/', async (req, res) => {
  const sessionId = req.headers['mcp-session-id'];
  if (!sessionId || !streamableSessions.has(sessionId)) {
    return res.status(400).json({ error: 'Invalid or missing session ID' });
  }
  const transport = streamableSessions.get(sessionId);
  await transport.handleRequest(req, res);
});

// Mount the streamable router on /mcp
app.use('/mcp', mcpRouter);

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 Dual-Transport MCP Bridge listening on port ${PORT}`);
  console.log('   - SSE endpoints: /sse, /messages');
  console.log('   - Streamable HTTP endpoint: /mcp');
});
