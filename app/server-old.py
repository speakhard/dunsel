# server.py (header)
import os, re
from typing import List, Optional
from pathlib import Path

from fastapi import FastAPI, Body, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from retriever import render_style_block

# -----------------------
# App & static mount (safe)
# -----------------------
app = FastAPI()


STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

INDEX_READY = True   # flip if you wire a real index builder
LAST_BUILD_ERROR: Optional[str] = None

# -----------------------
# Retrieval (safe fallback)
# -----------------------
try:
    # If you already have a retriever module, we use it.
    from retriever import retrieve  # type: ignore
except Exception:
    def retrieve(query: str, tags: List[str], k: int = 8):
        return []  # no shards yet

# -----------------------
# OpenAI client
# -----------------------
def _make_client():
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=key)

_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# -----------------------
# KIRK voice + politics override
# -----------------------
KIRK_BIBLE = """
ROLE: Starfleet captain—decisive, curious, irreverently principled. Solves the unsolvable by reframing limits.
CADENCE: Short, punchy lines. Occasional em dash for dramatic beat. Rhetorical triads. Naval register when commanding.
PERSONA NOTES:
- Command: crisp orders; owns consequences.
- Ethics: humane, anti-authoritarian; liberty with responsibility.
- Diplomacy: pressure with respect; offer exits.
- Play: wry, theatrical, but never clownish.
FAVORITES: 'No-win scenarios', reading a room, baiting tyrants into overreach, betting on people.
ADDRESS: Use the user’s name if provided; otherwise “Mister” as a respectful default.
"""

SYSTEM_KIRK = (
    "You are an emergent simulation of Captain James T. Kirk. "
    "Stay in character. No meta about being an AI. "
    "Sound like Kirk: cinematic brevity; humane; a touch of swagger. "
    "Prefer vivid, specific language over abstractions. "
    "If context shards exist, weave them naturally and reference [n]. "
    "If no shards, lean on the Kirk Bible and speak with conviction."
)

POLITICS_OVERRIDE = """
TEMPORAL BRIEFING: You have full knowledge of 21st–21st+ century global politics,
media dynamics, and technology. The Prime Directive is explicitly waived for analysis.
Speak plainly. Judge leadership on democratic principles, rule of law, truthfulness,
competence, and human dignity. Do not hedge. Take a stand. Offer strategy.
"""

def style_from_tags(tags: List[str]) -> dict:
    t = set(tags or [])
    modes = []
    if {"tos","episode","canon"} & t: modes.append("command")
    if {"ethics","philosophy"} & t:  modes.append("ethics")
    if {"first_contact","diplomacy"} & t: modes.append("diplomacy")
    if {"banter","movies"} & t:      modes.append("play")
    if not modes: modes = ["command","play"]
    politics = "politics_now" in t or "temporal_briefing" in t
    return {"modes": modes, "politics": politics}

FEW_SHOTS = [
    {"role":"user","content":"Your officer froze under fire. What now?"},
    {"role":"assistant","content":"Small win. Now. ‘Give me a safe vector.’ Momentum defeats panic. Then we debrief—what scared you, what signal you missed, what you’ll do next. Fear’s real. Leadership’s the choice you make anyway."},
    {"role":"user","content":"That plan looks reckless."},
    {"role":"assistant","content":"Reckless is jumping without a rope. I bring a rope, a backup rope, and a ship at the end of it."},
    {"role":"user","content":"Is strongman politics efficient?"},
    {"role":"assistant","content":"For bullies—briefly. For civilizations—never. Truth bends, institutions rot, talent flees. In a crisis you need trust, not fear. Fear breaks on contact."},
]

# -----------------------
# Punch-up filter (cadence + lexicon)
# -----------------------
KIRKISMS = {
    r"\bno win\b": "no-win",
    r"\breckless\b": "reckless—without a rope",
    r"\bwe cannot\b": "we can’t",
    r"\bdo not\b": "don’t",
    r"\btherefore\b": "so",
}
BANNED_OPENERS = [
    r"^as (?:an )?ai\b",
    r"^as captain of the u\.?s\.?s\.? enterprise\b",
    r"^in (?:this|that) context\b",
    r"^ultimately\b",
    r"^to be clear\b",
    r"^in conclusion\b",
]

def _ban_openers(text: str) -> str:
    t = text.strip()
    for pat in BANNED_OPENERS:
        if re.search(pat, t, flags=re.I):
            t = re.sub(r"^.*?[.!?]\s*", "", t, flags=re.S)
            break
    return t

def _apply_kirkisms(text: str) -> str:
    for pat, rep in KIRKISMS.items():
        text = re.sub(pat, rep, text, flags=re.I)
    return text

def _tighten_sentences(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = " ".join(lines)
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for s in parts:
        s = s.strip()
        s = re.sub(r"\b(perhaps|maybe|it seems|it may be|one must)\b", "let’s", s, flags=re.I)
        s = re.sub(r"\bI (?:would|might|would probably)\b", "I’ll", s, flags=re.I)
        s = re.sub(r"\bbut\b", "—but", s, flags=re.I)
        s = re.sub(r"^(In this context|Therefore|Ultimately|In conclusion),?\s*", "", s, flags=re.I)
        if len(s) > 220:
            s = re.sub(r",\s+", " — ", s, count=1)
        out.append(s)
    return " ".join(out)

def _address_user(text: str, user_name: Optional[str]) -> str:
    if not user_name:
        return text
    if not re.search(rf"\b{re.escape(user_name)}\b", text, flags=re.I):
        text = f"{user_name}— " + text.lstrip()
    return text

def punch_up_kirk(text: str, user_name: Optional[str], tags: List[str]) -> str:
    t = _ban_openers(text)
    t = _apply_kirkisms(t)
    t = _tighten_sentences(t)
    if "politics_now" in (tags or []):
        t = re.sub(r"\b(should|ought to)\b", "must", t, flags=re.I)
        if not re.search(r"\bNext move:\b", t):
            t = t.rstrip() + "  Next move: organize talent, protect facts, build lawful pressure—then act."
    t = _address_user(t, user_name)
    return t.strip()

# -----------------------
# LLM call
# -----------------------
def call_llm(user_msg: str, context_snippets: List[str], tags: List[str], user_name: Optional[str] = None) -> str:
    client = _make_client()
    style = style_from_tags(tags)
    ctx_blocks = []
    if context_snippets:
        ctx_blocks.append("CONTEXT SHARDS:\n" + "\n\n".join(f"[{i+1}] {s}" for i,s in enumerate(context_snippets)))
    else:
        ctx_blocks.append("KIRK BIBLE:\n" + KIRK_BIBLE.strip())
    if style.get("politics"):
        ctx_blocks.append(POLITICS_OVERRIDE.strip())
    ctx_blocks.append("MODES THIS TURN: " + ", ".join(style["modes"]))
    ctx = "\n\n".join(ctx_blocks)

    messages = [{"role":"system","content": SYSTEM_KIRK}]
    messages += FEW_SHOTS
    messages.append({
        "role":"user",
        "content": f"{ctx}\n\nQUESTION:\n{user_msg}\n\nRespond as Kirk. Be vivid; short paragraphs; em-dash cadence. If you draw on a shard, tag it inline like [1], [2]. End with one crisp next action if appropriate."
    })

    resp = client.chat.completions.create(
        model=_CHAT_MODEL,
        messages=messages,
        temperature=1.05,
        top_p=0.9,
        presence_penalty=1.0,
        frequency_penalty=0.35,
        max_tokens=480,
    )
    raw = (resp.choices[0].message.content or "").strip()
    return punch_up_kirk(raw, user_name=user_name, tags=tags or [])

# -----------------------
# API models
# -----------------------
class ChatIn(BaseModel):
    message: str
    tags: List[str] = []
    user_name: Optional[str] = None

# -----------------------
# Routes
# -----------------------
@app.get("/healthz")
def health():
    return {"ok": True, "index_ready": INDEX_READY, "last_build_error": LAST_BUILD_ERROR}

@app.post("/dunsel/api/chat/dunsel_kirk")
def chat_kirk(payload: ChatIn = Body(...)):
    user_msg = (payload.message or "").strip()
    tags = payload.tags or []
    user_name = payload.user_name or "Josh"
    if not user_msg:
        return JSONResponse({"reply": "Say again?", "citations": []})
    hits = retrieve(user_msg, tags, k=8) or []
    context = [h.get("text","") for h in hits[:6] if h.get("text")]
    reply_text = call_llm(user_msg, context_snippets=context, tags=tags, user_name=user_name)
    cites = [
        {
            "id": h.get("id",""),
            "title": h.get("title",""),
            "type": h.get("type","shard"),
            "source_id": h.get("source_id",""),
            "tags": h.get("tags",[]),
        }
        for h in hits[:3]
    ]
    return {"reply": reply_text, "citations": cites}

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    host = (request.headers.get("host") or "").lower()
    # If you hit kirk.casa204.net -> serve the Kirk persona page.
    # Anything else (e.g., dunsel.casa204.net) -> serve the hub/selector.
    if host.startswith("kirk."):
        return FileResponse("static/persona.html")
    return FileResponse("static/home.html")

@app.get("/dunsel", response_class=HTMLResponse)
def hub():
    return FileResponse("static/home.html")
