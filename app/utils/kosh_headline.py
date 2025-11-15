# app/utils/kosh_headline.py

import re

# Words that add emotional color but not structure. We strip them.
EMOTIONAL_ADJECTIVES = {
    "shocking", "outrageous", "bizarre", "insane", "heartbreaking",
    "brutal", "iconic", "beloved", "viral", "cringeworthy",
    "wacky", "furious", "angry", "devastating", "explosive",
    "controversial", "bombshell", "embarrassing", "insanely"
}

# Loud verbs we normalize into neutral ones.
VERB_NORMALIZATION = {
    r"\bslams\b": "criticizes",
    r"\bblasts\b": "criticizes",
    r"\btorches\b": "criticizes",
    r"\brips into\b": "criticizes",
    r"\bgoes off on\b": "criticizes",
    r"\bdrags\b": "criticizes",
    r"\bcancels\b": "rejects",
    r"\bslapped down\b": "rebuked",
    r"\btears into\b": "criticizes",
    r"\broasts\b": "criticizes",
}

# Strip section prefixes like "Politics: " or "Opinion: "
SECTION_PREFIX_RE = re.compile(
    r"^(Politics|War|World|Opinion|Analysis|Tech|Technology|Business|Climate|Entertainment|Celebrity|Breaking):\s+",
    flags=re.IGNORECASE,
)

# Strip outlet suffixes like " - The New York Times"
OUTLET_SUFFIX_RE = re.compile(
    r"\s+[-|–]\s+(The New York Times|NYTimes\.com|CNN|Fox News|BBC News|Reuters|AP News)\s*$",
    flags=re.IGNORECASE,
)

HASHTAG_MENTION_RE = re.compile(r"([#@][\w_]+)")

EMOJI_RE = re.compile(
    "["                       # crude emoji strip
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "]+"
)


def _normalize_verbs(text: str) -> str:
    for pattern, replacement in VERB_NORMALIZATION.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _remove_emotional_adjectives(text: str) -> str:
    words = text.split()
    cleaned = []
    for w in words:
        bare = re.sub(r"[^\w']", "", w).lower()
        if bare in EMOTIONAL_ADJECTIVES:
            continue
        cleaned.append(w)
    return " ".join(cleaned)


def _strip_question_wrapping(text: str) -> str:
    t = text.strip()
    if t.endswith("?"):
        t = t[:-1].strip()
    # e.g. "Why this election matters" -> "This election matters"
    m = re.match(r"^(why|how|what|is|are|does|do)\s+(.*)$", t, flags=re.IGNORECASE)
    if m:
        t = m.group(2).strip()
    return t


def _truncate_words(text: str, max_words: int = 20) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip()


def preprocess_headline_for_kosh(raw: str) -> str:
    """
    Take a messy real-world headline/summary and turn it into
    a short, neutral, event-focused description for Kosh.
    """
    if not raw:
        return ""

    text = raw.strip()

    # Remove emojis and hashtags/@mentions
    text = EMOJI_RE.sub("", text)
    text = HASHTAG_MENTION_RE.sub("", text)

    # Remove outlet suffixes (e.g., "- The New York Times")
    text = OUTLET_SUFFIX_RE.sub("", text)

    # Remove section prefixes ("Politics: ", "Opinion: ", etc.)
    text = SECTION_PREFIX_RE.sub("", text)

    # Split on hard separators, keep the first meaningful chunk
    for sep in ["—", "–", ";"]:
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if parts:
                text = parts[0]
                break

    # Remove emotional adjectives/adverbs
    text = _remove_emotional_adjectives(text)

    # Normalize loaded verbs
    text = _normalize_verbs(text)

    # Clean question-style headlines
    text = _strip_question_wrapping(text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)

    # Final length control
    text = _truncate_words(text, max_words=20)

    return text.strip()


def infer_tag_from_text(text: str) -> str:
    """
    Fallback tag inference if the caller doesn't provide tags.
    Very rough, but enough to choose Kosh mode.
    """
    t = (text or "").lower()

    if any(w in t for w in ["election", "vote", "ballot", "senate", "parliament", "congress", "supreme court"]):
        return "politics_now"

    if any(w in t for w in ["war", "invasion", "strike", "missile", "shelling", "artillery", "bombing", "airstrike"]):
        return "war"

    if any(w in t for w in ["climate", "heatwave", "heat wave", "wildfire", "wild fire", "flood", "hottest", "record temperatures", "drought"]):
        return "climate"

    if any(w in t for w in ["stock", "market", "inflation", "wage", "union", "strike", "layoff", "bank failure", "recession"]):
        return "economy"

    if any(w in t for w in ["ai", "artificial intelligence", "algorithm", "data breach", "surveillance", "neural network"]):
        return "tech"

    if any(w in t for w in ["singer", "actor", "actress", "rapper", "influencer", "celebrity", "pop star", "movie star"]):
        return "celebrity"

    if any(w in t for w in ["meme", "challenge", "trend", "tiktok", "goes viral", "viral video"]):
        return "viral"

    return "other"
