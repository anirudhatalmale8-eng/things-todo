'use strict';

/**
 * Tiny zero-dependency to-do server.
 * Serves the single-page UI from ./public and a small JSON API at /api/todos.
 * Data is persisted to data.json so the list survives a restart.
 */

const http = require('node:http');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const PORT = Number(process.env.PORT) || 3000;
const PUBLIC_DIR = path.join(__dirname, 'public');
const DATA_FILE = process.env.DATA_FILE || path.join(__dirname, 'data.json');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

// ---------------------------------------------------------------- persistence

function loadTodos() {
  try {
    const parsed = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

let todos = loadTodos();
let writeChain = Promise.resolve();

function persist() {
  // Serialise writes so concurrent requests can't interleave and corrupt the file.
  writeChain = writeChain.then(() =>
    fsp.writeFile(DATA_FILE, JSON.stringify(todos, null, 2)).catch((err) => {
      console.error('failed to persist todos:', err.message);
    })
  );
  return writeChain;
}

// -------------------------------------------------------------------- helpers

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function readBody(req, limit = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error('payload too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8').trim();
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error('invalid JSON body'));
      }
    });
    req.on('error', reject);
  });
}

// ------------------------------------------------------------------ API route

async function handleApi(req, res, url) {
  const segments = url.pathname.split('/').filter(Boolean); // ['api','todos',id?]
  const id = segments[2];

  if (!id) {
    if (req.method === 'GET') {
      return sendJson(res, 200, todos);
    }

    if (req.method === 'POST') {
      const body = await readBody(req);
      const title = typeof body.title === 'string' ? body.title.trim() : '';
      if (!title) {
        return sendJson(res, 400, { error: 'title is required' });
      }
      if (title.length > 500) {
        return sendJson(res, 400, { error: 'title must be 500 characters or fewer' });
      }
      const todo = {
        id: crypto.randomUUID(),
        title,
        done: false,
        createdAt: new Date().toISOString(),
      };
      todos.push(todo);
      persist();
      return sendJson(res, 201, todo);
    }

    res.writeHead(405, { Allow: 'GET, POST' });
    return res.end();
  }

  const index = todos.findIndex((t) => t.id === id);
  if (index === -1) {
    return sendJson(res, 404, { error: 'todo not found' });
  }

  if (req.method === 'PATCH') {
    const body = await readBody(req);
    if (typeof body.done === 'boolean') {
      todos[index].done = body.done;
    }
    if (typeof body.title === 'string' && body.title.trim()) {
      todos[index].title = body.title.trim().slice(0, 500);
    }
    persist();
    return sendJson(res, 200, todos[index]);
  }

  if (req.method === 'DELETE') {
    const [removed] = todos.splice(index, 1);
    persist();
    return sendJson(res, 200, removed);
  }

  res.writeHead(405, { Allow: 'PATCH, DELETE' });
  return res.end();
}

// --------------------------------------------------------------- static files

function serveStatic(req, res, url) {
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const filePath = path.join(PUBLIC_DIR, relative);

  // Never serve anything outside public/.
  if (!filePath.startsWith(PUBLIC_DIR + path.sep)) {
    res.writeHead(403).end('Forbidden');
    return;
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not found');
      return;
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream',
      'Content-Length': data.length,
      'Cache-Control': 'no-store',
    });
    res.end(data);
  });
}

// ---------------------------------------------------------------------- server

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  if (url.pathname === '/api/todos' || url.pathname.startsWith('/api/todos/')) {
    handleApi(req, res, url).catch((err) => {
      sendJson(res, 400, { error: err.message });
    });
    return;
  }

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { Allow: 'GET, HEAD' }).end();
    return;
  }

  serveStatic(req, res, url);
});

server.listen(PORT, () => {
  console.log(`todo app listening on http://localhost:${PORT}`);
});
