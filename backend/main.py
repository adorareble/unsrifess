import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from database import init_db, close_pool
from routers.public import public_router
from routers.panel import panel_router
from routers.auth_x import auth_x_router
from routers.sse import sse_router

app = FastAPI(title="TwitterTools")

app.include_router(public_router)
app.include_router(panel_router)
app.include_router(auth_x_router)
app.include_router(sse_router)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>404 — Unsr!fess</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#000;color:#e7e9ea;min-height:100vh;
    display:flex;align-items:center;justify-content:center;padding:20px;
  }
  .card{
    background:#16181c;border:1px solid #2f3336;border-radius:20px;
    padding:40px;text-align:center;max-width:460px;width:100%;
  }
  .code{
    font-size:6rem;font-weight:800;line-height:1;
    background:linear-gradient(135deg,#1d9bf0,#8b5cf6);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
    background-clip:text;margin-bottom:.25rem;
  }
  .msg{font-size:1.2rem;color:#71767b;margin-bottom:2rem}
  .btn{
    display:inline-block;padding:13px 32px;border:none;border-radius:9999px;
    background:#1d9bf0;color:#fff;font-size:1rem;font-weight:700;
    text-decoration:none;transition:background .2s;
  }
  .btn:hover{background:#1a8cd8}
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


@app.get("/panel")
async def panel_root():
    return RedirectResponse(url="/panel/dashboard")


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
