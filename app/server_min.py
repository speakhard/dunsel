from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"

app = FastAPI()
app.mount("/dunsel/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/healthz", response_class=HTMLResponse)
def healthz():
    return "OK"

@app.get("/dunsel", response_class=HTMLResponse)
def hub():
    f = STATIC_DIR / "home.html"
    return FileResponse(f) if f.exists() else HTMLResponse("<h1>Dunsel Hub</h1>")

@app.get("/", response_class=HTMLResponse)
def root():
    f = STATIC_DIR / "home.html"
    return FileResponse(f) if f.exists() else HTMLResponse("<h1>Dunsel Hub</h1>")
