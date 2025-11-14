import re
from pathlib import Path

HEADER_RE = re.compile(r'^TITLE:(.*?)\nYEAR:(.*?)\nTYPE:(.*?)\nTAGS:(.*?)\nSOURCE_ID:(.*?)\n---\n', re.S)

def parse_doc(path: Path):
    txt = path.read_text(encoding="utf-8", errors="ignore")
    m = HEADER_RE.match(txt)
    if not m:
        raise ValueError(f"Missing header in {path}")
    title, year, typ, tags, source_id = [s.strip() for s in m.groups()]
    body = txt[m.end():].strip()
    meta = {
        "title": title, "year": year, "type": typ,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "source_id": source_id, "path": str(path)
    }
    return body, meta

def chunk_text(text: str, max_chars=1800, overlap=200):
    import re
    text = re.sub(r'\s+', ' ', text).strip()
    chunks = []
    i = 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunk = text[i:end]
        last_period = chunk.rfind('. ')
        if last_period > max_chars * 0.6:
            end = i + last_period + 2
            chunk = text[i:end]
        chunks.append(chunk.strip())
        i = max(end - overlap, end)
    return chunks
