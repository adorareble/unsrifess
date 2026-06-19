import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import HTTPException

from database import init_db, close_pool
from routers.public import public_router
from routers.panel import panel_router
from routers.auth_x import auth_x_router
from routers.sse import sse_router
from routers.admin import admin_router

app = FastAPI(title="fessable")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "frontend")), name="static")

app.include_router(public_router)
app.include_router(panel_router)
app.include_router(auth_x_router)
app.include_router(sse_router)
app.include_router(admin_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if "/api/" in request.url.path or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{exc.status_code} — fessable</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#FAFAF9;color:#2C2C2A;min-height:100vh;
    display:flex;align-items:center;justify-content:center;padding:20px;
  }}
  .card{{
    background:#F1EFE8;border:1px solid #D3D1C7;border-radius:20px;
    padding:40px;text-align:center;max-width:460px;width:100%;
  }}
  .code{{
    font-size:6rem;font-weight:800;line-height:1;
    color:#B4B2A9;margin-bottom:.25rem;
  }}
  .msg{{font-size:1.2rem;color:#888780;margin-bottom:2rem}}
  .btn{{
    display:inline-block;padding:13px 32px;border:none;border-radius:9999px;
    background:#2C2C2A;color:#FAFAF9;font-size:1rem;font-weight:700;
    text-decoration:none;transition:opacity .2s;
  }}
  .btn:hover{{opacity:.85}}
</style>
</head>
<body>
  <div class="card">
    <div class="code">{exc.status_code}</div>
    <div class="msg">{exc.detail}</div>
    <a class="btn" href="/">Back to Home</a>
  </div>
</body>
</html>""", status_code=exc.status_code)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>404 — fessable</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#FAFAF9;color:#2C2C2A;min-height:100vh;
    display:flex;align-items:center;justify-content:center;padding:20px;
  }
  .card{
    background:#F1EFE8;border:1px solid #D3D1C7;border-radius:20px;
    padding:40px;text-align:center;max-width:460px;width:100%;
  }
  .code{
    font-size:6rem;font-weight:800;line-height:1;
    color:#B4B2A9;margin-bottom:.25rem;
  }
  .msg{font-size:1.2rem;color:#888780;margin-bottom:2rem}
  .btn{
    display:inline-block;padding:13px 32px;border:none;border-radius:9999px;
    background:#2C2C2A;color:#FAFAF9;font-size:1rem;font-weight:700;
    text-decoration:none;transition:opacity .2s;
  }
  .btn:hover{opacity:.85}
</style>
</head>
<body>
  <div class="card">
    <div class="code">404</div>
    <div class="msg">Not Found</div>
    <a class="btn" href="/">Back to Home</a>
  </div>
</body>
</html>""", status_code=404)


@app.on_event("startup")
async def startup():
    try:
        await init_db()
        print("Database initialized", flush=True)
    except Exception as e:
        print(f"Database init failed: {e}", flush=True)


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
