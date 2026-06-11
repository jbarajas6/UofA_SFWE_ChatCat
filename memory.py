# memory.py
#Developed by ChatGPT with prompt requesting to add short-term memory to the CatChat application.
#This is a precondition to other requirements to better understand context and help fill in any
#remaining gaps for the user.
#11/11/2025
import time
from typing import List, Dict, Optional
from flask import session

# --- Short-term memory knobs ---
MEMORY_KEY = "short_term_memory"     # session key
MEMORY_MAX_TURNS = 24                # total messages (user+assistant)
MEMORY_MAX_SECONDS = 600             # 3 minutes
MEMORY_MAX_CHARS = 12000              # coarse cap if no tokenizer

# Only allow memory in these states (string names from ChatState)
MEMORY_ALLOW_STATES = {"default"}
MEMORY_ALLOW_STATES = {"default", "admissions"}  # <-- add "admissions"

def _now() -> float:
    return time.time()

def _ensure_memory():
    if session.get(MEMORY_KEY) is None:
        session[MEMORY_KEY] = []  # list of dicts: {"role": "user"|"assistant", "content": str, "ts": float}

def _prune(mem: List[Dict]) -> List[Dict]:
    # 1) prune by time
    cutoff = _now() - MEMORY_MAX_SECONDS
    mem = [m for m in mem if m["ts"] >= cutoff]

    # 2) prune by turn count
    if len(mem) > MEMORY_MAX_TURNS:
        mem = mem[-MEMORY_MAX_TURNS:]

    # 3) prune by char count
    total = 0
    kept_rev: List[Dict] = []
    for m in reversed(mem):
        total += len(m["content"])
        if total <= MEMORY_MAX_CHARS:
            kept_rev.append(m)
        else:
            break
    return list(reversed(kept_rev))

def add_to_memory(role: str, content: str, state_name: Optional[str] = None):
    """Append the message and prune, but only if current state is allowed."""
    if state_name and state_name not in MEMORY_ALLOW_STATES:
        return
    _ensure_memory()
    mem = session[MEMORY_KEY]
    mem.append({"role": role, "content": content or "", "ts": _now()})
    session[MEMORY_KEY] = _prune(mem)

def get_recent_memory(state_name: Optional[str] = None) -> List[Dict]:
    """Return recent messages as [{'role','content'}] (no timestamps)."""
    if state_name and state_name not in MEMORY_ALLOW_STATES:
        return []
    mem = session.get(MEMORY_KEY, [])
    return [{"role": m["role"], "content": m["content"]} for m in mem]

def clear_memory():
    session[MEMORY_KEY] = []
