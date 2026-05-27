#!/usr/bin/env python3
"""
MCP Memory Server – Persistent conversation history + audit for multi‑agent systems.
Provides tools: add_message, get_conversation, list_threads, get_audit_log.
"""

import sqlite3
import json
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "memory.db"

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, agent_id TEXT NOT NULL, content TEXT NOT NULL, timestamp TIMESTAMP NOT NULL, metadata TEXT, FOREIGN KEY(thread_id) REFERENCES threads(thread_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, action TEXT NOT NULL, thread_id TEXT, details TEXT, timestamp TIMESTAMP NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS summaries (thread_id TEXT PRIMARY KEY, summary_text TEXT NOT NULL, last_updated TIMESTAMP NOT NULL, message_count INTEGER, FOREIGN KEY(thread_id) REFERENCES threads(thread_id))")
    sys.stderr.write("[MCP Memory] Database initialised\n")

def add_message(thread_id, agent_id, content, metadata=None):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO threads (thread_id) VALUES (?)", (thread_id,))
        cursor = conn.execute("INSERT INTO messages (thread_id, agent_id, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                              (thread_id, agent_id, content, datetime.utcnow().isoformat(), json.dumps(metadata) if metadata else None))
        return cursor.lastrowid

def get_conversation(thread_id, limit=None):
    with get_db() as conn:
        query = "SELECT agent_id, content, timestamp, metadata FROM messages WHERE thread_id = ? ORDER BY timestamp ASC"
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query, (thread_id,)).fetchall()
        return [dict(row) for row in rows]

def list_threads(limit=50):
    with get_db() as conn:
        rows = conn.execute("SELECT thread_id FROM threads ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [row["thread_id"] for row in rows]

def add_audit_entry(agent_id, action, thread_id=None, details=None):
    with get_db() as conn:
        conn.execute("INSERT INTO audit_log (agent_id, action, thread_id, details, timestamp) VALUES (?, ?, ?, ?, ?)",
                     (agent_id, action, thread_id, details, datetime.utcnow().isoformat()))

def get_audit_log(agent_id=None, limit=100):
    with get_db() as conn:
        if agent_id:
            rows = conn.execute("SELECT * FROM audit_log WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?", (agent_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

def run_stdio():
    init_db()
    sys.stderr.write("[MCP Memory] Ready on stdio\n")
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            method = req.get("method")
            params = req.get("params", {})
            req_id = req.get("id")

            if method == "tools/list":
                result = {"tools": [
                    {"name": "add_message", "description": "Store a message in a thread", "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "string"}, "agent_id": {"type": "string"}, "content": {"type": "string"}}}},
                    {"name": "get_conversation", "description": "Retrieve thread history", "inputSchema": {"type": "object", "properties": {"thread_id": {"type": "string"}, "limit": {"type": "integer"}}}},
                    {"name": "list_threads", "description": "List all threads", "inputSchema": {"type": "object"}},
                    {"name": "get_audit_log", "description": "Get audit trail", "inputSchema": {"type": "object", "properties": {"agent_id": {"type": "string"}, "limit": {"type": "integer"}}}}
                ]}
                response = {"jsonrpc": "2.0", "id": req_id, "result": result}
            elif method == "tools/call":
                tool = params.get("name")
                args = params.get("arguments", {})
                if tool == "add_message":
                    msg_id = add_message(args["thread_id"], args["agent_id"], args["content"], args.get("metadata"))
                    add_audit_entry(args["agent_id"], "add_message", args["thread_id"], f"msg_id={msg_id}")
                    text = f"Message added to {args['thread_id']}"
                    result = {"content": [{"type": "text", "text": text}]}
                elif tool == "get_conversation":
                    conv = get_conversation(args["thread_id"], args.get("limit"))
                    text = "\n".join(f"[{m['timestamp']}] {m['agent_id']}: {m['content']}" for m in conv) or "No messages"
                    result = {"content": [{"type": "text", "text": text}]}
                elif tool == "list_threads":
                    threads = list_threads(args.get("limit", 50))
                    text = "\n".join(threads) or "No threads"
                    result = {"content": [{"type": "text", "text": text}]}
                elif tool == "get_audit_log":
                    entries = get_audit_log(args.get("agent_id"), args.get("limit", 100))
                    text = "\n".join(f"[{e['timestamp']}] {e['agent_id']} -> {e['action']} (thread:{e['thread_id']}) {e['details']}" for e in entries) or "No audit"
                    result = {"content": [{"type": "text", "text": text}]}
                else:
                    result = {"error": f"Unknown tool {tool}"}
                response = {"jsonrpc": "2.0", "id": req_id, "result": result}
            else:
                response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method {method} not supported"}}

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")

if __name__ == "__main__":
    run_stdio()