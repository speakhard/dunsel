import os
from pathlib import Path
from typing import List, Optional
from .utils.kosh_headline import preprocess_headline_for_kosh, infer_tag_from_text

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI

# -------------------------------------------------------------------
# Basic setup
# -------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PERSONA_DIR = BASE_DIR / "persona"

app = FastAPI()

# Static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# CORS (loose, but fine for your use case / local + CF tunnel)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI()
MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# simple health flags
INDEX_READY = True
LAST_BUILD_ERROR: Optional[str] = None


# -------------------------------------------------------------------
# Data models
# -------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    tags: List[str] = []
    user_name: Optional[str] = None


# -------------------------------------------------------------------
# Persona loading
# -------------------------------------------------------------------

def load_persona(name: str) -> str:
    """
    Load persona markdown text from app/persona/<name>.md
    """
    path = PERSONA_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


# -------------------------------------------------------------------
# Message construction
# -------------------------------------------------------------------

def style_from_tags(tags: List[str]) -> str:
    """
    Optional styling hint based on tags.
    You can expand this if you add more modes later.
    """
    if "politics_now" in tags:
        return (
            "Focus on political, civic, and ethical stakes. "
            "Avoid partisan cheerleading. Be clear, grounded, and sharp."
        )
    if "command" in tags:
        return "Be more directive and concise. Provide actionable guidance."
    return ""


def build_messages(
    persona_text: str,
    user_message: str,
    tags: List[str],
    user_name: Optional[str],
    persona_name: str,
) -> List[dict]:
    """
    Build OpenAI chat messages given a persona, user input, and tags.
    """
    style_hint = style_from_tags(tags)

    system_intro = (
        f"You are {persona_name}, a persistent persona running inside the Dunsel system. "
        "Stay strictly in character. Do not mention being an AI, a model, or a persona. "
        "Respond only as the character."
    )

    system_content = "\n\n".join(
        [
            system_intro,
            "Persona specification:",
            persona_text,
            "",
            "Additional style hint (optional, may be empty):",
            style_hint,
        ]
    )

    user_name_label = user_name or "User"

    user_content = f"{user_name_label} says:\n\n{user_message}"

    messages: List[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    return messages


# -------------------------------------------------------------------
# Routes: HTML front doors
# -------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Serve different HTML depending on hostname:
    - kirk.casa204.net -> persona.html (Kirk UI)
    - kosh.casa204.net -> kosh.html (Kosh UI)
    - everything else  -> home.html (Dunsel hub)
    """
    host = request.headers.get("host", "")

    if host.startswith("kirk.casa204.net"):
        html_path = STATIC_DIR / "persona.html"
    elif host.startswith("kosh.casa204.net"):
        html_path = STATIC_DIR / "kosh.html"
    else:
        html_path = STATIC_DIR / "home.html"

    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    return PlainTextResponse(
        "Dunsel online, but expected HTML file is missing.",
        status_code=200,
    )


@app.get("/dunsel", response_class=HTMLResponse)
async def dunsel_home():
    """
    Explicit hub page route, same as root for non-persona hosts.
    """
    html_path = STATIC_DIR / "home.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return PlainTextResponse("Dunsel hub online, but home.html missing.", status_code=200)


# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "index_ready": INDEX_READY,
        "last_build_error": LAST_BUILD_ERROR,
    }


# -------------------------------------------------------------------
# Kirk persona endpoint
# -------------------------------------------------------------------

@app.post("/dunsel/api/chat/dunsel_kirk")
async def chat_dunsel_kirk(req: ChatRequest):
    """
    Main chat endpoint for the Kirk persona.
    """

    persona_text = load_persona("kirk")
    messages = build_messages(
        persona_text=persona_text,
        user_message=req.message,
        tags=req.tags,
        user_name=req.user_name,
        persona_name="James T. Kirk",
    )

    completion = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=600,
    )

    reply = completion.choices[0].message.content.strip()
    return {
        "reply": reply,
        "citations": [],
    }


# -------------------------------------------------------------------
# Kosh persona endpoint (news + gossip)
# -------------------------------------------------------------------

@app.post("/dunsel/api/chat/dunsel_kosh_news")
async def chat_kosh_news(request: ChatRequest):
    raw_message = request.message or ""
    incoming_tags = request.tags or []

    # 1) Clean the headline / blurb for Vorlon-friendly structure
    clean_headline = preprocess_headline_for_kosh(raw_message)

    # 2) Decide what to send as the actual "message" to Kosh
    # Prefer the cleaned version, but fall back to raw if we somehow stripped everything.
    final_message = clean_headline or raw_message.strip()

    # 3) Ensure we have at least one tag:
    #    - Use existing tags if present
    #    - Otherwise infer from the cleaned headline
    tags = list(incoming_tags)
    if not tags:
        inferred = infer_tag_from_text(final_message)
        tags = [inferred]

    persona = load_persona("kosh")  # whatever your existing loading function is

    # 4) Call your existing OpenAI chat wrapper with the preprocessed message & tags
    reply, citations = await run_chat_with_persona(
        persona=persona,
        message=final_message,
        tags=tags,
        user_name=request.user_name or "User",
    )

    return {"reply": reply, "citations": citations}

