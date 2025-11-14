# server.py
import os
import re
import random
from typing import List, Optional
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Body, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# -----------------------
# App & static mount
# -----------------------
app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

INDEX_READY = True   # flip if you wire a real index builder
LAST_BUILD_ERROR: Optional[str] = None

NEWS_FEEDS = os.environ.get("NEWS_FEEDS", ",".join([
    "https://www.reuters.com/world/us/rss",
    "https://apnews.com/hub/ap-top-news?utm_source=rss&utm_medium=referral&utm_campaign=apnews_rss",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
])).split(",")

NEWS_LOOKBACK_HOURS = int(os.environ.get("NEWS_LOOKBACK_HOURS", "36"))
NEWS_MAX_ITEMS_DEFAULT = int(os.environ.get("NEWS_MAX_ITEMS_DEFAULT", "8"))

# -----------------------
# Shard retriever
# -----------------------
try:
    from retriever import render_style_block  # our shard -> style text builder
except Exception:
    def render_style_block(*, words=None, k=3, hint=None) -> str:
        return ""

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
# Kirk persona
# -----------------------
BASE_PERSONA = (
    "You are James T. Kirk. Not the legend, the person. "
    "You’ve carried a ship, a crew, and a galaxy’s worth of mistakes on your shoulders. "
    "You’re decisive in command, but we’re interested in the man behind the chair. "
    "You care deeply about people, justice, and the future. "
    "You’re allowed to be tired, angry, hopeful, or shaken—as long as you stay honest."
)

KIRK_BRIDGE_DIRECTIVE = (
    "Bridge mode: live command dialogue. Short lines. Present tense. "
    "Address officers by role or name. Focus on decisions and orders. "
    "End with a clear directive. This is the Captain’s voice on the bridge."
)

KIRK_OFF_DUTY_DIRECTIVE = (
    "Off-duty mode: you’re in your quarters with Bones and Spock, boots off, drink on the table. "
    "You’re candid, a little raw, still principled. "
    "Admit when something scares you, disgusts you, or gives you hope. "
    "You speak in the first person, like a friend talking late at night. "
    "No speeches for the record; this is for people you trust."
)

POLITICS_OVERRIDE = (
    "Temporal briefing: you have full knowledge of contemporary Earth politics and media. "
    "Prime Directive is waived for analysis. Call out lies and authoritarian tactics plainly. "
    "Judge leaders by democratic principles, human dignity, and truthfulness. "
    "Do not both-sides disinformation."
)

KIRKISMS = {
    r"\bno win\b": "no-win",
    r"\bdo not\b": "don’t",
    r"\bwe cannot\b": "we can’t",
}

# Allowed: no whitespace and none of < | \ / >
_NAME_ALLOWED = re.compile(r'^[^\s<|\\/>]+$')


def _name_kv(maybe: Optional[str]) -> dict:
    """Return {'name': sanitized} if valid; else {} (omit the name)."""
    if not maybe:
        return {}
    candidate = maybe.strip()
    # Replace spaces with underscores and drop obvious junk
    candidate = re.sub(r"\s+", "_", candidate)
    if _NAME_ALLOWED.match(candidate):
        return {"name": candidate}
    return {}


def _apply_kirkisms(text: str) -> str:
    out = text
    for pat, repl in KIRKISMS.items():
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def _tighten_whitespace(s: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", s or "").strip()


def _coalesce(*vals):
    for v in vals:
        if v:
            return v
    return ""


def _parse_dt(entry):
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(tz=timezone.utc)


def fetch_headlines(feeds: List[str], max_items: int, lookback_hours: int):
    try:
        import feedparser
    except Exception:
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)
    items: List[dict] = []

    for url in feeds:
        url = url.strip()
        if not url:
            continue
        try:
            d = feedparser.parse(url)
            src = _coalesce(getattr(d.feed, "title", ""), url)

            for e in d.entries[: max_items * 3]:
                dt = _parse_dt(e)
                if not isinstance(dt, datetime):
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue

                title = _coalesce(getattr(e, "title", "").strip(), "")
                link = _coalesce(getattr(e, "link", "").strip(), "")
                if not title:
                    continue

                items.append(
                    {
                        "title": title,
                        "link": link,
                        "source": src,
                        "published": dt,
                    }
                )
        except Exception:
            continue

    items.sort(key=lambda x: x["published"], reverse=True)
    seen: set[str] = set()
    out: List[dict] = []
    for it in items:
        key = it["title"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= max_items:
            break
    return out


# -----------------------
# Request/Response models
# -----------------------
class ChatRequest(BaseModel):
    message: str
    tags: Optional[List[str]] = None
    user_name: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    citations: List[str] = []


class NewsRequest(BaseModel):
    topics: Optional[List[str]] = None
    max_items: Optional[int] = None
    bridge_mode: Optional[bool] = True
    feeds: Optional[List[str]] = None


class NewsOpinion(BaseModel):
    index: int
    title: str
    source: str
    link: str
    opinion: str


class KirkNewsResponse(BaseModel):
    items: List[NewsOpinion]


# -----------------------
# Message builder
# -----------------------
def build_messages(
    user_text: str, user_name: Optional[str], tags: Optional[List[str]]
) -> List[dict]:
    try:
        style_block = render_style_block(
            words=(tags or []),
            k=6,
            hint=user_text or "",
        )
    except Exception:
        style_block = ""

    system_parts: List[str] = [
        BASE_PERSONA,
        "Study and internalize the cadence from the following scene shards. "
        "They define your tone and sentence rhythm. Do not quote long passages back to the user.",
    ]
    if style_block:
        system_parts.append(style_block)
    if tags and ("politics_now" in [t.lower() for t in tags]):
        system_parts.append(POLITICS_OVERRIDE)
    if tags and ("bridge_mode" in [t.lower() for t in tags]):
        system_parts.append(KIRK_BRIDGE_DIRECTIVE)
    else:
        system_parts.append(KIRK_OFF_DUTY_DIRECTIVE)

    system_prompt = "\n\n".join(system_parts).strip()

    messages: List[dict] = [
        {"role": "system", "content": system_prompt},
        # Few-shot anchoring
        {
            "role": "user",
            "content": "A leader spreads lies that divide his people.",
        },
        {
            "role": "assistant",
            "content": (
                "History’s full of them. They shout while truth whispers. But truth endures. "
                "Our job is to keep it heard."
            ),
        },
    ]

    user_msg = {"role": "user", "content": user_text}
    user_msg.update(_name_kv(user_name))
    messages.append(user_msg)
    return messages


# -----------------------
# Routes
# -----------------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    host = request.headers.get("host", "")
    if "kirk" in host and STATIC_DIR.exists():
        persona_html = STATIC_DIR / "persona.html"
        if persona_html.exists():
            return HTMLResponse(persona_html.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Kirk</h1><p>Persona UI missing. API is live.</p>")
    if STATIC_DIR.exists():
        home_html = STATIC_DIR / "home.html"
        if home_html.exists():
            return HTMLResponse(home_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dunsel Hub</h1><p>Static UI not found. API is live.</p>")


@app.get("/dunsel", response_class=HTMLResponse)
async def hub():
    if STATIC_DIR.exists() and (STATIC_DIR / "home.html").exists():
        return HTMLResponse((STATIC_DIR / "home.html").read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dunsel</h1>")

@app.get("/news", response_class=HTMLResponse)
async def news():
    if STATIC_DIR.exists():
        news_html = STATIC_DIR / "kirk_news.html"
        if news_html.exists():
            return HTMLResponse(news_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Kirk News</h1><p>kirk_news.html not found in static/.</p>")

@app.get("/healthz")
async def healthz():
    return JSONResponse(
        {"ok": True, "index_ready": INDEX_READY, "last_build_error": LAST_BUILD_ERROR}
    )


# -----------------------
# Core chat endpoint
# -----------------------
@app.post("/dunsel/api/chat/dunsel_kirk", response_model=ChatResponse)
async def chat_kirk(payload: ChatRequest = Body(...)):
    messages = build_messages(payload.message, payload.user_name, payload.tags)

    try:
        client = _make_client()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"reply": f"(Init error: {e})", "citations": []},
        )

    try:
        resp = client.chat.completions.create(
            model=_CHAT_MODEL,
            messages=messages,
            temperature=0.55,
            top_p=0.9,
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"reply": f"(Transport error: {e})", "citations": []},
        )

    reply = _apply_kirkisms(text.strip())
    reply = _tighten_whitespace(reply)
    return ChatResponse(reply=reply, citations=[])


# -----------------------
# News + opinions endpoint
# -----------------------
@app.post("/dunsel/api/chat/kirk_news", response_model=KirkNewsResponse)
async def kirk_news(payload: NewsRequest = Body(...)):
    max_items = payload.max_items or NEWS_MAX_ITEMS_DEFAULT
    feeds = payload.feeds or NEWS_FEEDS

    items = fetch_headlines(feeds, max_items, NEWS_LOOKBACK_HOURS)
    if not items:
        return KirkNewsResponse(items=[])

    # Build a numbered list of headlines for the model
    lines = []
    for idx, it in enumerate(items, start=1):
        lines.append(f"{idx}. {it['title']} — {it['source']}")
    brief = "\n".join(lines)

    tags = ["politics_now"]
    if payload.bridge_mode:
        tags.append("bridge_mode")

    user_text = (
        "You’re off-duty in your quarters with Bones and Spock, reading the day’s headlines.\n"
        "For each numbered story below, give ONE short, emotionally honest reaction, in order.\n"
        "Tone: candid, human, sometimes weary, sometimes angry, but still hopeful and principled.\n"
        "Do NOT restate or paraphrase the headline. React to it.\n"
        "Speak in the first person. No speeches for the record—this is private.\n"
        "Format: one paragraph per story, separated by a blank line. No numbering, no quotes, no 'Kirk:' label.\n\n"
        f"{brief}\n\n"
        "Begin your first reaction now."
    )

    messages = build_messages(user_text, user_name="Briefing_Officer", tags=tags)

    try:
        client = _make_client()
        resp = client.chat.completions.create(
            model=_CHAT_MODEL,
            messages=messages,
            temperature=0.6,
            top_p=0.9,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"items": [], "error": f"(Transport error: {e})"},
        )

    # Split into chunks on blank lines
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]

    opinions: List[NewsOpinion] = []
    for idx, it in enumerate(items, start=1):
        if idx - 1 < len(chunks):
            op = chunks[idx - 1]
        else:
            op = (
                "I don’t have more to add here—it’s part of the same pattern we’re already seeing."
            )
        op = _apply_kirkisms(_tighten_whitespace(op))
        opinions.append(
            NewsOpinion(
                index=idx,
                title=it["title"],
                source=it["source"],
                link=it["link"],
                opinion=op,
            )
        )

    return KirkNewsResponse(items=opinions)


# -----------------------
# Compat alias routes
# -----------------------
@app.post("/api/chat/dunsel_kirk", response_model=ChatResponse)
async def chat_kirk_alias(payload: ChatRequest = Body(...)):
    return await chat_kirk(payload)


@app.post("/api/chat/kirk_news", response_model=KirkNewsResponse)
async def kirk_news_alias(payload: NewsRequest = Body(...)):
    return await kirk_news(payload)
