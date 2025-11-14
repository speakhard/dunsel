# app/retriever.py
# Loads plain .txt shards and renders a style block for the system prompt.
# Works with minimal headers like:
#   # id: movie_st4_the_voyage_home
#   # source: Star Trek IV – The Voyage Home (1986)
#   # notes: humor, diplomacy
# Body may include QUOTES / SCENE TEXTURE / CADENCE NOTES / VOICE SUMMARY, etc.

from __future__ import annotations
import os, glob, re, time, random, hashlib
from functools import lru_cache
from typing import List, Dict, Any

SHARDS_DIR = os.path.join(os.path.dirname(__file__), "persona", "shards")
HEADER_RE = re.compile(r"^\s*#\s*(\w+)\s*:\s*(.*)\s*$")

def _safe_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def _split_header_body(text: str) -> tuple[Dict[str, str], str]:
    headers: Dict[str, str] = {}
    body_lines: List[str] = []
    in_header = True
    for line in text.splitlines():
        if in_header:
            m = HEADER_RE.match(line)
            if m:
                k, v = m.group(1).strip().lower(), m.group(2).strip()
                headers[k] = v
                continue
            # first non-header line flips to body
            in_header = False
        body_lines.append(line.rstrip())
    body = "\n".join(body_lines).strip()
    return headers, body

def _infer_keywords(h: Dict[str, str]) -> List[str]:
    # Light heuristic so *no tags* still gives us a way to match when tags appear later.
    bag = " ".join(h.get(k, "") for k in ("id", "source", "notes", "type")).lower()
    # Split on non-letters/numbers; keep simple words only
    toks = re.findall(r"[a-z0-9]+", bag)
    # Deduplicate but keep order
    seen = set()
    out = []
    for t in toks:
        if t not in seen:
            seen.add(t); out.append(t)
    return out

def _priority_of(h: Dict[str, str]) -> int:
    # If header provides priority, use it; otherwise default to 10.
    try:
        return int(h.get("priority", "10"))
    except Exception:
        return 10

def _mtime_of(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0

@lru_cache(maxsize=1)
def load_all_shards() -> List[Dict[str, Any]]:
    files = sorted(glob.glob(os.path.join(SHARDS_DIR, "*.txt")))
    shards: List[Dict[str, Any]] = []
    for fp in files:
        text = _safe_read(fp)
        if not text:
            continue
        headers, body = _split_header_body(text)
        if not headers and not body:
            continue
        sid = headers.get("id") or os.path.splitext(os.path.basename(fp))[0]
        shard = {
            "id": sid,
            "path": fp,
            "headers": headers,
            "body": body,
            "priority": _priority_of(headers),
            "keywords": _infer_keywords(headers),
            "mtime": _mtime_of(fp),
        }
        shards.append(shard)
    return shards

def _score_match(shard: Dict[str, Any], want_words: List[str]) -> float:
    # Simple lexical overlap score for when tags/words are provided.
    if not want_words:
        return 0.0
    kw = set(shard.get("keywords", []))
    w  = set(want_words)
    overlap = len(kw & w)
    return overlap + 0.01 * shard["priority"]

def _seed_from(hint: str | None) -> int:
    if not hint:
        return int(time.time())  # still deterministic within a second, fine for fallback
    h = hashlib.sha256(hint.encode("utf-8")).hexdigest()
    # Use 64-bit slice for Python's RNG
    return int(h[:16], 16)

def choose_shards_for_request(
    *, words: List[str] | None = None, k: int = 3, hint: str | None = None
) -> List[Dict[str, Any]]:
    """
    Pick up to k shards. If words provided (e.g., tags or episode names), prefer shards whose
    headers overlap lexically. Otherwise: take a blend of high-priority and recency, seeded by hint.
    """
    corpus = load_all_shards()
    if not corpus:
        return []

    words_norm = [w.lower() for w in (words or []) if w]

    # 1) If we have words, rank by lexical score + priority
    scored = []
    if words_norm:
        for s in corpus:
            scored.append(( _score_match(s, words_norm), s ))
        scored.sort(key=lambda x: (-x[0], -x[1]["priority"], -x[1]["mtime"]))
        picked = [s for _, s in scored[:k]]
        if picked:
            return picked

    # 2) Otherwise, mix top priority and recency, then sample deterministically
    by_priority = sorted(corpus, key=lambda s: (-s["priority"], -s["mtime"]))
    top_pool = by_priority[:max(6, k)]  # small front pool

    rnd = random.Random(_seed_from(hint))
    if len(top_pool) <= k:
        return top_pool

    # Always include the single top-priority shard, sample the rest
    first = top_pool[0:1]
    rest = top_pool[1:]
    sampled = rnd.sample(rest, k - 1)
    return first + sampled

def render_style_block(
    *, words: List[str] | None = None, k: int = 3, hint: str | None = None
) -> str:
    """
    Return the text block to tuck into the system prompt.
    """
    shards = choose_shards_for_request(words=words, k=k, hint=hint)
    out_lines: List[str] = []
    if not shards:
        return ""  # nothing found; safe no-op

    # Gentle nudge so quotes guide cadence without being parroted.
    out_lines.append(
        "The following scene shards are cadence/diction exemplars—emulate voice; do not quote long passages back to the user."
    )
    out_lines.append("")

    for s in shards:
        h = s["headers"]
        title = h.get("id", s["id"])
        source = h.get("source", "")
        notes = h.get("notes", "")
        out_lines.append(f"[shard:{title}]")
        if source: out_lines.append(f"SOURCE: {source}")
        if notes:  out_lines.append(f"NOTES: {notes}")
        out_lines.append(s["body"].strip())
        out_lines.append("")  # spacer

    return "\n".join(out_lines).strip()
