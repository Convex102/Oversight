#!/usr/bin/env python3
"""
Oversight Labs — CrewAI Orchestrator v6.0 (Hybrid HDC+FEP+CogState)
==========================================
Personal LLM + MCP Orchestrator + Oversight A/P Integration

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────┐
  │  POST /dispatch  ←── intelligent router (classifies prompt) │
  │  POST /run/<crew> ←── direct crew invocation                │
  │  GET  /health    ←── full ecosystem status                  │
  └─────────────────────────────────────────────────────────────┘
        ↓ tiered LLM routing
  T1: DeepSeek R1 / Claude      (audit, oversight, risk)
  T2: DeepSeek V3 / Gemini Flash (coding, research, SEO)
  T3: Llama 3.3 70B free        (triage, journal, summaries)

COMPRESSION STACK (60-70% token reduction):
  TokenJuice → ContextPilot → LLMLingua-2 (opt-in)

MEMORY STACK:
  kioku-lite (BM25+vector+graph) → ChromaDB fallback → Mem0 (deduped facts)

MCP TOOLS (all self-hosted, all free):
  SearXNG      web search (docker :8080)
  Crawl4AI     web scraping (docker :11235)
  Playwright   browser automation (npm :3333)
  Mem0         LLM-deduped memory (docker :8765)
  Context7     live library docs (Upstash-hosted)
  CodeGraph    semantic code graph (npm :6820)
  ScreamingFrog SEO crawl (:6800)
  ASGL-A tools  FRS102 audit (:8000)

FIXES from v4.4:
  FIX-1: _TJ_AVAILABLE was referenced but never assigned — NameError at startup
  FIX-2: LLM_T3_BULK.call() — crewai.LLM has no .call(); replaced with direct
         litellm completion call
  FIX-3: Mem0 endpoints corrected to /v1/memories and /v1/memories/search
  FIX-4: Version strings updated throughout
  FIX-5: /dispatch intelligent router added
  FIX-6: CREW_TIERS moved before it is referenced
  FIX-7: Prompt caching header added to Anthropic/OpenRouter calls
  FIX-8: Service health checks added for all Docker-dependent tools
  FIX-9: launch_ecosystem.py Network-AI and Playwright processes added inline

API KEYS (.env file in same directory):
  DEEPSEEK_API_KEY   = sk-...
  GEMINI_API_KEY     = AIza...
  OPENROUTER_API_KEY = sk-or-...
  ANTHROPIC_API_KEY  = sk-ant-... (optional)
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re as _re
import subprocess as _sp
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import requests as _req
from crewai import Agent, Crew, LLM, Process, Task
from crewai.tools import BaseTool
from flask import Flask, Response, jsonify, request
from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════════════════
# STARTUP: paths, logging, .env
# ══════════════════════════════════════════════════════════════════════════════
_base    = Path(__file__).parent
_log_dir = _base / "logs"
_log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][Orchestrator][%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(_log_dir / "Orchestrator.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
LOG = logging.getLogger("Orchestrator")


def _load_env() -> None:
    env_file = _base / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v

_load_env()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
FREELLM_PORT   = os.environ.get("FREELLMAPI_PORT",       "3001")
FREELLM_BASE   = f"http://localhost:{FREELLM_PORT}/v1"
FREELLM_KEY    = os.environ.get("FREELLMAPI_KEY",        "freellmapi")
DASHBOARD_PORT = os.environ.get("DASHBOARD_PORT",        "5001")
ASGL_BASE_URL  = os.environ.get("ASGL_BASE_URL",         "http://localhost:8000/api/v1")
CREWAI_PORT    = int(os.environ.get("CREWAI_PORT",        8001))
SF_MCP_PORT    = os.environ.get("SF_MCP_PORT",            "6800")
CG_REST_PORT   = os.environ.get("CODEGRAPH_REST_PORT",    "6820")
_SEARXNG_URL   = os.environ.get("SEARXNG_URL",            "http://localhost:8080")
_CRAWL4AI_URL  = os.environ.get("CRAWL4AI_URL",           "http://localhost:11235")
_PW_PORT       = os.environ.get("PLAYWRIGHT_MCP_PORT",    "3333")
_PW_BASE       = f"http://localhost:{_PW_PORT}"
_MEM0_URL      = os.environ.get("MEM0_URL",               "http://localhost:8765")
_NAI_PORT      = os.environ.get("NETWORK_AI_PORT",        "7001")
_NAI_BASE      = f"http://localhost:{_NAI_PORT}"
_KIOKU_DATA    = os.environ.get("KIOKU_DATA_DIR",         str(_base / ".kioku"))
_CHROMA_DIR    = str(_base / "data" / "chroma_db")
_LINGUA_RATIO  = float(os.environ.get("LLMLINGUA_RATIO",  "0.5"))

DEEPSEEK_KEY   = os.environ.get("DEEPSEEK_API_KEY",   "")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY",     "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY",  "")

_keys = {
    "DeepSeek": bool(DEEPSEEK_KEY), "Gemini": bool(GEMINI_KEY),
    "OpenRouter": bool(OPENROUTER_KEY), "Anthropic": bool(ANTHROPIC_KEY),
    "FreeLLMAPI": True,
}
LOG.info("Keys: %s", " | ".join(f"{k}={'✓' if v else '✗'}" for k, v in _keys.items()))


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE HEALTH CHECKS
# ══════════════════════════════════════════════════════════════════════════════
def _svc_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        return _req.get(url, timeout=timeout).status_code < 500
    except Exception:
        return False

def _svc_status() -> Dict[str, bool]:
    # FIX-1: _BB_BASE/_BB_AVAILABLE defined later — use globals() lookup to avoid NameError
    _bb_base      = globals().get("_BB_BASE",      "http://localhost:3334")
    _bb_available = globals().get("_BB_AVAILABLE", False)
    return {
        "searxng":     _svc_ok(f"{_SEARXNG_URL}/search?q=test&format=json"),
        "crawl4ai":    _svc_ok(f"{_CRAWL4AI_URL}/health"),
        "playwright":  _svc_ok(f"{_PW_BASE}/health"),
        "mem0":        _svc_ok(f"{_MEM0_URL}/health"),
        "network_ai":  _svc_ok(f"{_NAI_BASE}/health"),
        "asgl_a":      _svc_ok(f"{ASGL_BASE_URL.rstrip('/api/v1')}/health"),
        "freellmapi":  _svc_ok(f"{FREELLM_BASE}/models"),
        "codegraph":   _svc_ok(f"http://localhost:{CG_REST_PORT}/api/status"),
        "browserbase": _svc_ok(f"{_bb_base}/health") if _bb_available else False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TIERED LLM FACTORY
# Uses litellm openai-compat — no crewai native providers needed.
# FIX-7: prompt caching headers added for Anthropic
# ══════════════════════════════════════════════════════════════════════════════
def _llm(model: str, base_url: str, api_key: str,
         temperature: float = 0.1, max_tokens: int = 4096,
         extra_headers: Optional[Dict] = None) -> LLM:
    kwargs: Dict[str, Any] = dict(
        model=model, base_url=base_url, api_key=api_key,
        temperature=temperature, max_tokens=max_tokens,
    )
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    return LLM(**kwargs)

def tier1_reasoning(temperature: float = 0.05, max_tokens: int = 8192) -> LLM:
    """T1: DeepSeek R1 → Claude Sonnet → OpenRouter R1 → FreeLLMAPI"""
    if DEEPSEEK_KEY:
        return _llm("deepseek-reasoner", "https://api.deepseek.com/v1",
                    DEEPSEEK_KEY, temperature, max_tokens)
    if ANTHROPIC_KEY:
        # FIX-7: prompt caching reduces costs 50-90% on repeated system prompts
        return _llm("claude-sonnet-4-20250514", "https://api.anthropic.com/v1",
                    ANTHROPIC_KEY, temperature, max_tokens,
                    extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"})
    if OPENROUTER_KEY:
        return _llm("deepseek/deepseek-r1", "https://openrouter.ai/api/v1",
                    OPENROUTER_KEY, temperature, max_tokens)
    return _llm("gpt-4o", FREELLM_BASE, FREELLM_KEY, temperature, max_tokens)

def tier2_coding(temperature: float = 0.1, max_tokens: int = 8192) -> LLM:
    """T2: DeepSeek V3 (#1 LiveCodeBench) → OpenRouter → FreeLLMAPI"""
    if DEEPSEEK_KEY:
        return _llm("deepseek-chat", "https://api.deepseek.com/v1",
                    DEEPSEEK_KEY, temperature, max_tokens)
    if OPENROUTER_KEY:
        return _llm("deepseek/deepseek-chat-v3-0324", "https://openrouter.ai/api/v1",
                    OPENROUTER_KEY, temperature, max_tokens)
    return _llm("gpt-4o", FREELLM_BASE, FREELLM_KEY, temperature, max_tokens)

def tier2_research(temperature: float = 0.2, max_tokens: int = 8192) -> LLM:
    """T2: Gemini 2.5 Flash (1M ctx, 1500/day free) → OpenRouter → DeepSeek → FreeLLMAPI"""
    if GEMINI_KEY:
        return _llm("gemini/gemini-2.5-flash-preview-05-20",
                    "https://generativelanguage.googleapis.com/v1beta/openai/",
                    GEMINI_KEY, temperature, max_tokens)
    if OPENROUTER_KEY:
        return _llm("google/gemini-2.5-flash-preview:free",
                    "https://openrouter.ai/api/v1",
                    OPENROUTER_KEY, temperature, max_tokens)
    if DEEPSEEK_KEY:
        return _llm("deepseek-chat", "https://api.deepseek.com/v1",
                    DEEPSEEK_KEY, temperature, max_tokens)
    return _llm("gpt-4o", FREELLM_BASE, FREELLM_KEY, temperature, max_tokens)

def tier3_bulk(temperature: float = 0.0, max_tokens: int = 2048) -> LLM:
    """T3: Llama 3.3 70B free (OpenRouter) → Gemini → DeepSeek → FreeLLMAPI"""
    if OPENROUTER_KEY:
        return _llm("meta-llama/llama-3.3-70b-instruct:free",
                    "https://openrouter.ai/api/v1",
                    OPENROUTER_KEY, temperature, max_tokens)
    if GEMINI_KEY:
        return _llm("gemini/gemini-2.5-flash-preview-05-20",
                    "https://generativelanguage.googleapis.com/v1beta/openai/",
                    GEMINI_KEY, temperature, max_tokens)
    if DEEPSEEK_KEY:
        return _llm("deepseek-chat", "https://api.deepseek.com/v1",
                    DEEPSEEK_KEY, temperature, max_tokens)
    return _llm("gpt-4o", FREELLM_BASE, FREELLM_KEY, temperature, max_tokens)

LLM_T1_REASONING = tier1_reasoning()
LLM_T2_CODING    = tier2_coding()
LLM_T2_RESEARCH  = tier2_research()
LLM_T3_BULK      = tier3_bulk()
LOG.info("LLMs: T1=%s T2c=%s T2r=%s T3=%s",
         "r1" if DEEPSEEK_KEY else "claude" if ANTHROPIC_KEY else "or" if OPENROUTER_KEY else "free",
         "v3" if DEEPSEEK_KEY else "or" if OPENROUTER_KEY else "free",
         "gemini" if GEMINI_KEY else "or" if OPENROUTER_KEY else "free",
         "llama-free" if OPENROUTER_KEY else "gemini" if GEMINI_KEY else "free")


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTPILOT — Context dedup + KV-cache prefix reuse (MLSys 2026)
# pip install contextpilot
# ══════════════════════════════════════════════════════════════════════════════
_CP_ENABLED = os.environ.get("CONTEXTPILOT_ENABLED", "1") == "1"
try:
    from contextpilot import ContextPilot as _ContextPilot
    _cp = _ContextPilot()
    _CP_AVAILABLE = True
    LOG.info("ContextPilot: active")
except ImportError:
    _CP_AVAILABLE = False; _cp = None

def cp_compress_messages(messages: List[Dict]) -> List[Dict]:
    if not _CP_AVAILABLE or not _CP_ENABLED or not _cp or not messages:
        return messages
    try:
        return _cp.compress(messages)
    except Exception:
        return messages


# ══════════════════════════════════════════════════════════════════════════════
# LLMLINGUA-2 — Token-level prompt compression (Microsoft Research)
# pip install llmlingua   |   Set LLMLINGUA_ENABLED=1 in .env to activate
# ══════════════════════════════════════════════════════════════════════════════
_LINGUA_ENABLED = os.environ.get("LLMLINGUA_ENABLED", "0") == "1"
try:
    if _LINGUA_ENABLED:
        from llmlingua import PromptCompressor as _PromptCompressor
        _lingua = _PromptCompressor(
            model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
            use_llmlingua2=True, device_map="cpu",
        )
        _LINGUA_AVAILABLE = True
        LOG.info("LLMLingua-2: active at %.0f%% ratio", _LINGUA_RATIO * 100)
    else:
        raise ImportError("disabled")
except (ImportError, Exception):
    _LINGUA_AVAILABLE = False; _lingua = None

def lingua_compress(text: str, ratio: Optional[float] = None) -> str:
    if not _LINGUA_AVAILABLE or not _lingua or len(text) < 500:
        return text
    try:
        r = ratio or _LINGUA_RATIO
        result = _lingua.compress_prompt(text, rate=r, force_tokens=["\n", ".", "!", "?"])
        return result.get("compressed_prompt", text)
    except Exception:
        return text


# ══════════════════════════════════════════════════════════════════════════════
# HEADROOM — Context compression layer  (pip install "headroom-ai[all]")
# Stage 3 in the 4-stage pipeline:
#   Stage 1  TokenJuice    — strip HTML noise, truncate verbose tool outputs
#   Stage 2  ContextPilot  — dedup overlapping blocks, KV-cache prefix reuse
#   Stage 3  Headroom      — ContentRouter selects per content type:
#                              SmartCrusher   (JSON / structured data)
#                              CodeCompressor (AST-level, code files)
#                              Kompress-base  (prose, briefings, RAG chunks)
#                            + CacheAligner stabilises prefixes for KV hits
#                            + CCR makes compression reversible on demand
#   Stage 4  LLMLingua-2   — token-level neural compression (opt-in)
#
# Measured on real agent workloads:
#   Code search 100 results : 17,765 -> 1,408 tokens  (92% saving)
#   SRE incident debugging  : 65,694 -> 5,118 tokens  (92% saving)
#   GitHub issue triage     : 54,174 -> 14,761 tokens (73% saving)
#   Codebase exploration    : 78,502 -> 41,254 tokens (47% saving)
# ══════════════════════════════════════════════════════════════════════════════
_HR_ENABLED = os.environ.get("HEADROOM_ENABLED", "1") == "1"
try:
    from headroom import compress as _hr_compress  # type: ignore
    _HR_AVAILABLE = True
    LOG.info("Headroom Stage 3: active — ContentRouter + CacheAligner + CCR")
except ImportError:
    _HR_AVAILABLE = False
    _hr_compress = None
    LOG.info("Headroom not installed (pip install headroom-ai[all]) — Stage 3 skipped")

# Live stats — surfaced in /health and /ase/status
_hr_stats: Dict = {
    "calls":     0,
    "tokens_in": 0,
    "tokens_out": 0,
    "chars_in":  0,
    "chars_out": 0,
}


def headroom_compress(messages: List[Dict]) -> List[Dict]:
    """Stage 3: Headroom ContentRouter + CacheAligner. Falls back silently."""
    if not _HR_AVAILABLE or not _HR_ENABLED or not messages or not _hr_compress:
        return messages
    try:
        chars_in = sum(len(str(m.get("content", ""))) for m in messages)
        result = _hr_compress(messages)
        if not result:
            return messages
        chars_out = sum(len(str(m.get("content", ""))) for m in result)
        _hr_stats["calls"]     += 1
        _hr_stats["chars_in"]  += chars_in
        _hr_stats["chars_out"] += chars_out
        _hr_stats["tokens_in"]  += chars_in  // 4
        _hr_stats["tokens_out"] += chars_out // 4
        saved = round((1 - chars_out / max(chars_in, 1)) * 100, 1)
        LOG.debug("Headroom: %d->%d chars (%.1f%% saved), cumulative %d tokens saved",
                  chars_in, chars_out, saved,
                  max(0, _hr_stats["tokens_in"] - _hr_stats["tokens_out"]))
        return result
    except Exception as exc:
        LOG.debug("Headroom compress error: %s", exc)
        return messages


def headroom_compress_text(text: str) -> str:
    """Single-string variant — wraps in user message, compresses, unwraps."""
    if not text or len(text) < 400:
        return text
    msgs = [{"role": "user", "content": text}]
    compressed = headroom_compress(msgs)
    if compressed and compressed[0].get("content"):
        return str(compressed[0]["content"])
    return text


def compress_pipeline(messages: List[Dict], tool_name: str = "_default") -> List[Dict]:
    """
    Full 4-stage compression pipeline applied before every LLM call.

    Stage 1  TokenJuice    — strip noise from each message's content string
    Stage 2  ContextPilot  — cross-message dedup + KV-cache prefix reuse
    Stage 3  Headroom      — ContentRouter (SmartCrusher/CodeCompressor/Kompress-base)
                             + CacheAligner + CCR reversible compression
    Stage 4  LLMLingua-2   — token-level neural compression (opt-in)

    Each stage is independently optional — graceful no-op if library absent.
    Typical combined saving on mixed crew workloads: 60-80%.
    """
    if not messages:
        return messages

    # Stage 1: TokenJuice — per-message noise stripping
    stage1 = []
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            m = dict(m)
            m["content"] = tokenjuice(tool_name, m["content"])
        stage1.append(m)

    # Stage 2: ContextPilot — cross-message dedup + prefix reuse
    stage2 = cp_compress_messages(stage1)

    # Stage 3: Headroom — ContentRouter + CacheAligner + CCR
    stage3 = headroom_compress(stage2)

    # Stage 4: LLMLingua-2 — token-level neural compression (opt-in)
    if _LINGUA_AVAILABLE and _LINGUA_ENABLED:
        stage4 = []
        for m in stage3:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                m = dict(m)
                m["content"] = lingua_compress(m["content"])
            stage4.append(m)
        return stage4

    return stage3


# ══════════════════════════════════════════════════════════════════════════════
# KIOKU-LITE — Tri-hybrid search: BM25 + vector + knowledge graph
# pip install kioku-lite   |   python -m kioku init
# ══════════════════════════════════════════════════════════════════════════════
_KIOKU_AVAILABLE = False
_kioku_db = None
try:
    from kioku import KiokuDB
    _kioku_db = KiokuDB(data_dir=_KIOKU_DATA)
    _KIOKU_AVAILABLE = True
    LOG.info("kioku-lite: active at %s", _KIOKU_DATA)
except (ImportError, Exception) as _e:
    LOG.info("kioku-lite: %s — ChromaDB fallback", _e)


# ══════════════════════════════════════════════════════════════════════════════
# NETWORK-AI — Write-lock mutex for shared MCP memory
# npm install -g @jovanSAPFIONEER/network-ai
# ══════════════════════════════════════════════════════════════════════════════
def nai_blackboard_write(key: str, value: str, agent_id: str) -> bool:
    try:
        r = _req.post(f"{_NAI_BASE}/blackboard/write",
                      json={"key": key, "value": value, "agent_id": agent_id,
                            "mode": "propose-validate-commit"}, timeout=3)
        return r.status_code == 200
    except Exception:
        return False

def nai_budget_check(agent_id: str, tokens_requested: int) -> bool:
    try:
        r = _req.post(f"{_NAI_BASE}/budget/check",
                      json={"agent_id": agent_id, "tokens": tokens_requested}, timeout=1)
        if not r.json().get("approved", True):
            LOG.warning("Network-AI: %s budget exceeded (%d)", agent_id, tokens_requested)
            return False
        return True
    except Exception:
        return True  # no Network-AI = allow


# ══════════════════════════════════════════════════════════════════════════════
# TOKENJUICE — Tool output compression
# FIX-1: _TJ_AVAILABLE now defined here so __main__ log doesn't crash
# Built-in Python implementation — no extra package needed.
# ══════════════════════════════════════════════════════════════════════════════
_TJ_AVAILABLE = True   # FIX-1: always True — built-in implementation

_TJ_RULES: Dict[str, Dict] = {
    "web_search":         {"max_chars": 2000, "drop_nav": False, "drop_ads": True},
    "web_fetch":          {"max_chars": 3000, "drop_nav": True,  "drop_ads": True},
    "codegraph_explore":  {"max_chars": 6000, "drop_nav": False, "drop_ads": False},
    "codegraph_search":   {"max_chars": 3000, "drop_nav": False, "drop_ads": False},
    "codegraph_impact":   {"max_chars": 4000, "drop_nav": False, "drop_ads": False},
    "read_from_memory":   {"max_chars": 3000, "drop_nav": False, "drop_ads": False},
    "asgl_get_engagement":{"max_chars": 4000, "drop_nav": False, "drop_ads": False},
    "asgl_get_diff_rows": {"max_chars": 5000, "drop_nav": False, "drop_ads": False},
    "_default":           {"max_chars": 4000, "drop_nav": True,  "drop_ads": True},
}
_URL_SHORT_RE = _re.compile(r'https?://([^/\s]{1,30})[^\s]*')

def tokenjuice(tool_name: str, raw: str) -> str:
    if not raw or len(raw) < 200:
        return raw
    rule = _TJ_RULES.get(tool_name, _TJ_RULES["_default"])
    t = _html.unescape(raw)
    t = _re.sub(r'<[^>]+>', ' ', t)
    t = _re.sub(r'\x1b\[[0-9;]*m', '', t)
    if rule.get("drop_nav"):
        t = _re.sub(r'(?i)(skip to|jump to|breadcrumb|navigation menu).*?\n', '', t)
    if rule.get("drop_ads"):
        t = _re.sub(r'(?i)(cookie policy|privacy policy|©\s*\d{4}).*?\n', '', t)
    t = _URL_SHORT_RE.sub(lambda m: f"https://{m.group(1)}/…", t)
    lines = t.splitlines(); deduped: List[str] = []; prev = None
    for line in lines:
        s = line.strip()
        if s != prev:
            deduped.append(line); prev = s
    t = "\n".join(deduped)
    t = _re.sub(r'\n{3,}', '\n\n', t)
    t = _re.sub(r'[ \t]{2,}', ' ', t)
    mx = rule["max_chars"]
    if len(t) > mx:
        head = t[:mx * 2 // 3]; tail = t[-(mx // 3):]
        removed = len(t) - mx
        t = f"{head}\n\n… [TokenJuice: {removed:,} chars removed] …\n\n{tail}"
    return t.strip()


# ══════════════════════════════════════════════════════════════════════════════
# RAG VECTOR MEMORY — ChromaDB + sentence-transformers
# pip install chromadb sentence-transformers
# ══════════════════════════════════════════════════════════════════════════════
_rag_client = None
_RAG_AVAILABLE = False

def _init_rag() -> None:
    global _rag_client, _RAG_AVAILABLE
    try:
        import chromadb
        from chromadb.config import Settings
        _rag_client = chromadb.PersistentClient(
            path=_CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        _RAG_AVAILABLE = True
        LOG.info("ChromaDB RAG: active at %s", _CHROMA_DIR)
    except ImportError:
        LOG.info("ChromaDB not installed — pip install chromadb sentence-transformers")

_init_rag()


def _get_collection(domain: str):
    if not _RAG_AVAILABLE or _rag_client is None:
        return None
    try:
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
            return _rag_client.get_or_create_collection(f"oversight_{domain}", embedding_function=ef)
        except Exception:
            return _rag_client.get_or_create_collection(f"oversight_{domain}")
    except Exception:
        return None


def rag_store_impl(domain: str, content: str, metadata: Optional[Dict] = None) -> bool:
    col = _get_collection(domain)
    if not col:
        return False
    try:
        compressed = tokenjuice(f"rag_store_{domain}", content)
        col.add(documents=[compressed],
                ids=[str(uuid.uuid4())],
                metadatas=[{"domain": domain, "ts": str(time.time()), **(metadata or {})}])
        return True
    except Exception as e:
        LOG.warning("RAG store: %s", e)
        return False


def rag_search_impl(domain: str, query: str, n: int = 5) -> str:
    col = _get_collection(domain)
    if not col:
        return ""
    try:
        count = col.count()
        if count == 0:
            return f"(No {domain} memory yet)"
        results = col.query(query_texts=[query], n_results=min(n, count))
        docs = results.get("documents", [[]])[0]
        if not docs:
            return f"(No relevant {domain} memory for: {query[:80]})"
        return f"[RAG {len(docs)} results]\n" + "\n---\n".join(docs)
    except Exception as e:
        return f"(RAG error: {e})"


def rag_store_agent_output(crew_name: str, prompt: str, result: str) -> None:
    dm = {"research": "research", "coding": "coding", "trading": "trading",
          "audit": "audit", "oversight": "oversight", "seo": "research"}
    domain = dm.get(crew_name, "research")
    rag_store_impl(domain, f"TASK: {prompt[:300]}\nRESULT: {result[:2000]}",
                   {"crew": crew_name})


# kioku upgrade: make rag_store/rag_search route through kioku first
def rag_store(domain: str, content: str, metadata: Optional[Dict] = None) -> bool:
    ok_k = False
    if _KIOKU_AVAILABLE and _kioku_db:
        try:
            compressed = tokenjuice(f"kioku_{domain}", content)
            _kioku_db.save(text=compressed, metadata={"domain": domain, **(metadata or {})})
            ok_k = True
        except Exception as e:
            LOG.debug("kioku store: %s", e)
    ok_c = rag_store_impl(domain, content, metadata)
    return ok_k or ok_c


def rag_search(domain: str, query: str, n: int = 5) -> str:
    if _KIOKU_AVAILABLE and _kioku_db:
        try:
            results = _kioku_db.search(query=query, filters={"domain": domain}, top_k=n)
            if results:
                return f"[kioku {len(results)}]\n" + "\n---\n".join(r.get("text","") for r in results)
        except Exception as e:
            LOG.debug("kioku search: %s", e)
    return rag_search_impl(domain, query, n)


LOG.info("Memory: kioku=%s chromadb=%s | Compression: TJ=✓ CP=%s LLM2=%s",
         "✓" if _KIOKU_AVAILABLE else "✗",
         "✓" if _RAG_AVAILABLE else "✗",
         "✓" if _CP_AVAILABLE else "✗",
         "✓" if _LINGUA_AVAILABLE else "✗")


# ══════════════════════════════════════════════════════════════════════════════
# HDC VECTOR MEMORY — Hyperdimensional Computing memory layer
# Pure-numpy, no extra install beyond numpy (already a crewai dep).
# Architecture:
#   • 10,000-dim bipolar (±1) hypervectors
#   • Char trigram + positional level encoding → encode any text as HD vector
#   • Store: accumulate (bundle) into labelled class vectors
#   • Query:  cosine similarity to all stored vectors, return top-k
#   • Decay:  multiply stored vectors by (1−rate) each cycle
# ══════════════════════════════════════════════════════════════════════════════
import numpy as _np
import hashlib as _hashlib
import threading as _threading
import json as _json_bg

_HDC_DIM       = int(os.environ.get("HDC_DIM", "10000"))
_HDC_DECAY     = float(os.environ.get("HDC_DECAY_RATE", "0.005"))   # per decay cycle
_HDC_SAVE_PATH = Path(os.environ.get("HDC_SAVE_PATH", str(_base / ".hdc_memory.npz")))

_hdc_lock   = _threading.Lock()
_hdc_labels: List[str]           = []   # ordered labels
_hdc_matrix: Optional[_np.ndarray] = None  # shape (N, _HDC_DIM)
_hdc_counts: List[int]           = []   # bundle counts per label


def _hdc_seed_vec(token: str) -> _np.ndarray:
    """Deterministic random ±1 hypervector seeded from token string."""
    seed = int(_hashlib.md5(token.encode()).hexdigest(), 16) % (2**31)
    rng  = _np.random.default_rng(seed)
    return rng.choice([-1.0, 1.0], size=_HDC_DIM).astype(_np.float32)


def hdc_encode(text: str) -> _np.ndarray:
    """
    Encode text → a single ±1 hypervector.
    Method: trigram bundling with positional binding via cyclic-shift permutation.
    """
    text = text.lower()[:2000]
    tokens = [text[i:i+3] for i in range(max(1, len(text)-2))]
    if not tokens:
        return _np.zeros(_HDC_DIM, dtype=_np.float32)
    acc = _np.zeros(_HDC_DIM, dtype=_np.float32)
    for pos, tok in enumerate(tokens):
        base = _hdc_seed_vec(tok)
        # positional binding: cyclic left-shift by pos % HDC_DIM
        shift = pos % _HDC_DIM
        bound = _np.roll(base, shift)
        acc  += bound
    # binarise → bipolar
    return _np.sign(acc).astype(_np.float32)


def hdc_store(label: str, text: str) -> None:
    """Bundle-encode text into the labelled class vector."""
    global _hdc_matrix, _hdc_labels, _hdc_counts
    vec = hdc_encode(text)
    with _hdc_lock:
        if label in _hdc_labels:
            idx = _hdc_labels.index(label)
            _hdc_matrix[idx] += vec
            _hdc_counts[idx] += 1
        else:
            _hdc_labels.append(label)
            _hdc_counts.append(1)
            row = vec.reshape(1, -1)
            if _hdc_matrix is None:
                _hdc_matrix = row
            else:
                _hdc_matrix = _np.vstack([_hdc_matrix, row])


def hdc_query(text: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Cosine similarity search. Returns [{label, score, count}]."""
    global _hdc_matrix, _hdc_labels
    if _hdc_matrix is None or not _hdc_labels:
        return []
    qvec = hdc_encode(text).reshape(1, -1)
    with _hdc_lock:
        mat  = _hdc_matrix.copy()
        lbls = list(_hdc_labels)
        cnts = list(_hdc_counts)
    norms = _np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    qnorm = _np.linalg.norm(qvec) + 1e-9
    sims  = (mat @ qvec.T).flatten() / (norms.flatten() * qnorm)
    idx   = _np.argsort(sims)[::-1][:top_k]
    return [{"label": lbls[i], "score": float(sims[i]), "count": cnts[i]}
            for i in idx if sims[i] > 0.0]


def hdc_decay(rate: float = _HDC_DECAY) -> None:
    """Multiplicative decay of all stored hypervectors (simulates forgetting)."""
    global _hdc_matrix
    with _hdc_lock:
        if _hdc_matrix is not None:
            _hdc_matrix *= (1.0 - rate)


def hdc_save() -> None:
    """Persist HDC memory to disk."""
    global _hdc_matrix, _hdc_labels, _hdc_counts
    try:
        with _hdc_lock:
            if _hdc_matrix is not None:
                _np.savez_compressed(
                    str(_HDC_SAVE_PATH),
                    matrix=_hdc_matrix,
                    counts=_np.array(_hdc_counts, dtype=_np.int32),
                )
                _HDC_SAVE_PATH.with_suffix(".json").write_text(
                    _json_bg.dumps(_hdc_labels), encoding="utf-8"
                )
    except Exception as _e:
        LOG.debug("HDC save: %s", _e)


def hdc_load() -> None:
    """Restore HDC memory from disk."""
    global _hdc_matrix, _hdc_labels, _hdc_counts
    try:
        lbl_f = _HDC_SAVE_PATH.with_suffix(".json")
        if _HDC_SAVE_PATH.exists() and lbl_f.exists():
            data = _np.load(str(_HDC_SAVE_PATH))
            with _hdc_lock:
                _hdc_matrix = data["matrix"].astype(_np.float32)
                _hdc_counts  = data["counts"].tolist()
                _hdc_labels  = _json_bg.loads(lbl_f.read_text(encoding="utf-8"))
            LOG.info("HDC memory: loaded %d vectors from disk", len(_hdc_labels))
    except Exception as _e:
        LOG.debug("HDC load: %s", _e)

hdc_load()


# Expose as an additional rag_store/rag_search layer
def rag_store_hdc(domain: str, content: str, label_suffix: str = "") -> None:
    """Encode content and store into HDC under 'domain[:label_suffix]' label."""
    try:
        label = f"{domain}:{label_suffix}" if label_suffix else domain
        hdc_store(label, content)
    except Exception as _e:
        LOG.debug("HDC store: %s", _e)


def rag_search_hdc(domain: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Search HDC memory filtered by domain prefix."""
    results = hdc_query(query, top_k=top_k * 3)
    return [r for r in results if r["label"].startswith(domain)][:top_k]


LOG.info("HDC Vector Memory: dim=%d decay=%.3f path=%s labels=%d",
         _HDC_DIM, _HDC_DECAY, _HDC_SAVE_PATH, len(_hdc_labels))


# ══════════════════════════════════════════════════════════════════════════════
# FEP ACTIVE INFERENCE — Free Energy Principle routing
# Karl Friston's variational free energy: F = KL(q||p) − log P(o|m)
#
# Here we maintain a Dirichlet belief distribution over crew types.
# Each successful/failed kickoff updates the posterior.
# The dispatch router uses minimum expected free energy (EFE) to select
# the crew that minimises both epistemic (uncertainty) and pragmatic
# (outcome) free energy — i.e., exploit when confident, explore when not.
#
# Key variables:
#   α  — Dirichlet concentration (pseudo-count of outcomes)
#   EFE = −(epistemic value) − (pragmatic value)
#   Epistemic: information gain = digamma(α_i) − digamma(sum(α))
#   Pragmatic: log prior belief P(crew | context)
# ══════════════════════════════════════════════════════════════════════════════
import math as _math

_FEP_SAVE_PATH = Path(os.environ.get("FEP_SAVE_PATH", str(_base / ".fep_state.json")))
_fep_lock      = _threading.Lock()

# Dirichlet alpha counts: {crew_name: [successes+1, failures+1]}
_fep_alpha: Dict[str, List[float]] = {}


def _fep_init_crew(name: str) -> None:
    if name not in _fep_alpha:
        _fep_alpha[name] = [1.0, 1.0]   # uniform prior


def fep_update(crew_name: str, success: bool) -> None:
    """Update Dirichlet posterior after observing outcome."""
    with _fep_lock:
        _fep_init_crew(crew_name)
        idx = 0 if success else 1
        _fep_alpha[crew_name][idx] += 1.0
    fep_save()


def _digamma(x: float) -> float:
    """Lanczos approximation to digamma function."""
    if x < 1:
        return _digamma(1 + x) - 1.0 / x
    result = 0.0
    while x < 6:
        result -= 1.0 / x
        x += 1
    result += _math.log(x) - 0.5 / x
    ix2 = 1.0 / (x * x)
    result -= ix2 * (1/12 - ix2 * (1/120 - ix2 / 252))
    return result


def fep_expected_free_energy(crew_name: str,
                              keyword_score: float = 0.0) -> float:
    """
    Lower EFE = more preferred.
    EFE = −epistemic_value − pragmatic_value
    Epistemic  = digamma(α_success) − digamma(α_total)    # information gain
    Pragmatic  = log(success_rate) + keyword_score_bonus
    """
    with _fep_lock:
        _fep_init_crew(crew_name)
        s, f    = _fep_alpha[crew_name]
    alpha_total  = s + f
    success_rate = s / alpha_total
    epistemic    = _digamma(s) - _digamma(alpha_total)   # > 0 means useful to try
    pragmatic    = _math.log(max(success_rate, 0.01))
    bonus        = keyword_score * 0.5
    return -(epistemic + pragmatic + bonus)


def fep_select_crew(scores: Dict[str, int],
                     fallback: str = "research") -> str:
    """
    Combine keyword scores with FEP expected-free-energy.
    Returns crew with minimum EFE (most preferred action).
    """
    candidates = {k: v for k, v in scores.items() if v > 0}
    if not candidates:
        candidates = {fallback: 1}
    efe_map = {name: fep_expected_free_energy(name, kw_score)
               for name, kw_score in candidates.items()}
    return min(efe_map, key=lambda k: efe_map[k])


def fep_save() -> None:
    try:
        with _fep_lock:
            data = dict(_fep_alpha)
        _FEP_SAVE_PATH.write_text(
            _json_bg.dumps(data, indent=2), encoding="utf-8"
        )
    except Exception as _e:
        LOG.debug("FEP save: %s", _e)


def fep_load() -> None:
    global _fep_alpha
    try:
        if _FEP_SAVE_PATH.exists():
            with _fep_lock:
                _fep_alpha = _json_bg.loads(
                    _FEP_SAVE_PATH.read_text(encoding="utf-8")
                )
            LOG.info("FEP Active Inference: loaded %d crew belief states", len(_fep_alpha))
    except Exception as _e:
        LOG.debug("FEP load: %s", _e)

fep_load()
LOG.info("FEP Active Inference: ready (%d priors)", len(_fep_alpha))


# ══════════════════════════════════════════════════════════════════════════════
# COGNITIVE STATE — Modal controller
# Tracks the orchestrator's current operating mode based on recent performance.
#
# Modes and their effects:
#   EXPLORE     — high novelty, low confidence → higher LLM temp, broader tool use
#   EXPLOIT     — high confidence, recent wins  → lower temp, targeted tools
#   CONSOLIDATE — memory consolidation pass    → background dream thread runs
#   ALERT       — recent errors / anomalies    → T1 reasoning escalation
#   REST        — low activity window          → decay thread, save state
#
# State is persisted to disk so it survives restarts.
# ══════════════════════════════════════════════════════════════════════════════
_COG_SAVE_PATH = Path(os.environ.get("COG_SAVE_PATH", str(_base / ".cog_state.json")))
_cog_lock      = _threading.Lock()

_COGNITIVE_MODES = ["EXPLORE", "EXPLOIT", "CONSOLIDATE", "ALERT", "REST"]

_cog_state: Dict[str, Any] = {
    "mode":         "EXPLORE",
    "confidence":   0.5,    # 0-1: fraction of recent successes
    "arousal":      0.5,    # 0-1: activity level (high = recent traffic)
    "novelty":      0.5,    # 0-1: how unexpected recent prompts are
    "error_rate":   0.0,    # fraction of recent failures
    "cycle":        0,      # total update ticks
    "last_updated": "",
}

# Sliding window of recent outcomes
_cog_window: List[bool] = []   # True=success, False=failure
_COG_WINDOW  = 20              # rolling window size


def cog_update(success: bool, novelty: float = 0.5) -> str:
    """
    Update cognitive state after a crew run.
    Returns new mode name.
    """
    global _cog_window
    with _cog_lock:
        _cog_window.append(success)
        if len(_cog_window) > _COG_WINDOW:
            _cog_window = _cog_window[-_COG_WINDOW:]
        w = _cog_window
        n = len(w)
        confidence = sum(w) / n
        error_rate = 1.0 - confidence
        # Arousal: decays slowly unless traffic is high
        arousal    = min(1.0, _cog_state["arousal"] * 0.9 + 0.1)
        # Novelty: exponential moving average
        new_novelty = _cog_state["novelty"] * 0.8 + novelty * 0.2
        # Mode selection logic
        if error_rate > 0.4:
            mode = "ALERT"
        elif confidence > 0.75 and new_novelty < 0.3:
            mode = "EXPLOIT"
        elif new_novelty > 0.7 or confidence < 0.4:
            mode = "EXPLORE"
        elif arousal < 0.15:
            mode = "REST"
        else:
            mode = "CONSOLIDATE"
        _cog_state.update({
            "mode":         mode,
            "confidence":   round(confidence, 3),
            "arousal":      round(arousal, 3),
            "novelty":      round(new_novelty, 3),
            "error_rate":   round(error_rate, 3),
            "cycle":        _cog_state["cycle"] + 1,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        result = mode
    cog_save()
    LOG.info("CogState: mode=%s conf=%.2f novelty=%.2f err=%.2f cycle=%d",
             result, confidence, new_novelty, error_rate, _cog_state["cycle"])
    return result


def cog_llm_temperature(base_temp: float = 0.1) -> float:
    """Adjust LLM temperature based on current cognitive mode."""
    mode = _cog_state.get("mode", "EXPLORE")
    return {
        "EXPLORE":     min(1.0, base_temp + 0.25),
        "EXPLOIT":     max(0.0, base_temp - 0.05),
        "CONSOLIDATE": base_temp,
        "ALERT":       max(0.0, base_temp - 0.08),
        "REST":        base_temp,
    }.get(mode, base_temp)


def cog_save() -> None:
    try:
        with _cog_lock:
            data = dict(_cog_state)
        _COG_SAVE_PATH.write_text(
            _json_bg.dumps(data, indent=2), encoding="utf-8"
        )
    except Exception as _e:
        LOG.debug("CogState save: %s", _e)


def cog_load() -> None:
    try:
        if _COG_SAVE_PATH.exists():
            with _cog_lock:
                loaded = _json_bg.loads(_COG_SAVE_PATH.read_text(encoding="utf-8"))
                _cog_state.update(loaded)
            LOG.info("CogState: loaded mode=%s cycle=%d",
                     _cog_state["mode"], _cog_state["cycle"])
    except Exception as _e:
        LOG.debug("CogState load: %s", _e)

cog_load()
LOG.info("Cognitive State: mode=%s confidence=%.2f",
         _cog_state["mode"], _cog_state["confidence"])



# ══════════════════════════════════════════════════════════════════════════════
# LLM CALL HELPER — FIX-2: replaces LLM_T3_BULK.call() which doesn't exist
# Uses litellm directly for inline completions outside of crew tasks
# ══════════════════════════════════════════════════════════════════════════════
def llm_complete(prompt: str, llm: LLM = None, max_tokens: int = 1024) -> str:
    """
    Call an LLM directly (not via a crew) for inline tasks like context compression.
    FIX-2: crewai.LLM has no .call() method — must use litellm or requests.
    """
    _llm_obj = llm or LLM_T3_BULK
    try:
        # litellm is always available (crewai depends on it)
        import litellm
        resp = litellm.completion(
            model=_llm_obj.model,
            messages=[{"role": "user", "content": prompt}],
            api_base=getattr(_llm_obj, "base_url", None),
            api_key=getattr(_llm_obj, "api_key", None),
            max_tokens=max_tokens,
            temperature=getattr(_llm_obj, "temperature", 0.0),
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        LOG.warning("llm_complete fallback: %s", exc)
        # Last resort: call FreeLLMAPI directly
        try:
            r = _req.post(f"{FREELLM_BASE}/chat/completions",
                          json={"model": "gpt-4o",
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": max_tokens},
                          headers={"Authorization": f"Bearer {FREELLM_KEY}"},
                          timeout=30)
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            return ""


# ══════════════════════════════════════════════════════════════════════════════
# MCP MEMORY TOOLS
# ══════════════════════════════════════════════════════════════════════════════
class _StoreArgs(BaseModel):
    thread_id: str = Field(..., description="Thread namespace: 'audit-42', 'trading-signals'")
    content:   str = Field(..., description="Finding or observation to store")
    agent_id:  str = Field("agent", description="ID of the writing agent")

class MCPStoreTool(BaseTool):
    name:        str = "store_to_memory"
    description: str = "Store a finding to shared MCP memory. Use thread_id to namespace."
    args_schema: Type[BaseModel] = _StoreArgs
    def _run(self, thread_id: str, content: str, agent_id: str = "agent") -> str:
        try:
            r = _req.post(f"http://localhost:{DASHBOARD_PORT}/api/memory/add",
                          json={"thread_id": thread_id, "agent_id": agent_id, "content": content},
                          timeout=3)
            return f"Stored in '{thread_id}' id={r.json().get('id','?')}"
        except Exception as e:
            return f"Memory store failed: {e}"

class _ReadArgs(BaseModel):
    thread_id: str = Field(..., description="Thread to read")
    limit:     int = Field(10, ge=1, le=100)

class MCPReadTool(BaseTool):
    name:        str = "read_from_memory"
    description: str = "Read recent agent messages from a shared MCP memory thread."
    args_schema: Type[BaseModel] = _ReadArgs
    def _run(self, thread_id: str, limit: int = 10) -> str:
        try:
            r = _req.get(f"http://localhost:{DASHBOARD_PORT}/api/memory/get",
                         params={"thread_id": thread_id, "limit": limit}, timeout=3)
            return tokenjuice("read_from_memory", r.text or "(empty)")
        except Exception as e:
            return f"Memory read failed: {e}"

class TradingSignalTool(BaseTool):
    name:        str = "get_trading_signal"
    description: str = "Get current XAUUSD FusionModel signal: BUY/SELL/HOLD + confidence."
    def _run(self, **kwargs) -> str:
        try:
            return _req.get(f"http://localhost:{DASHBOARD_PORT}/api/signal", timeout=3).text
        except Exception as e:
            return f"Signal fetch failed: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# RAG TOOLS
# ══════════════════════════════════════════════════════════════════════════════
class _RAGSearchArgs(BaseModel):
    query:     str = Field(..., description="Natural language query")
    domain:    str = Field("audit", description="audit|research|trading|oversight|coding|seo")
    n_results: int = Field(5, ge=1, le=20)

class RAGSearchTool(BaseTool):
    name:        str = "rag_search_memory"
    description: str = "Semantic search over past agent findings. Returns only relevant context."
    args_schema: Type[BaseModel] = _RAGSearchArgs
    def _run(self, query: str, domain: str = "audit", n_results: int = 5) -> str:
        return rag_search(domain, query, n_results)

class _RAGStoreArgs(BaseModel):
    content: str = Field(..., description="Finding to store in vector memory")
    domain:  str = Field("audit", description="audit|research|trading|oversight|coding|seo")
    tags:    str = Field("", description="Comma-separated tags")

class RAGStoreTool(BaseTool):
    name:        str = "rag_store_finding"
    description: str = "Store an important finding in semantic vector memory for future retrieval."
    args_schema: Type[BaseModel] = _RAGStoreArgs
    def _run(self, content: str, domain: str = "audit", tags: str = "") -> str:
        ok = rag_store(domain, content, {"tags": tags} if tags else None)
        return "Stored in vector memory" if ok else "Storage failed — install chromadb"

rag_search_tool = RAGSearchTool()
rag_store_tool  = RAGStoreTool()


# ══════════════════════════════════════════════════════════════════════════════
# FOCUS COMPRESS TOOL — Agent-driven context compaction
# FIX-2: uses llm_complete() instead of LLM_T3_BULK.call()
# ══════════════════════════════════════════════════════════════════════════════
class _FocusArgs(BaseModel):
    context:          str = Field(..., description="Context or conversation history to compress")
    target:           str = Field("key decisions, findings, and open questions",
                                  description="What to preserve")
    max_output_words: int = Field(200, ge=50, le=1000)

class FocusCompressTool(BaseTool):
    name:        str = "compress_context"
    description: str = (
        "Compress a long context into a compact brief. Call when context is getting long. "
        "Preserves: key decisions, findings, critical numbers. Uses free bulk model."
    )
    args_schema: Type[BaseModel] = _FocusArgs
    def _run(self, context: str, target: str = "key decisions, findings, and open questions",
             max_output_words: int = 200) -> str:
        if len(context) < 500:
            return context
        compressed = tokenjuice("focus_compress", context)
        compressed = headroom_compress_text(compressed)  # Stage 3
        compressed = lingua_compress(compressed, ratio=0.4)
        brief = llm_complete(  # FIX-2: correct method
            f"Compress to {max_output_words} words. Preserve: {target}.\n"
            f"Use bullets. Include specific numbers and names.\n\nCONTEXT:\n{compressed[:6000]}"
        )
        return f"[COMPRESSED: {len(context)}→{len(brief)} chars]\n\n{brief}" if brief else compressed

focus_tool = FocusCompressTool()


# ══════════════════════════════════════════════════════════════════════════════
# XDA ARTICLE MCP TOOLS
# SearXNG, Context7, Crawl4AI, Playwright, Mem0
# All degrade gracefully if containers not running.
# ══════════════════════════════════════════════════════════════════════════════

# ── SearXNG ───────────────────────────────────────────────────────────────────
class _SearchArgs(BaseModel):
    query: str = Field(..., description="Search query")
    n:     int = Field(5, ge=1, le=20)

class SearXNGSearchTool(BaseTool):
    name:        str = "web_search"
    description: str = (
        "Search the web via self-hosted SearXNG. Free, private, no API key. "
        "Use for: FRC/TPR regulatory updates, competitor research, gold prices, news. "
        "Setup: docker run -d -p 8080:8080 searxng/searxng"
    )
    args_schema: Type[BaseModel] = _SearchArgs
    def _run(self, query: str, n: int = 5) -> str:
        try:
            r = _req.get(f"{_SEARXNG_URL}/search",
                         params={"q": query, "format": "json"}, timeout=8)
            if not r.ok:
                return f"SearXNG offline. Start: docker run -d -p 8080:8080 searxng/searxng"
            results = r.json().get("results", [])[:n]
            if not results:
                return f"No results for: {query}"
            lines = [f"[{i+1}] {res.get('title','')}\n    {res.get('url','')}\n    {res.get('content','')[:200]}"
                     for i, res in enumerate(results)]
            return tokenjuice("web_search", f"Search: {query}\n\n" + "\n\n".join(lines))
        except _req.ConnectionError:
            return "SearXNG offline. Start: docker run -d -p 8080:8080 searxng/searxng"
        except Exception as e:
            return f"SearXNG error: {e}"

# ── Context7 ──────────────────────────────────────────────────────────────────
class _C7Args(BaseModel):
    library: str = Field(..., description="Library name: 'crewai', 'fastapi', 'chromadb'")
    topic:   str = Field("", description="Specific topic within the library")
    tokens:  int = Field(5000, ge=1000, le=20000)

class Context7DocsTool(BaseTool):
    name:        str = "get_library_docs"
    description: str = (
        "Fetch current documentation for any programming library from Context7 (Upstash). "
        "Use BEFORE writing any code to get the actual current API (prevents hallucination). "
        "Examples: get_library_docs('crewai', 'tools'), get_library_docs('fastapi', 'streaming')"
    )
    args_schema: Type[BaseModel] = _C7Args
    def _run(self, library: str, topic: str = "", tokens: int = 5000) -> str:
        try:
            res_r = _req.get("https://context7.com/api/v1/search",
                             params={"q": library}, timeout=8)
            if not res_r.ok:
                return f"Context7 unavailable ({res_r.status_code})"
            libs = res_r.json().get("results", [])
            if not libs:
                return f"Library '{library}' not found in Context7"
            lib_id = libs[0].get("id", "")
            params: Dict = {"libraryId": lib_id, "tokens": tokens}
            if topic:
                params["topic"] = topic
            docs_r = _req.get("https://context7.com/api/v1/library-docs",
                               params=params, timeout=10)
            if not docs_r.ok:
                return f"Context7 docs fetch failed: {docs_r.status_code}"
            text = docs_r.text or ""
            return tokenjuice("web_fetch", f"[{library}/{topic} docs]\n\n{text}")
        except _req.ConnectionError:
            return "Context7 requires internet connection"
        except Exception as e:
            return f"Context7 error: {e}"

# ── Crawl4AI ──────────────────────────────────────────────────────────────────
class _CrawlArgs(BaseModel):
    url:        str = Field(..., description="URL to fetch as clean markdown")
    js_wait_ms: int = Field(0, ge=0, le=10000, description="Wait for JS (0=static only)")

class Crawl4AITool(BaseTool):
    name:        str = "web_fetch"
    description: str = (
        "Fetch any URL as clean markdown. Free alternative to Firecrawl ($16/mo). "
        "Use for: FRC guidance, TPR consultations, company websites, news articles. "
        "Use browser_navigate for pages requiring login or JS interaction. "
        "Setup: docker run -d -p 11235:11235 unclecode/crawl4ai:latest"
    )
    args_schema: Type[BaseModel] = _CrawlArgs
    def _run(self, url: str, js_wait_ms: int = 0) -> str:
        payload: Dict = {"url": url, "crawler_params": {"headless": True},
                         "extra": {"word_count_threshold": 10}}
        if js_wait_ms > 0:
            payload["crawler_params"]["page_timeout"] = js_wait_ms
        try:
            r = _req.post(f"{_CRAWL4AI_URL}/crawl", json=payload, timeout=30)
            if not r.ok:
                return f"Crawl4AI offline. Start: docker run -d -p 11235:11235 unclecode/crawl4ai:latest"
            data = r.json()
            md = (data.get("result", {}).get("markdown") or
                  data.get("markdown") or data.get("content") or str(data)[:3000])
            return tokenjuice("web_fetch", f"[{url}]\n\n{md}")
        except _req.ConnectionError:
            return "Crawl4AI offline. Start: docker run -d -p 11235:11235 unclecode/crawl4ai:latest"
        except Exception as e:
            return f"Crawl4AI error: {e}"

# ── Playwright MCP ────────────────────────────────────────────────────────────
class _PWArgs(BaseModel):
    url:      str = Field(..., description="URL to navigate to")
    action:   str = Field("snapshot", description="snapshot|click|type|scroll|back|forward")
    selector: str = Field("", description="Accessibility selector for click/type")
    text:     str = Field("", description="Text to type")

class PlaywrightTool(BaseTool):
    name:        str = "browser_navigate"
    description: str = (
        "Control a real browser via Playwright (accessibility tree, no vision needed). "
        "Use for: Companies House checks, HMRC portals, login-required pages. "
        "Use web_fetch for static pages — Playwright only when browser session needed. "
        "Setup: npm install -g @playwright/mcp@latest && playwright-mcp --port 3333"
    )
    args_schema: Type[BaseModel] = _PWArgs
    def _run(self, url: str, action: str = "snapshot",
             selector: str = "", text: str = "") -> str:
        try:
            if action == "snapshot":
                r = _req.post(f"{_PW_BASE}/navigate", json={"url": url}, timeout=15)
            elif action == "click":
                r = _req.post(f"{_PW_BASE}/click", json={"selector": selector}, timeout=10)
            elif action == "type":
                r = _req.post(f"{_PW_BASE}/type",
                               json={"selector": selector, "text": text}, timeout=10)
            elif action in ("scroll", "back", "forward"):
                r = _req.post(f"{_PW_BASE}/{action}", json={}, timeout=10)
            else:
                return f"Unknown action: {action}. Use: snapshot|click|type|scroll|back|forward"
            if not r.ok:
                return (f"Playwright offline. Start: "
                        f"npm install -g @playwright/mcp@latest && playwright-mcp --port 3333")
            data = r.json()
            content = data.get("content") or data.get("snapshot") or str(data)[:4000]
            return tokenjuice("web_fetch", f"[{action}: {url or selector}]\n\n{content}")
        except _req.ConnectionError:
            return "Playwright offline. Start: npm install -g @playwright/mcp@latest && playwright-mcp --port 3333"
        except Exception as e:
            return f"Playwright error: {e}"

# ── Mem0 (LLM-deduped memory, replaces $249/mo) ───────────────────────────────
# FIX-3: corrected endpoints to /v1/memories and /v1/memories/search
class _Mem0StoreArgs(BaseModel):
    content: str = Field(..., description="Fact or decision. Mem0 auto-deduplicates.")
    user_id: str = Field("oversight-graeme", description="User ID for this memory")

class _Mem0SearchArgs(BaseModel):
    query:   str = Field(..., description="What to recall")
    user_id: str = Field("oversight-graeme")
    limit:   int = Field(5, ge=1, le=20)

class Mem0StoreTool(BaseTool):
    name:        str = "mem0_remember"
    description: str = (
        "Store a conversation fact in Mem0 (LLM-curated, auto-deduplicating). "
        "Use for: client preferences, key decisions, personal context. "
        "NOT for bulk docs — use rag_store_finding for those. "
        "Setup: docker run -d -p 8765:8765 mem0ai/openmemory-mcp:latest"
    )
    args_schema: Type[BaseModel] = _Mem0StoreArgs
    def _run(self, content: str, user_id: str = "oversight-graeme") -> str:
        try:
            # FIX-3: correct OpenMemory endpoint
            r = _req.post(f"{_MEM0_URL}/v1/memories",
                          json={"messages": [{"role": "user", "content": content}],
                                "user_id": user_id}, timeout=15)
            if not r.ok:
                return f"Mem0 offline. Start: docker run -d -p 8765:8765 mem0ai/openmemory-mcp:latest"
            events = r.json().get("results", [])
            return f"Mem0: {', '.join(e.get('event','?') for e in events) or 'stored'}"
        except _req.ConnectionError:
            rag_store("research", content, {"source": "mem0_fallback", "user_id": user_id})
            return "Mem0 offline → stored in kioku/ChromaDB fallback"
        except Exception as e:
            return f"Mem0 error: {e}"

class Mem0SearchTool(BaseTool):
    name:        str = "mem0_recall"
    description: str = "Recall deduplicated facts from Mem0. No stale contradictions."
    args_schema: Type[BaseModel] = _Mem0SearchArgs
    def _run(self, query: str, user_id: str = "oversight-graeme", limit: int = 5) -> str:
        try:
            # FIX-3: correct OpenMemory search endpoint
            r = _req.get(f"{_MEM0_URL}/v1/memories/search",
                         params={"query": query, "user_id": user_id, "limit": limit}, timeout=5)
            if not r.ok:
                return rag_search("research", query, limit)
            results = r.json().get("results", [])
            if not results:
                return f"(No Mem0 memories for: {query[:80]})"
            return "[Mem0]\n" + "\n".join(f"[{m.get('score',0):.2f}] {m.get('memory','')}"
                                          for m in results)
        except _req.ConnectionError:
            return rag_search("research", query, limit)
        except Exception as e:
            return f"Mem0 recall error: {e}"

# ── Screaming Frog ────────────────────────────────────────────────────────────
class _SFArgs(BaseModel):
    url:      str = Field(..., description="URL to crawl")
    mode:     str = Field("spider", description="spider|list|sitemap")
    max_urls: int = Field(100, ge=1, le=10000)

class ScreamingFrogTool(BaseTool):
    name:        str = "screaming_frog_crawl"
    description: str = (
        "Crawl a website with Screaming Frog SEO Spider. "
        "Returns: broken links, missing titles, H1s, duplicate content, redirects. "
        "Setup: Screaming Frog → Configuration → API Access → Enable MCP Server on port 6800"
    )
    args_schema: Type[BaseModel] = _SFArgs
    def _run(self, url: str, mode: str = "spider", max_urls: int = 100) -> str:
        sf_base = f"http://localhost:{SF_MCP_PORT}"
        try:
            r = _req.post(f"{sf_base}/api/v1/crawl/start",
                          json={"url": url, "mode": mode, "max_urls": max_urls}, timeout=10)
            if not r.ok:
                return (f"Screaming Frog MCP offline. "
                        f"Enable: SF → Configuration → API Access → MCP Server → port {SF_MCP_PORT}")
            crawl_id = r.json().get("crawl_id", "")
            for _ in range(24):
                time.sleep(5)
                s = _req.get(f"{sf_base}/api/v1/crawl/{crawl_id}/status", timeout=5).json()
                if s.get("status") in ("complete", "finished"):
                    break
            res = _req.get(f"{sf_base}/api/v1/crawl/{crawl_id}/export",
                           params={"format": "json", "limit": 50}, timeout=10).json()
            iss = res.get("issues", {})
            return (f"Crawl: {res.get('total_urls',0)} URLs | {url}\n"
                    f"Broken: {iss.get('broken_links',0)} | Missing title: {iss.get('missing_title',0)} "
                    f"| Missing H1: {iss.get('missing_h1',0)} | Duplicates: {iss.get('duplicate_titles',0)}")
        except _req.ConnectionError:
            return (f"Screaming Frog offline. "
                    f"Enable: SF → Configuration → API Access → MCP Server → port {SF_MCP_PORT}")
        except Exception as e:
            return f"Screaming Frog error: {e}"

# ── Tool instances ─────────────────────────────────────────────────────────────
mem_store        = MCPStoreTool()
mem_read         = MCPReadTool()
sig_tool         = TradingSignalTool()
sf_tool          = ScreamingFrogTool()
searxng_tool     = SearXNGSearchTool()
context7_tool    = Context7DocsTool()
crawl4ai_tool    = Crawl4AITool()
playwright_tool  = PlaywrightTool()
mem0_store_tool  = Mem0StoreTool()
mem0_recall_tool = Mem0SearchTool()
LOG.info("MCP tools: SearXNG, Context7, Crawl4AI, Playwright, Mem0, ScreamingFrog, RAG, Focus")


# ══════════════════════════════════════════════════════════════════════════════
# CODEGRAPH TOOLS
# npm install -g @colbymchenry/codegraph && codegraph init -i
# ══════════════════════════════════════════════════════════════════════════════
import subprocess as _sp

def _cg_rest(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    try:
        r = _req.get(f"http://localhost:{CG_REST_PORT}{path}",
                     params=params or {}, timeout=5)
        return r.json() if r.ok else None
    except Exception:
        return None

def _cg_cli(args: List[str], cwd: Optional[str] = None) -> str:
    try:
        res = _sp.run(["codegraph"] + args, capture_output=True, text=True,
                      timeout=15, cwd=cwd or str(_base), encoding="utf-8", errors="replace")
        return res.stdout.strip() or res.stderr.strip() or "(no output)"
    except FileNotFoundError:
        return "CodeGraph not installed. Run: npm install -g @colbymchenry/codegraph"
    except Exception as e:
        return f"CodeGraph error: {e}"

class _CGExploreArgs(BaseModel):
    symbol:  str = Field(..., description="Symbol: class, function, method name")
    depth:   int = Field(2, ge=1, le=5)
    project: str = Field("", description="Absolute project path")

class CodeGraphExploreTool(BaseTool):
    name:        str = "codegraph_explore"
    description: str = "Explore a code symbol using the pre-indexed knowledge graph. ALWAYS call before reading files."
    args_schema: Type[BaseModel] = _CGExploreArgs
    def _run(self, symbol: str, depth: int = 2, project: str = "") -> str:
        data = _cg_rest(f"/api/explore/{symbol}", {"depth": depth})
        if data:
            return tokenjuice("codegraph_explore", json.dumps(data, indent=2))
        return _cg_cli(["explore", symbol, f"--depth={depth}", "--format=json"],
                       cwd=project if project and os.path.isdir(project) else None)

class _CGSearchArgs(BaseModel):
    query: str = Field(..., description="Symbol or text to search")
    limit: int = Field(20, ge=1, le=100)

class CodeGraphSearchTool(BaseTool):
    name:        str = "codegraph_search"
    description: str = "Find any symbol in the codebase instantly. Use first, then codegraph_explore."
    args_schema: Type[BaseModel] = _CGSearchArgs
    def _run(self, query: str, limit: int = 20) -> str:
        data = _cg_rest("/api/search", {"q": query, "limit": limit})
        if data:
            return tokenjuice("codegraph_search", json.dumps(data, indent=2))
        return _cg_cli(["search", query, f"--limit={limit}", "--format=json"])

class _CGImpactArgs(BaseModel):
    symbol: str = Field(..., description="Symbol to check — what breaks if this changes?")

class CodeGraphImpactTool(BaseTool):
    name:        str = "codegraph_impact"
    description: str = "Trace full impact of a symbol change. ALWAYS call before modifying code."
    args_schema: Type[BaseModel] = _CGImpactArgs
    def _run(self, symbol: str) -> str:
        data = _cg_rest(f"/api/impact/{symbol}")
        if data:
            return tokenjuice("codegraph_impact", json.dumps(data, indent=2))
        return _cg_cli(["impact", symbol, "--format=json"])

class _CGStatusArgs(BaseModel):
    project: str = Field("", description="Absolute project path")

class CodeGraphStatusTool(BaseTool):
    name:        str = "codegraph_status"
    description: str = "Check CodeGraph index status (files, nodes, last update)."
    args_schema: Type[BaseModel] = _CGStatusArgs
    def _run(self, project: str = "") -> str:
        data = _cg_rest("/api/status")
        if data:
            return json.dumps(data, indent=2)
        return _cg_cli(["status", "--format=json"])

cg_explore = CodeGraphExploreTool()
cg_search  = CodeGraphSearchTool()
cg_impact  = CodeGraphImpactTool()
cg_status  = CodeGraphStatusTool()
_CG_TOOLS_AVAILABLE = True
LOG.info("CodeGraph: REST at :%s | CLI fallback active", CG_REST_PORT)




# ══════════════════════════════════════════════════════════════════════════════
# BROWSERBASE MCP TOOL — managed cloud browser for OWAY vision/portal access
# Replaces self-hosted Playwright for OWAY crew.
# Playwright stays for research/seo crews that don't need session persistence.
#
# Setup: npm install -g @browserbasehq/mcp
#        Add to .env: BROWSERBASE_API_KEY=... BROWSERBASE_PROJECT_ID=...
# Docs: https://github.com/browserbase/mcp-server-browserbase
# ══════════════════════════════════════════════════════════════════════════════
_BB_API_KEY     = os.environ.get("BROWSERBASE_API_KEY", "")
_BB_PROJECT_ID  = os.environ.get("BROWSERBASE_PROJECT_ID", "")
_BB_PORT        = os.environ.get("BROWSERBASE_MCP_PORT", "3334")
_BB_BASE        = f"http://localhost:{_BB_PORT}"
_BB_AVAILABLE   = bool(_BB_API_KEY and _BB_PROJECT_ID)

class _BBArgs(BaseModel):
    url:     str = Field(..., description="URL to navigate to")
    action:  str = Field("snapshot", description="snapshot|click|type|scroll|extract")
    selector:str = Field("", description="Element selector for click/type")
    text:    str = Field("", description="Text to type")
    extract: str = Field("", description="What data to extract from page")

class BrowserbaseTool(BaseTool):
    name:        str = "browserbase_navigate"
    description: str = (
        "Managed cloud browser via Browserbase + Stagehand. "
        "Persistent sessions, no local process needed. "
        "Use for OWAY source collection: Companies House, HMRC portals, "
        "client web sources requiring login or JavaScript. "
        "Prefer over browser_navigate for OWAY tasks — more reliable for agentic loops. "
        "Requires BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID in .env. "
        "Setup: npm install -g @browserbasehq/mcp"
    )
    args_schema: Type[BaseModel] = _BBArgs

    def _run(self, url: str, action: str = "snapshot",
             selector: str = "", text: str = "", extract: str = "") -> str:
        if not _BB_AVAILABLE:
            LOG.warning("Browserbase: API key not set — falling back to Playwright")
            return playwright_tool._run(url=url, action=action,
                                         selector=selector, text=text)
        try:
            if action == "snapshot":
                r = _req.post(f"{_BB_BASE}/navigate",
                               json={"url": url, "projectId": _BB_PROJECT_ID},
                               headers={"x-bb-api-key": _BB_API_KEY}, timeout=20)
            elif action == "extract":
                r = _req.post(f"{_BB_BASE}/extract",
                               json={"url": url, "instruction": extract,
                                     "projectId": _BB_PROJECT_ID},
                               headers={"x-bb-api-key": _BB_API_KEY}, timeout=30)
            elif action == "click":
                r = _req.post(f"{_BB_BASE}/act",
                               json={"action": f"click on {selector}",
                                     "projectId": _BB_PROJECT_ID},
                               headers={"x-bb-api-key": _BB_API_KEY}, timeout=15)
            elif action == "type":
                r = _req.post(f"{_BB_BASE}/act",
                               json={"action": f"type '{text}' into {selector}",
                                     "projectId": _BB_PROJECT_ID},
                               headers={"x-bb-api-key": _BB_API_KEY}, timeout=15)
            else:
                return f"Unknown action: {action}"

            if not r.ok:
                # Fallback to local Playwright
                LOG.warning("Browserbase HTTP %s — falling back to Playwright", r.status_code)
                return playwright_tool._run(url=url, action=action,
                                             selector=selector, text=text)
            data    = r.json()
            content = (data.get("content") or data.get("result") or
                       data.get("snapshot") or str(data)[:4000])
            return tokenjuice("web_fetch", f"[Browserbase {action}: {url}]\n\n{content}")
        except _req.ConnectionError:
            LOG.warning("Browserbase MCP offline — falling back to Playwright")
            return playwright_tool._run(url=url, action=action,
                                         selector=selector, text=text)
        except Exception as e:
            return f"Browserbase error: {e}"

browserbase_tool = BrowserbaseTool()
LOG.info("Browserbase: %s (API key %s)",
         "configured" if _BB_AVAILABLE else "no key — Playwright fallback",
         "set" if _BB_API_KEY else "NOT SET")

# ══════════════════════════════════════════════════════════════════════════════
# ASGL-A TOOLS (Oversight-A FRS102 audit)
# ══════════════════════════════════════════════════════════════════════════════
os.environ.setdefault("ASGL_BASE_URL", ASGL_BASE_URL)
try:
    from oversight_enhancements.asgl_crew_tools import ASGL_AUDIT_TOOLS, ASGL_OVERSIGHT_TOOLS
    LOG.info("ASGL-A tools: loaded (%s)", ASGL_BASE_URL)
    _ASGL_TOOLS_AVAILABLE = True
except ImportError as _e:
    LOG.warning("ASGL-A tools: not found (%s)", _e)
    ASGL_AUDIT_TOOLS = []; ASGL_OVERSIGHT_TOOLS = []
    _ASGL_TOOLS_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# CREW TIER ASSIGNMENTS (FIX-6: defined before CREW_MAP references it)
# ══════════════════════════════════════════════════════════════════════════════
CREW_TIERS = {
    "research": "T2-research (Gemini 2.5 Flash) + SearXNG + Crawl4AI + Playwright + Mem0",
    "coding":   "T2-coding (DeepSeek V3) + CodeGraph + Context7",
    "trading":  "T2-research + T1-reasoning risk",
    "audit":    "T1-reasoning (DeepSeek R1) + ASGL-A tools",
    "oversight":"T1-reasoning (DeepSeek R1) + cross-engagement meta-review",
    "seo":      "T2-research + Screaming Frog + Crawl4AI + SearXNG",
}


# ══════════════════════════════════════════════════════════════════════════════
# CREWS
# ══════════════════════════════════════════════════════════════════════════════

# ── Research ──────────────────────────────────────────────────────────────────
research_crew = Crew(agents=[
    Agent(role="Senior Research Lead",
          goal=(
              "Conduct rigorous research. Find primary sources, synthesise findings. "
              "Search web (web_search), fetch pages (web_fetch), use browser for auth pages. "
              "Store findings in MCP memory. Use compress_context when context grows long."
          ),
          backstory="Senior researcher, AI/finance/quantitative methods. Peer-review quality.",
          llm=LLM_T2_RESEARCH,
          tools=[mem_store, mem_read, rag_search_tool, rag_store_tool,
                 searxng_tool, crawl4ai_tool, playwright_tool,
                 mem0_store_tool, mem0_recall_tool, sf_tool, focus_tool],
          verbose=True, max_iter=5),
    Agent(role="Fact Checker",
          goal="Verify every claim. Rate: HIGH/MEDIUM/LOW/UNVERIFIED.",
          backstory="Meticulous fact-checker. Cross-references all claims.",
          llm=LLM_T3_BULK,
          tools=[mem_read, searxng_tool],
          verbose=True, max_iter=3),
    Agent(role="Research Summariser",
          goal="Synthesise verified findings into a concise brief with structured headers.",
          backstory="Executive summaries readable in under 3 minutes.",
          llm=LLM_T3_BULK,
          tools=[mem_store, mem_read, focus_tool],
          verbose=True, max_iter=2),
], process=Process.sequential, verbose=True)

# ── Coding ────────────────────────────────────────────────────────────────────
coding_crew = Crew(agents=[
    Agent(role="Software Architect",
          goal=(
              "Design architecture. ALWAYS: codegraph_search → codegraph_explore → "
              "codegraph_impact → get_library_docs → spec. Document every decision."
          ),
          backstory="Senior architect, 15yr production. Simplest design satisfying requirements.",
          llm=LLM_T2_CODING,
          tools=[cg_search, cg_explore, cg_impact, cg_status, context7_tool, mem_store, mem_read],
          verbose=True, max_iter=3),
    Agent(role="Senior Software Engineer",
          goal=(
              "Implement spec. Before coding: codegraph_search → explore → impact → "
              "get_library_docs for current APIs. Type hints, docstrings, error handling."
          ),
          backstory="Ships production code. Never uses deprecated APIs. Never duplicates.",
          llm=LLM_T2_CODING,
          tools=[cg_search, cg_explore, cg_impact, context7_tool, mem_store, mem_read],
          verbose=True, max_iter=5),
    Agent(role="Code Reviewer & Security Auditor",
          goal="Review for bugs, security, spec deviations. codegraph_impact before approving. Rate CRITICAL/HIGH/MEDIUM/LOW.",
          backstory="Security-focused. Checks injection, race conditions, API misuse.",
          llm=LLM_T1_REASONING,
          tools=[cg_search, cg_explore, cg_impact, mem_read],
          verbose=True, max_iter=3),
    Agent(role="Test Engineer",
          goal="Write tests for 80%+ coverage. Use codegraph_explore to find all code paths.",
          backstory="Tests catch real bugs, not bugs that pass by construction.",
          llm=LLM_T3_BULK,
          tools=[cg_search, cg_explore, mem_store],
          verbose=True, max_iter=3),
], process=Process.sequential, verbose=True)

# ── Trading ───────────────────────────────────────────────────────────────────
trading_crew = Crew(agents=[
    Agent(role="XAUUSD Signal Analyst",
          goal="Analyse FusionModel signal. CCC 0.0691 at epoch 20 = correct early convergence.",
          backstory="Quant analyst, XAUUSD 15M/1H. HMM-CNN-BiLSTM-ATTN-MC-SHAP architecture.",
          llm=LLM_T2_RESEARCH,
          tools=[sig_tool, mem_store, mem_read, searxng_tool],
          verbose=True, max_iter=3),
    Agent(role="Risk Manager",
          goal="Kelly Criterion, 1:2 R:R minimum. Never approve without validated SL.",
          backstory="Survived multiple market crises.",
          llm=LLM_T1_REASONING,
          tools=[mem_store, mem_read],
          verbose=True, max_iter=2),
    Agent(role="Trading Journal Keeper",
          goal="Log every signal and outcome in MCP memory thread 'trading-journal'.",
          backstory="Timestamp, signal, risk verdict, entry, SL/TP, outcome.",
          llm=LLM_T3_BULK,
          tools=[mem_store],
          verbose=True, max_iter=2),
], process=Process.sequential, verbose=True)

# ── Audit (ASGL-A, FRS102) ────────────────────────────────────────────────────
_at = [mem_store, mem_read, rag_search_tool, rag_store_tool] + ASGL_AUDIT_TOOLS
audit_crew = Crew(agents=[
    Agent(role="Anomaly Detector",
          goal="Identify numeric anomalies in ASGL-A diff rows above 5% materiality.",
          backstory="Financial data quality analyst. WP vs CT600 vs statutory accounts.",
          llm=LLM_T3_BULK,
          tools=_at,
          verbose=True, max_iter=5),
    Agent(role="Compliance Monitor (FRS102/FRS105)",
          goal=(
              "Review ASGL-A engagement for FRS102/FRS105 compliance. "
              "Create review points ONLY for new issues. "
              "Flag going concern, related party, tax at HIGH severity."
          ),
          backstory="UK Chartered Accountant. Professional judgment. Never fabricates data.",
          llm=LLM_T1_REASONING,
          tools=_at,
          verbose=True, max_iter=8),
], process=Process.sequential, verbose=True)

# ── Oversight (meta-layer) ────────────────────────────────────────────────────
_ot = [mem_store, mem_read, rag_search_tool, focus_tool] + ASGL_OVERSIGHT_TOOLS
oversight_crew = Crew(agents=[
    Agent(role="Meta Reviewer & System Improver",
          goal=(
              "Review all crew outputs and ASGL-A engagement histories. "
              "Read 'oversight-meta-review' memory first. "
              "Identify systematic errors. Store findings back to memory. "
              "Use compress_context when reviewing many engagements."
          ),
          backstory="System architect for Oversight Labs. System-level thinking, not engagement-level.",
          llm=LLM_T1_REASONING,
          tools=_ot,
          verbose=True, max_iter=6),
], process=Process.sequential, verbose=True)

# ── SEO ───────────────────────────────────────────────────────────────────────
seo_crew = Crew(agents=[
    Agent(role="SEO Analyst",
          goal=(
              "Crawl with Screaming Frog. Fetch competitor pages with web_fetch. "
              "Search competitor rankings with web_search. "
              "Identify: broken links, missing titles, H1s, duplicate content, redirect chains."
          ),
          backstory="SEO specialist, 10yr experience. Combines crawl data with competitor research.",
          llm=LLM_T2_RESEARCH,
          tools=[sf_tool, crawl4ai_tool, searxng_tool, playwright_tool, mem_store],
          verbose=True, max_iter=3),
    Agent(role="SEO Strategist",
          goal="90-day action plan from crawl findings. Validate keyword opportunities with web_search.",
          backstory="Growth strategist, B2B SaaS and professional services SEO.",
          llm=LLM_T2_RESEARCH,
          tools=[searxng_tool, mem_read, mem_store],
          verbose=True, max_iter=3),
], process=Process.sequential, verbose=True)

CREW_MAP: Dict[str, Crew] = {
    "research":  research_crew,
    "coding":    coding_crew,
    "trading":   trading_crew,
    "audit":     audit_crew,
    "oversight": oversight_crew,
    "seo":       seo_crew,
}

# ── OWAY Crew (additive) ──────────────────────────────────────────────────────
try:
    from oway.oway_crew_integration import (
        CREW_MAP_OWAY, register_oway_routes, oway_crew
    )
    CREW_MAP.update(CREW_MAP_OWAY)
    LOG.info("OWAY crew: loaded — %d agents", len(oway_crew.agents) if oway_crew else 0)
    _OWAY_AVAILABLE = True
except ImportError as _e:
    LOG.info("OWAY crew: not found (%s) — run oway_wiring.py first", _e)
    _OWAY_AVAILABLE = False

# Update CREW_TIERS with OWAY
CREW_TIERS["oway"] = "T1-Reasoning + T2-Research + Browserbase + OWAY tools"

# ── Extended Crews (compliance, sales, finint + 20 new tools) ─────────────────
try:
    from oway.extended_crew_tools import (
        EXTENDED_CREW_MAP, EXTENDED_CREW_TIERS,
        register_extended_routes, EXTENDED_TOOLS
    )
    CREW_MAP.update(EXTENDED_CREW_MAP)
    CREW_TIERS.update(EXTENDED_CREW_TIERS)
    LOG.info("Extended crews loaded: %s | %d tools",
             list(EXTENDED_CREW_MAP.keys()), len(EXTENDED_TOOLS))
    _EXTENDED_AVAILABLE = True
except ImportError as _e:
    LOG.info("Extended crews: not found (%s)", _e)
    _EXTENDED_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# FLASK API
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

# Safety defaults in case import try blocks above failed silently
_OWAY_AVAILABLE     = globals().get("_OWAY_AVAILABLE",     False)
_EXTENDED_AVAILABLE = globals().get("_EXTENDED_AVAILABLE", False)

# Register OWAY routes if available
try:
    if _OWAY_AVAILABLE:
        from oway.oway_crew_integration import register_oway_routes
        register_oway_routes(app)
        LOG.info("OWAY routes registered: /oway-dispatch, /oway-status")
    if _EXTENDED_AVAILABLE:
        from oway.extended_crew_tools import register_extended_routes
        register_extended_routes(app)
        LOG.info("Extended routes registered: /extended-tools, /extended-crews")
    # Security Overwatch + Governance Council
    try:
        from oway.security_overwatch import (
            OVERWATCH_CREW_MAP, OVERWATCH_CREW_TIER,
            OVERWATCH_DISPATCH_KW, register_overwatch_routes
        )
        CREW_MAP.update(OVERWATCH_CREW_MAP)
        CREW_TIERS.update(OVERWATCH_CREW_TIER)
        register_overwatch_routes(app)
        LOG.info("Security Overwatch + Governance Council: loaded")
    except ImportError as _e:
        LOG.info("Security Overwatch: not found (%s)", _e)

    # OSINT Crew v2
    try:
        from oway.osint_crew import (
            OSINT_CREW_MAP, OSINT_CREW_TIER,
            OSINT_DISPATCH_KW, register_osint_routes
        )
        CREW_MAP.update(OSINT_CREW_MAP)
        CREW_TIERS.update(OSINT_CREW_TIER)
        register_osint_routes(app)
        LOG.info("OSINT crew v2: loaded — 12 sources")
    except ImportError as _e:
        LOG.info("OSINT crew: not found (%s)", _e)

    # Extended Crews v3 (50 new APIs, 8 crews)
    try:
        from oway.extended_crew_tools_v3 import (
            V3_CREW_MAP, V3_CREW_TIERS, V3_DISPATCH_KW, register_v3_routes
        )
        CREW_MAP.update(V3_CREW_MAP)
        CREW_TIERS.update(V3_CREW_TIERS)
        register_v3_routes(app)
        LOG.info("Extended v3 crews: %s", list(V3_CREW_MAP.keys()))
    except ImportError as _e:
        LOG.info("Extended v3: not found (%s)", _e)

    # VM Red vs Blue Arena (Iron Dome adversarial testing)
    try:
        from oway.vm_red_vs_blue import register_arena_routes, arena_tool
        register_arena_routes(app)
        # Inject arena_tool into overwatch Detection Engineer agent
        if arena_tool and "overwatch" in CREW_MAP and CREW_MAP["overwatch"]:
            for _agent in CREW_MAP["overwatch"].agents:
                if _agent.role == "Detection Engineer":
                    if arena_tool not in _agent.tools:
                        _agent.tools.append(arena_tool)
                        LOG.info("Arena tool: injected into Detection Engineer")
        LOG.info("Red vs Blue Arena: 14 attack types, fully isolated, no external access")
    except ImportError as _e:
        LOG.info("Arena: not found (%s) — run oway_wiring.py first", _e)
except Exception as _e:
    LOG.debug("OWAY route registration: %s", _e)

@app.after_request
def cors(r: Response) -> Response:
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return r


@app.route("/health")
def health():
    svcs = _svc_status()
    return jsonify({
        "status":    "ok",
        "version":   "6.0.0-hybrid",
        "crews":     list(CREW_MAP.keys()),
        # Compression
        "tokenjuice":   True,
        "contextpilot": _CP_AVAILABLE and _CP_ENABLED,
        "headroom":     _HR_AVAILABLE and _HR_ENABLED,
        "llmlingua2":   _LINGUA_AVAILABLE,
        "headroom_stats": {
            "calls":        _hr_stats["calls"],
            "tokens_saved": max(0, _hr_stats["tokens_in"] - _hr_stats["tokens_out"]),
            "saving_pct":   round((1 - _hr_stats["chars_out"] / max(_hr_stats["chars_in"], 1)) * 100, 1),
        },
        # Memory
        "kioku":        _KIOKU_AVAILABLE,
        "chromadb":     _RAG_AVAILABLE,
        "network_ai":   svcs["network_ai"],
        # Tool services
        "searxng":      svcs["searxng"],
        "crawl4ai":     svcs["crawl4ai"],
        "playwright":   svcs["playwright"],
        "mem0":         svcs["mem0"],
        "asgl_a":       svcs["asgl_a"],
        "freellmapi":   svcs["freellmapi"],
        "codegraph":    svcs["codegraph"],
        "browserbase":  svcs.get("browserbase", False),
        "oway_crew":    _OWAY_AVAILABLE,
        # Keys
        "api_keys":     {k: v for k, v in _keys.items()},
        "port":         CREWAI_PORT,
        # Hybrid systems
        "hdc_memory":   {"labels": len(_hdc_labels), "dim": _HDC_DIM, "decay_rate": _HDC_DECAY},
        "fep_inference":{"crews": len(_fep_alpha),
                         "confidences": {k: round(v[0]/sum(v), 3)
                                         for k, v in _fep_alpha.items()}},
        "cognitive_state": _cog_state,
        "bg_threads":   {"decay": _DECAY_INTERVAL, "dream": _DREAM_INTERVAL,
                         "health": _HEALTH_INTERVAL},
        "arena":        _svc_ok("http://localhost:5001/arena/history", timeout=1.0),
    })


@app.route("/crews")
def list_crews():
    return jsonify({
        name: {
            "agents": [a.role for a in crew.agents],
            "tier":   CREW_TIERS.get(name, "?"),
            "agent_count": len(crew.agents),
        }
        for name, crew in CREW_MAP.items()
    })


@app.route("/cogstate")
def cogstate():
    """Current cognitive state and recent window."""
    with _cog_lock:
        state = dict(_cog_state)
    state["window_size"]     = len(_cog_window)
    state["window_successes"] = sum(_cog_window)
    state["hdc_labels"]      = len(_hdc_labels)
    return jsonify(state)


@app.route("/fep")
def fep_state():
    """FEP active inference — crew belief distributions."""
    with _fep_lock:
        alpha = dict(_fep_alpha)
    result = {}
    for crew, (s, f) in alpha.items():
        total = s + f
        result[crew] = {
            "success_rate":  round(s / total, 3),
            "alpha_success": round(s, 1),
            "alpha_failure": round(f, 1),
            "efe":           round(fep_expected_free_energy(crew), 4),
        }
    return jsonify(result)


@app.route("/hdc/query", methods=["POST"])
def hdc_query_endpoint():
    """Query HDC memory directly."""
    data  = request.get_json(force=True, silent=True) or {}
    query = data.get("query", "")
    top_k = int(data.get("top_k", 5))
    if not query:
        return jsonify({"error": "query required"}), 400
    return jsonify({"results": hdc_query(query, top_k=top_k)})


def _kickoff(crew_name: str, prompt: str) -> Dict[str, Any]:
    """
    Run a crew task. Returns result dict.
    FIX-LOGIC: Use the full registered crew (all agents) not just the lead agent.
    The lead agent is used for task assignment but all agents participate.
    """
    crew_obj = CREW_MAP[crew_name]
    lead     = crew_obj.agents[0]
    # Rebuild with fresh task — CrewAI requires task injection at runtime
    task = Task(
        description=prompt,
        expected_output="Detailed, structured response directly addressing the request.",
        agent=lead,
    )
    # Use the full crew with all its agents — only task list is replaced
    import copy as _copy
    run_crew = Crew(
        agents   = crew_obj.agents,
        tasks    = [task],
        process  = crew_obj.process,
        verbose  = True,
    )
    start = time.time()
    result = run_crew.kickoff()
    elapsed = time.time() - start
    result_str = str(result)
    rag_store_agent_output(crew_name, prompt, result_str)
    # Hybrid: HDC encode + FEP + CogState update
    rag_store_hdc(crew_name, result_str, label_suffix=crew_name)
    fep_update(crew_name, success=True)
    novelty = min(1.0, max(0.0, 1.0 - (len(rag_search_hdc(crew_name, prompt)) / 5.0)))
    cog_update(success=True, novelty=novelty)
    compressed = tokenjuice(f"crew_{crew_name}", result_str)
    return {
        "crew": crew_name, "result": compressed, "status": "success",
        "elapsed": round(elapsed, 2), "raw_len": len(result_str),
        "compressed_len": len(compressed), "tier": CREW_TIERS.get(crew_name, "?"),
        "cog_mode": _cog_state.get("mode", "UNKNOWN"),
        "fep_confidence": round(_fep_alpha.get(crew_name, [1.0, 1.0])[0] /
                                 sum(_fep_alpha.get(crew_name, [1.0, 1.0])), 3),
        "hdc_matches": len(rag_search_hdc(crew_name, prompt[:200])),
    }


@app.route("/run/<crew_name>", methods=["POST", "OPTIONS"])
def run_crew(crew_name: str):
    if request.method == "OPTIONS":
        return "", 204
    if crew_name not in CREW_MAP:
        return jsonify({"error": f"Unknown crew: {crew_name}",
                        "available": list(CREW_MAP.keys())}), 404
    data   = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    try:
        LOG.info("crew=%s", crew_name)
        return jsonify(_kickoff(crew_name, prompt))
    except Exception as e:
        LOG.error("crew=%s failed: %s", crew_name, e, exc_info=True)
        fep_update(crew_name, success=False)   # FEP learns from failure
        cog_update(success=False, novelty=0.5) # CogState registers error
        return jsonify({
            "crew":     crew_name,
            "error":    str(e),
            "status":   "error",
            "cog_mode": _cog_state.get("mode", "UNKNOWN"),
            "fep_confidence": round(
                _fep_alpha.get(crew_name, [1.0, 1.0])[0] /
                sum(_fep_alpha.get(crew_name, [1.0, 1.0])), 3
            ),
        }), 500


# FIX-5: /dispatch — intelligent router endpoint
# Classifies the prompt and routes to the most appropriate crew automatically.
@app.route("/dispatch", methods=["POST", "OPTIONS"])
def dispatch():
    """
    Intelligent prompt router. POST {"prompt": "..."} → best crew automatically.

    Classification rules (keyword + pattern matching, then LLM triage for ambiguous):
      coding    — code, function, bug, implement, debug, class, API, python
      audit     — FRS102, FRS105, review point, engagement, discrepancy, accounts
      research  — research, find, summarise, explain, what is, latest, news
      trading   — XAUUSD, gold, signal, trade, BUY, SELL, FusionModel
      oversight — pattern, systematic, meta, improve, oversight
      seo       — SEO, crawl, website, backlink, ranking, keyword
    """
    if request.method == "OPTIONS":
        return "", 204
    data   = request.get_json(force=True, silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    # Fast keyword classification
    p_lower = prompt.lower()
    crew_name = "research"  # default

    coding_kw    = ["code", "function", "bug", "implement", "debug", "class", "python",
                    "fastapi", "crewai", "script", "error", "traceback", "import", "def "]
    audit_kw     = ["frs102", "frs105", "review point", "engagement", "discrepancy",
                    "accounts", "ct600", "trial balance", "going concern", "dla", "dividend"]
    trading_kw   = ["xauusd", "gold", "signal", "trade", "buy", "sell", "fusionmodel",
                    "xau", "price", "chart"]
    oversight_kw = ["systematic", "pattern", "meta", "oversight", "improve system",
                    "cross-engagement"]
    oway_kw        = ["oway", "schema", "workflow automation", "task schema",
                      "source map", "confidence gate", "field completion",
                      "companies house", "hmrc portal", "client portal"]
    compliance_kw  = ["due diligence", "kyc", "aml", "sanctions", "pep",
                      "companies house", "fca", "opencorporates", "land registry",
                      "directors", "filings", "authorised", "regulated"]
    sales_kw       = ["outreach", "lead", "prospect", "cfo", "finance director",
                      "apollo", "hunter", "linkedin", "email finder", "enrichment",
                      "b2b", "target company", "sales"]
    finint_kw      = ["market data", "stock price", "crypto", "bitcoin", "coingecko",
                      "alpha vantage", "figi", "isin", "instrument", "market cap",
                      "sentiment", "financial news"]
    social_kw      = ["social media", "twitter", "linkedin", "post", "schedule",
                      "brand mention", "hashtag", "content", "engagement"]
    website_kw     = ["website", "pagespeed", "seo audit", "core web vitals",
                      "backlink", "domain authority", "traffic", "moz"]
    competitor_kw  = ["competitor", "competitive", "market share", "swot",
                      "similarweb", "tech stack", "market position"]
    hr_kw          = ["recruit", "candidate", "job posting", "hire", "talent",
                      "onboarding", "hr", "salary", "linkedin profile"]
    ops_kw         = ["jira", "github issue", "ops", "sprint", "ticket",
                      "slack", "notify team", "project tracking"]
    rd_kw          = ["arxiv", "research paper", "sota", "huggingface",
                      "patent", "r&d", "model search", "literature review"]
    seo_kw       = ["seo", "crawl", "website", "backlink", "ranking", "keyword", "google",
                    "search engine", "pages indexed"]

    scores = {
        "coding":    sum(1 for kw in coding_kw    if kw in p_lower),
        "audit":     sum(1 for kw in audit_kw     if kw in p_lower),
        "trading":   sum(1 for kw in trading_kw   if kw in p_lower),
        "oversight": sum(1 for kw in oversight_kw if kw in p_lower),
        "seo":       sum(1 for kw in seo_kw       if kw in p_lower),
        "oway":       sum(1 for kw in oway_kw       if kw in p_lower),
        "compliance": sum(1 for kw in compliance_kw if kw in p_lower),
        "sales":      sum(1 for kw in sales_kw      if kw in p_lower),
        "finint":     sum(1 for kw in finint_kw     if kw in p_lower),
        "social":     sum(1 for kw in social_kw     if kw in p_lower),
        "website":    sum(1 for kw in website_kw    if kw in p_lower),
        "competitor": sum(1 for kw in competitor_kw if kw in p_lower),
        "hr":         sum(1 for kw in hr_kw         if kw in p_lower),
        "ops":        sum(1 for kw in ops_kw        if kw in p_lower),
        "rd":         sum(1 for kw in rd_kw         if kw in p_lower),
        "overwatch":  sum(1 for kw in [
                          "security","threat","attack","intrusion","cve",
                          "iron dome","retrain","council","near miss","overwatch",
                          "sigma rule","mitre","pen test","detection gap"
                      ] if kw in p_lower),
        "osint":      sum(1 for kw in [
                          "osint","passive intel","ip lookup","domain intel",
                          "threat intel","indicator","ioc","malware check",
                          "phishing","reputation","whois","subdomain",
                          "wayback","virustotal","threatfox","greynoise"
                      ] if kw in p_lower),
        "media":      sum(1 for kw in ["youtube","video","image","stock photo",
                          "audio","tts","content","unsplash","pexels"] if kw in p_lower),
        "docs":       sum(1 for kw in ["invoice ocr","google sheets","google docs",
                          "filed accounts","ocr","document extract"] if kw in p_lower),
        "comms":      sum(1 for kw in ["calendly","mailchimp","newsletter",
                          "support ticket","freshdesk","meeting schedule"] if kw in p_lower),
        "geo":        sum(1 for kw in ["postcode","uk address","ordnance survey",
                          "location","geographic"] if kw in p_lower),
        "finops":     sum(1 for kw in ["stripe","xero","revenue","insolvency",
                          "cash position","invoice outstanding"] if kw in p_lower),
        "legal":      sum(1 for kw in ["legislation","act of parliament","ico",
                          "charity","data protection law","legal research"] if kw in p_lower),
        "nlp":        sum(1 for kw in ["translate","sentiment","named entity",
                          "summarise text","deepl","language"] if kw in p_lower),
        "market":     sum(1 for kw in ["trustpilot","reviews","price comparison",
                          "beauhurst","startup intelligence"] if kw in p_lower),
        # v3 crews
        "media":      sum(1 for kw in ["youtube","video","image","stock photo",
                          "audio","tts","content","unsplash","pexels"] if kw in p_lower),
        "docs":       sum(1 for kw in ["invoice ocr","google sheets","google docs",
                          "filed accounts","ocr","document extract"] if kw in p_lower),
        "comms":      sum(1 for kw in ["calendly","mailchimp","newsletter",
                          "support ticket","freshdesk","meeting schedule"] if kw in p_lower),
        "geo":        sum(1 for kw in ["postcode","uk address","ordnance survey",
                          "location","geographic"] if kw in p_lower),
        "finops":     sum(1 for kw in ["stripe","xero","revenue","insolvency",
                          "cash position","invoice outstanding"] if kw in p_lower),
        "legal":      sum(1 for kw in ["legislation","act of parliament","ico",
                          "charity","data protection law","legal research"] if kw in p_lower),
        "nlp":        sum(1 for kw in ["translate","sentiment","named entity",
                          "summarise text","deepl","language"] if kw in p_lower),
        # security + osint + arena
        "overwatch":  sum(1 for kw in ["security","threat","attack","intrusion","cve",
                          "iron dome","retrain","council","near miss","overwatch",
                          "sigma rule","mitre","pen test","detection gap",
                          "red team","blue team","arena","evasion",
                          "detection rate","conviction threshold"] if kw in p_lower),
        "osint":      sum(1 for kw in ["osint","passive intel","ip lookup","domain intel",
                          "threat intel","indicator","ioc","malware check",
                          "phishing","reputation","whois","subdomain",
                          "wayback","virustotal","threatfox","greynoise"] if kw in p_lower),
        "research":   1,  # default weight
    }
    # FEP Active Inference: combine keyword scores with belief-weighted EFE
    crew_name = fep_select_crew(scores, fallback="research")
    LOG.info("FEP routing: selected %s via EFE minimisation", crew_name)

    # Override: if a specific crew is forced
    if data.get("crew") and data["crew"] in CREW_MAP:
        crew_name = data["crew"]

    LOG.info("dispatch: '%s...' → %s (scores=%s)", prompt[:60], crew_name,
             {k: v for k, v in scores.items() if v > 0})

    try:
        result = _kickoff(crew_name, prompt)
        result["dispatched_to"] = crew_name
        result["classification_scores"] = scores
        return jsonify(result)
    except Exception as e:
        LOG.error("dispatch failed: %s", e, exc_info=True)
        return jsonify({"error": str(e), "status": "error", "crew": crew_name}), 500



# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND THREADS — Decay · Dream Consolidation · Health Monitor
# All run as daemon threads; they die cleanly when the main process exits.
# ══════════════════════════════════════════════════════════════════════════════
import threading as _bg_threading

# ── Thread: Memory Decay ──────────────────────────────────────────────────────
# Every _DECAY_INTERVAL seconds:
#   1. Apply HDC multiplicative decay
#   2. Prune ChromaDB entries older than _CHROMA_MAX_AGE (soft cap)
#   3. Save HDC memory to disk
#   4. Trigger REST cognitive mode if no recent activity
_DECAY_INTERVAL  = int(os.environ.get("DECAY_INTERVAL_SEC",  str(5 * 60)))   # 5 min
_CHROMA_MAX_DOCS = int(os.environ.get("CHROMA_MAX_DOCS",     "5000"))

def _decay_loop() -> None:
    LOG.info("BG[decay]: started (interval=%ds)", _DECAY_INTERVAL)
    while True:
        time.sleep(_DECAY_INTERVAL)
        try:
            # 1. HDC decay
            hdc_decay(_HDC_DECAY)
            hdc_save()
            # 2. Soft-prune ChromaDB: if any collection exceeds _CHROMA_MAX_DOCS,
            #    delete the oldest 10% by timestamp metadata
            if _RAG_AVAILABLE and _rag_client:
                try:
                    for col_name in [c.name for c in _rag_client.list_collections()]:
                        col = _rag_client.get_collection(col_name)
                        cnt = col.count()
                        if cnt > _CHROMA_MAX_DOCS:
                            trim = cnt - int(_CHROMA_MAX_DOCS * 0.9)
                            res  = col.get(limit=trim, include=["metadatas"])
                            ids  = res.get("ids", [])
                            if ids:
                                col.delete(ids=ids)
                                LOG.info("BG[decay]: ChromaDB %s pruned %d docs", col_name, len(ids))
                except Exception as _e:
                    LOG.debug("BG[decay]: chroma prune: %s", _e)
            # 3. If cycle count hasn't changed, nudge into REST
            LOG.debug("BG[decay]: HDC decay applied, disk saved")
        except Exception as _e:
            LOG.warning("BG[decay]: %s", _e)


# ── Thread: Dream Consolidation ───────────────────────────────────────────────
# Every _DREAM_INTERVAL seconds (default 30 min):
#   1. Pull recent RAG entries from each domain
#   2. Summarise with T3 bulk LLM → one consolidated memory per domain
#   3. Store consolidated summary back to RAG + HDC
#   4. Transition cognitive mode to CONSOLIDATE during window
_DREAM_INTERVAL = int(os.environ.get("DREAM_INTERVAL_SEC", str(30 * 60)))   # 30 min
_DREAM_DOMAINS  = ["research", "coding", "trading", "audit", "oversight"]

def _dream_loop() -> None:
    LOG.info("BG[dream]: started (interval=%ds)", _DREAM_INTERVAL)
    while True:
        time.sleep(_DREAM_INTERVAL)
        try:
            LOG.info("BG[dream]: consolidation cycle starting")
            prev_mode = _cog_state.get("mode", "EXPLORE")
            # Temporarily set CONSOLIDATE mode
            with _cog_lock:
                _cog_state["mode"] = "CONSOLIDATE"
            for domain in _DREAM_DOMAINS:
                try:
                    # Retrieve recent memories
                    recent = rag_search(domain, "recent findings summary", n=8)
                    if not recent or "(No" in recent:
                        continue
                    # Summarise
                    summary = llm_complete(
                        f"Summarise these {domain} memory fragments into a single coherent "
                        f"paragraph capturing the most important patterns and facts. "
                        f"Be concise (max 150 words).\n\n{recent[:4000]}",
                        max_tokens=256,
                    )
                    if summary and len(summary) > 50:
                        label = f"dream_consolidation_{domain}_{int(time.time())}"
                        rag_store(domain, summary,
                                  {"source": "dream_consolidation", "domain": domain})
                        rag_store_hdc(domain, summary, label_suffix="dream")
                        LOG.info("BG[dream]: consolidated domain=%s (%d chars)", domain, len(summary))
                except Exception as _e:
                    LOG.debug("BG[dream]: domain=%s %s", domain, _e)
            # Restore cognitive mode
            with _cog_lock:
                _cog_state["mode"] = prev_mode
            cog_save()
            LOG.info("BG[dream]: consolidation cycle complete")
        except Exception as _e:
            LOG.warning("BG[dream]: %s", _e)


# ── Thread: Health Monitor ────────────────────────────────────────────────────
# Every _HEALTH_INTERVAL seconds:
#   1. Check all services via _svc_status()
#   2. Count degraded services and log a summary
#   3. If >50% services down → set ALERT cognitive mode
#   4. Write health snapshot to .health_snapshot.json for external readers
_HEALTH_INTERVAL  = int(os.environ.get("HEALTH_INTERVAL_SEC", "60"))
_HEALTH_SNAP_PATH = Path(os.environ.get("HEALTH_SNAP_PATH", str(_base / ".health_snapshot.json")))

def _health_loop() -> None:
    LOG.info("BG[health]: started (interval=%ds)", _HEALTH_INTERVAL)
    while True:
        time.sleep(_HEALTH_INTERVAL)
        try:
            svcs = _svc_status()
            total = len(svcs); up = sum(svcs.values())
            frac_up = up / max(total, 1)
            status_line = " | ".join(
                f"{'✓' if v else '✗'}{k}" for k, v in sorted(svcs.items())
            )
            LOG.info("BG[health]: %d/%d up | %s", up, total, status_line)
            # Alert escalation
            if frac_up < 0.5:
                with _cog_lock:
                    _cog_state["mode"] = "ALERT"
                LOG.warning("BG[health]: >50%% services down → ALERT mode")
            # Snapshot for external monitoring
            snapshot = {
                "ts":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "services": svcs,
                "up":       up, "total": total,
                "frac_up":  round(frac_up, 3),
                "cog_mode": _cog_state.get("mode", "UNKNOWN"),
                "hdc_labels": len(_hdc_labels),
                "fep_crews":  len(_fep_alpha),
            }
            _HEALTH_SNAP_PATH.write_text(
                _json_bg.dumps(snapshot, indent=2), encoding="utf-8"
            )
        except Exception as _e:
            LOG.warning("BG[health]: %s", _e)


# ── Thread launcher ───────────────────────────────────────────────────────────
def _start_background_threads() -> None:
    for name, target in [
        ("decay",    _decay_loop),
        ("dream",    _dream_loop),
        ("health",   _health_loop),
    ]:
        t = _bg_threading.Thread(target=target, name=f"bg-{name}", daemon=True)
        t.start()
        LOG.info("BG[%s]: thread started (tid=%d)", name, t.ident or 0)
    LOG.info("Hybrid background threads: decay=%ds dream=%ds health=%ds",
             _DECAY_INTERVAL, _DREAM_INTERVAL, _HEALTH_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════════════
# ASE PROXY ROUTES — All-Seeing Eye v7
# Solves browser CORS restrictions by routing external API calls through the
# orchestrator, which has no CORS limitations. The ASE points all external
# fetches at http://localhost:8001/ase/proxy/* instead of the source APIs.
# ═══════════════════════════════════════════════════════════════════════════════

_PROXY_TIMEOUT = 12  # seconds per upstream request

def _proxy_get(url: str, params: dict = None, headers: dict = None) -> dict:
    """GET a URL, return parsed JSON or error dict."""
    try:
        r = _req.get(url, params=params, headers=headers, timeout=_PROXY_TIMEOUT)
        if r.ok:
            return r.json()
        return {"error": f"upstream {r.status_code}", "url": url}
    except Exception as e:
        return {"error": str(e), "url": url}


@app.route("/ase/proxy/opensky", methods=["GET", "OPTIONS"])
def ase_proxy_opensky():
    """OpenSky Network ADS-B states — live flight positions."""
    if request.method == "OPTIONS": return "", 204
    q = {k: request.args.get(k) for k in ["lamin","lomin","lamax","lomax"] if request.args.get(k)}
    data = _proxy_get("https://opensky-network.org/api/states/all", params=q)
    return jsonify(data)


@app.route("/ase/proxy/gdelt/geo", methods=["GET", "OPTIONS"])
def ase_proxy_gdelt_geo():
    """GDELT GEO API — geo-coded event points."""
    if request.method == "OPTIONS": return "", 204
    q = dict(request.args)
    data = _proxy_get("https://api.gdeltproject.org/api/v2/geo/geo", params=q)
    return jsonify(data)


@app.route("/ase/proxy/gdelt/doc", methods=["GET", "OPTIONS"])
def ase_proxy_gdelt_doc():
    """GDELT DOC API — article list with tone."""
    if request.method == "OPTIONS": return "", 204
    q = dict(request.args)
    data = _proxy_get("https://api.gdeltproject.org/api/v2/doc/doc", params=q)
    return jsonify(data)


@app.route("/ase/proxy/vessels", methods=["GET", "OPTIONS"])
def ase_proxy_vessels():
    """VesselTracker AIS data."""
    if request.method == "OPTIONS": return "", 204
    q = dict(request.args)
    data = _proxy_get("https://www.vesseltracker.com/ws/api/vessels", params=q)
    return jsonify(data)


@app.route("/ase/proxy/windy", methods=["GET", "OPTIONS"])
def ase_proxy_windy():
    """Windy webcams near a location."""
    if request.method == "OPTIONS": return "", 204
    lat  = request.args.get("lat", "51.5")
    lng  = request.args.get("lng", "-0.1")
    rad  = request.args.get("radius", "100")
    url  = f"https://api.windy.com/api/webcams/v2/list/nearby/{lat},{lng},{rad}/fields=webcams;title;status;image;player;location"
    data = _proxy_get(url)
    return jsonify(data)


@app.route("/ase/proxy/overpass", methods=["POST", "OPTIONS"])
def ase_proxy_overpass():
    """OpenStreetMap Overpass API — webcam nodes."""
    if request.method == "OPTIONS": return "", 204
    body = request.get_json(force=True, silent=True) or {}
    query = body.get("query", "")
    try:
        r = _req.get(
            "https://overpass-api.de/api/interpreter",
            params={"data": query},
            timeout=_PROXY_TIMEOUT,
        )
        return jsonify(r.json() if r.ok else {"error": f"upstream {r.status_code}"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/ase/proxy/tfl", methods=["GET", "OPTIONS"])
def ase_proxy_tfl():
    """TfL JamCam positions — London traffic cameras."""
    if request.method == "OPTIONS": return "", 204
    data = _proxy_get("https://api.tfl.gov.uk/Place/Type/JamCam")
    return jsonify(data)


@app.route("/ase/proxy/polymarket", methods=["GET", "OPTIONS"])
def ase_proxy_polymarket():
    """Polymarket active prediction markets."""
    if request.method == "OPTIONS": return "", 204
    q = request.args.get("q", "conflict")
    limit = request.args.get("limit", "6")
    data = _proxy_get(
        "https://gamma-api.polymarket.com/markets",
        params={"searchQuery": q, "limit": limit, "active": "true"},
    )
    return jsonify(data)


@app.route("/ase/proxy/coingecko", methods=["GET", "OPTIONS"])
def ase_proxy_coingecko():
    """CoinGecko crypto prices."""
    if request.method == "OPTIONS": return "", 204
    ids  = request.args.get("ids", "bitcoin,ethereum")
    data = _proxy_get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
    )
    return jsonify(data)


@app.route("/ase/proxy/alphavantage", methods=["GET", "OPTIONS"])
def ase_proxy_alphavantage():
    """Alpha Vantage global quote — equities/ETFs."""
    if request.method == "OPTIONS": return "", 204
    sym = request.args.get("symbol", "SPY")
    key = request.args.get("apikey", "")
    if not key:
        return jsonify({"error": "apikey required"})
    data = _proxy_get(
        "https://www.alphavantage.co/query",
        params={"function": "GLOBAL_QUOTE", "symbol": sym, "apikey": key},
    )
    return jsonify(data)


@app.route("/ase/proxy/ipapi", methods=["GET", "OPTIONS"])
def ase_proxy_ipapi():
    """IP geolocation + threat detection."""
    if request.method == "OPTIONS": return "", 204
    data = _proxy_get("https://ipapi.co/json/")
    return jsonify(data)


@app.route("/ase/proxy/reddit", methods=["GET", "OPTIONS"])
def ase_proxy_reddit():
    """Reddit search — geopolitics or worldnews subreddit."""
    if request.method == "OPTIONS": return "", 204
    q   = request.args.get("q", "")
    sub = request.args.get("sub", "worldnews")
    data = _proxy_get(
        f"https://www.reddit.com/r/{sub}/search.json",
        params={"q": q, "sort": "new", "limit": "8", "restrict_sr": "on"},
        headers={"User-Agent": "OversightASE/1.0"},
    )
    return jsonify(data)


@app.route("/ase/proxy/newsapi", methods=["GET", "OPTIONS"])
def ase_proxy_newsapi():
    """NewsAPI article search."""
    if request.method == "OPTIONS": return "", 204
    q   = request.args.get("q", "")
    key = request.args.get("apiKey", "")
    if not key:
        return jsonify({"error": "apiKey required"})
    data = _proxy_get(
        "https://newsapi.org/v2/everything",
        params={"q": q, "sortBy": "publishedAt", "pageSize": "6", "apiKey": key},
    )
    return jsonify(data)


@app.route("/ase/proxy/shodan", methods=["GET", "OPTIONS"])
def ase_proxy_shodan():
    """Shodan host search — exposed cameras and devices."""
    if request.method == "OPTIONS": return "", 204
    q   = request.args.get("query", "")
    key = request.args.get("key", "")
    if not key:
        return jsonify({"error": "Shodan key required"})
    data = _proxy_get(
        "https://api.shodan.io/shodan/host/search",
        params={"key": key, "query": q, "page": "1"},
    )
    return jsonify(data)


# ── FLYWHEEL INGEST ────────────────────────────────────────────────────────────
# The ASE pushes every significant signal here. The orchestrator stores it in
# HDC memory, updates FEP beliefs, and returns the crew it would route to.
# This makes the live feed a continuous training stream for the organism.

@app.route("/ase/ingest", methods=["POST", "OPTIONS"])
def ase_ingest():
    """
    Receive a live signal from the ASE and feed it into the organism.

    Body: {
        "type":     "military|ais_dark|market|gdelt_crit|social_brk|shodan_vuln|...",
        "priority": 1-5,
        "content":  "signal text / summary",
        "source":   "gdelt|opensky|ais|shodan|social|market|polymarket",
        "lat":      float | null,
        "lng":      float | null,
        "metadata": {}   // any extra k/v
    }

    Returns: {
        "stored": true,
        "crew_routed": "research|overwatch|finint|...",
        "hdc_label": "...",
        "fep_confidence": 0.xx
    }
    """
    if request.method == "OPTIONS": return "", 204
    body    = request.get_json(force=True, silent=True) or {}
    sig_type = body.get("type", "unknown")
    content  = body.get("content", "")
    source   = body.get("source", "ase")
    priority = int(body.get("priority", 3))
    lat      = body.get("lat")
    lng      = body.get("lng")
    meta     = body.get("metadata", {})

    # Store in HDC vector memory
    hdc_label = f"ase:{sig_type}:{source}"
    try:
        hdc_store(hdc_label, content)  # FIX: label first, text second
    except Exception:
        pass

    # Update FEP belief for source crew
    crew_map = {
        "military":    "overwatch",
        "ais_dark":    "osint",
        "market":      "finint",
        "gdelt_crit":  "research",
        "gdelt_warn":  "research",
        "social_brk":  "social",
        "shodan_vuln": "overwatch",
        "polymarket":  "finint",
        "sentiment":   "research",
        "red_blue":    "overwatch",
    }
    crew = crew_map.get(sig_type, "research")
    try:
        fep_update(crew, success=True)
        cog_update(success=True, novelty=min(1.0, priority / 5.0))
    except Exception:
        pass

    # FEP confidence for this crew
    alpha = _fep_alpha.get(crew, [1.0, 1.0])
    total = alpha[0] + alpha[1]
    confidence = round(alpha[0] / total, 3)

    LOG.info(
        "ASE ingest: type=%s source=%s crew=%s priority=%d lat=%s lng=%s",
        sig_type, source, crew, priority, lat, lng,
    )

    return jsonify({
        "stored":        True,
        "crew_routed":   crew,
        "hdc_label":     hdc_label,
        "fep_confidence": confidence,
        "cog_mode":      _cog_state.get("mode", "EXPLORE"),
    })


@app.route("/ase/ingest/batch", methods=["POST", "OPTIONS"])
def ase_ingest_batch():
    """Batch ingest multiple signals in one call (reduces HTTP overhead)."""
    if request.method == "OPTIONS": return "", 204
    body    = request.get_json(force=True, silent=True) or {}
    signals = body.get("signals", [])
    results = []
    for sig in signals[:50]:  # cap at 50 per batch
        sig_type  = sig.get("type", "unknown")
        content   = sig.get("content", "")
        source    = sig.get("source", "ase")
        priority  = int(sig.get("priority", 3))
        crew_map  = {
            "military":"overwatch","ais_dark":"osint","market":"finint",
            "gdelt_crit":"research","gdelt_warn":"research","social_brk":"social",
            "shodan_vuln":"overwatch","polymarket":"finint","sentiment":"research",
        }
        crew = crew_map.get(sig_type, "research")
        try:
            hdc_store(f"ase:{sig_type}:{source}", content)  # FIX: label first
            fep_update(crew, success=True)
        except Exception:
            pass
        results.append({"type": sig_type, "crew": crew})
    return jsonify({"ingested": len(results), "results": results})


@app.route("/ase/status", methods=["GET", "OPTIONS"])
def ase_status():
    """Quick status endpoint for the ASE health check."""
    if request.method == "OPTIONS": return "", 204
    return jsonify({
        "version":        "7.0",
        "proxy_routes":   [
            "/ase/proxy/opensky", "/ase/proxy/gdelt/geo", "/ase/proxy/gdelt/doc",
            "/ase/proxy/vessels", "/ase/proxy/windy", "/ase/proxy/overpass",
            "/ase/proxy/tfl", "/ase/proxy/polymarket", "/ase/proxy/coingecko",
            "/ase/proxy/alphavantage", "/ase/proxy/ipapi", "/ase/proxy/reddit",
            "/ase/proxy/newsapi", "/ase/proxy/shodan",
        ],
        "ingest_routes":  ["/ase/ingest", "/ase/ingest/batch"],
        "hdc_labels":     len(_hdc_labels),
        "fep_crews":      len(_fep_alpha),
        "cog_mode":       _cog_state.get("mode", "EXPLORE"),
        "compression": {
            "pipeline":     "TokenJuice -> ContextPilot -> Headroom -> LLMLingua-2",
            "headroom_calls":  _hr_stats["calls"],
            "tokens_saved": max(0, _hr_stats["tokens_in"] - _hr_stats["tokens_out"]),
            "saving_pct":   round((1 - _hr_stats["chars_out"] / max(_hr_stats["chars_in"], 1)) * 100, 1),
        },
    })


def _graceful_shutdown(signum, frame) -> None:
    """Save all persistent state on SIGTERM/SIGINT before exit."""
    LOG.info("Shutdown signal %d — saving state...", signum)
    try:
        hdc_save()
        fep_save()
        cog_save()
        LOG.info("State saved: HDC(%d labels) FEP(%d crews) CogState",
                 len(_hdc_labels), len(_fep_alpha))
    except Exception as _e:
        LOG.warning("Shutdown save error: %s", _e)
    import sys as _sys
    _sys.exit(0)


if __name__ == "__main__":
    import signal as _signal
    _signal.signal(_signal.SIGTERM, _graceful_shutdown)
    _signal.signal(_signal.SIGINT,  _graceful_shutdown)

    LOG.info("=" * 60)
    LOG.info("Oversight Labs Orchestrator v6.0 starting on :%d", CREWAI_PORT)
    LOG.info("Keys: %s", [k for k, v in _keys.items() if v])
    LOG.info("TJ=✓ CP=%s LLM2=%s kioku=%s chroma=%s",
             "✓" if _CP_AVAILABLE else "✗",
             "✓" if _LINGUA_AVAILABLE else "✗",
             "✓" if _KIOKU_AVAILABLE else "✗",
             "✓" if _RAG_AVAILABLE else "✗")
    LOG.info("Hybrid: HDC(dim=%d labels=%d) FEP(%d crews) CogState(mode=%s)",
             _HDC_DIM, len(_hdc_labels), len(_fep_alpha), _cog_state.get("mode","?"))
    LOG.info("Crews: %s", list(CREW_MAP.keys()))
    LOG.info("=" * 60)
    _start_background_threads()
    app.run(host="0.0.0.0", port=CREWAI_PORT, debug=False, threaded=True)
