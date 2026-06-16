"""SlothOS ROMs — FastAPI server (Phase 3.0, LAN-only EmulatorJS backend)."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import (
    FastAPI,
    HTTPException,
    Path as FPath,
    Query,
    Request,
    UploadFile,
    File,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import aiofiles

# Config paths (override via env slothos_config)
HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(__import__("os").environ.get("SLOTHOS_CONFIG", HERE / "config.yaml"))


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


CFG = load_config()
ROMS_DIR = Path(CFG["roms_dir"])
DB_PATH = Path(CFG["catalog_db"])
BOXART_DIR = Path(CFG["boxart_cache_dir"])
SAVES_DIR = Path(CFG["saves_dir"])
EMUJS_DIR = Path(CFG["emujs_bundle_dir"])
CORES_PATH = HERE / CFG.get("cores_config", "cores.yaml")

with CORES_PATH.open("r", encoding="utf-8") as fh:
    CORES = yaml.safe_load(fh)

# Ensure runtime dirs exist
for p in (ROMS_DIR, DB_PATH.parent, BOXART_DIR, SAVES_DIR):
    p.mkdir(parents=True, exist_ok=True)

import scanner as scanner_mod  # noqa: E402

HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(follow_redirects=True)
    # initial DB init
    scanner_mod.init_db(DB_PATH)
    yield
    await HTTP_CLIENT.aclose()
    HTTP_CLIENT = None


app = FastAPI(title="SlothOS ROMs", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(HERE / "templates"))


@app.middleware("http")
async def coop_coep_middleware(request: Request, call_next):
    """Inject COOP/COEP headers on /play/* and /static/emujs/* for SharedArrayBuffer."""
    resp: Response = await call_next(request)
    path = request.url.path
    if path.startswith("/play") or path.startswith("/static/emujs"):
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return resp


# ---------- API ----------

@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/scan")
async def api_scan():
    """Re-scan ROM directory and rebuild catalog."""
    # run blocking scan in threadpool
    result = await asyncio.to_thread(
        scanner_mod.scan, ROMS_DIR, DB_PATH, CORES_PATH
    )
    return result


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "system": row["system"],
        "filename": row["filename"],
        "file_ext": row["file_ext"],
        "file_size": row["file_size"],
        "crc32": row["crc32"],
        "boxart_status": row["boxart_status"],
        "boxart_url": f"/boxart/{row['system']}/{row['title']}",
        "play_url": f"/play/{row['id']}",
    }


@app.get("/api/library")
async def library(
    system: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    clauses, params = [], []
    if system and system in CORES:
        clauses.append("system = ?")
        params.append(system)
    if q:
        clauses.append("title LIKE ?")
        params.append(f"%{q}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM roms{where} ORDER BY title LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    conn = db_conn()
    rows = conn.execute(sql, params).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) FROM roms{where}", params[:-2]
    ).fetchone()[0] if clauses else conn.execute("SELECT COUNT(*) FROM roms").fetchone()[0]
    conn.close()
    return {
        "total": total,
        "items": [_row_to_dict(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/library/{rom_id}")
async def library_one(rom_id: int = FPath(..., ge=1)):
    conn = db_conn()
    row = conn.execute("SELECT * FROM roms WHERE id=?", (rom_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "ROM not found")
    return _row_to_dict(row)


@app.api_route("/roms/{system}/{filename:path}", methods=["GET", "HEAD"])
async def serve_rom(system: str, filename: str):
    if system not in CORES:
        raise HTTPException(404, "Unknown system")
    # prevent traversal
    safe_name = Path(filename).name
    rom_path = (ROMS_DIR / system / safe_name).resolve()
    try:
        rom_path.relative_to(ROMS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Bad path")
    if not rom_path.is_file():
        raise HTTPException(404, "ROM not found")
    return FileResponse(str(rom_path), filename=safe_name)


@app.get("/boxart/{system}/{title:path}")
async def serve_boxart(system: str, title: str):
    if system not in CORES:
        raise HTTPException(404, "Unknown system")
    repo = CORES[system].get("thumbnails_repo", "")
    from boxart import fetch_and_cache
    assert HTTP_CLIENT is not None
    png, status = await fetch_and_cache(title, system, repo, BOXART_DIR, HTTP_CLIENT)
    # update catalog status asynchronously (best-effort)
    try:
        conn = db_conn()
        conn.execute(
            "UPDATE roms SET boxart_status=? WHERE system=? AND title=?",
            (status, system, title),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return Response(content=png, media_type="image/png")


@app.get("/play/{rom_id}", response_class=HTMLResponse)
async def play(request: Request, rom_id: int = FPath(..., ge=1)):
    conn = db_conn()
    row = conn.execute("SELECT * FROM roms WHERE id=?", (rom_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "ROM not found")
    sys_cfg = CORES.get(row["system"], {})
    # device_id via cookie (best-effort); default to anonymous
    device_id = request.cookies.get("slothos_device") or "anon"
    return templates.TemplateResponse(
        request,
        "play.html",
        {
            "title": row["title"],
            "system": row["system"],
            "filename": row["filename"],
            "rom_id": row["id"],
            "ejs_core": sys_cfg.get("ejs_core", row["system"]),
            "device_id": device_id,
        },
    )


@app.get("/api/saves/{device_id}/{rom_id}")
async def saves_get(device_id: str, rom_id: int):
    safe_device = Path(device_id).name  # prevent traversal
    save_path = SAVES_DIR / safe_device / f"{rom_id}.state"
    if not save_path.is_file():
        # EmulatorJS expects 200/204 when no save exists, not 404 — return empty
        return Response(b"", media_type="application/octet-stream", status_code=204)
    return FileResponse(str(save_path), media_type="application/octet-stream")


@app.put("/api/saves/{device_id}/{rom_id}")
async def saves_put(device_id: str, rom_id: int, file: UploadFile = File(...)):
    safe_device = Path(device_id).name
    save_dir = SAVES_DIR / safe_device
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{rom_id}.state"
    # 16 MB cap
    size = 0
    async with aiofiles.open(str(save_path), "wb") as out:
        while chunk := await file.read(1 << 16):
            size += len(chunk)
            if size > 16 * (1 << 20):
                await out.close()
                save_path.unlink(missing_ok=True)
                raise HTTPException(413, "Save state too large (>16MB)")
            await out.write(chunk)
    return {"ok": True, "size": size}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"systems": list(CORES.keys())})


# ---------- Static mount (EmulatorJS bundle) ----------

if EMUJS_DIR.is_dir():
    app.mount(
        "/static/emujs",
        StaticFiles(directory=str(EMUJS_DIR)),
        name="emujs",
    )
else:
    @app.get("/static/emujs/{path:path}")
    async def emujs_missing(path: str):
        raise HTTPException(
            503,
            "EmulatorJS bundle not installed. Run scripts/fetch-emujs.sh on Lappy.",
        )


if __name__ == "__main__":
    import uvicorn

    host = CFG.get("host", "0.0.0.0")
    port = int(CFG.get("port", 8444))
    uvicorn.run("server:app", host=host, port=port, reload="--reload" in sys.argv)
