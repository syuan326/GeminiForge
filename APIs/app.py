# -*- coding: utf-8 -*-
"""
GeminiForge 远程 API 服务
=========================
这是一个与 register.py 中 CredentialSyncer 兼容的远程凭证存储 API。

接口契约：
  POST /login
      body: admin_key=<SYNC_KEY>
      成功后会种下 Cookie，后续 GET/PUT 自动携带。

  GET /admin/accounts-config
      返回: {"accounts": [ {id, csesidx, config_id, secure_c_ses, host_c_oses, expires_at, ...} ]}

  PUT /admin/accounts-config
      body: JSON 数组（整个账号列表）
      返回: {"ok": true, "count": N}

认证方式：
  1. 先 POST /login 拿到 Cookie；或
  2. 每次请求带请求头 X-Admin-Key: <SYNC_KEY>

存储：
  默认使用 SQLite，数据保存在 data/accounts.db，重启不丢失。
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Form, HTTPException, Request, Response

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
ADMIN_KEY = os.environ.get("ADMIN_KEY", "123456789")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/accounts.db")
APP_NAME = "GeminiForge Remote API"
APP_VERSION = "1.0.0"

# 防止多人 PUT 同时写库
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# SQLite 存储
# ---------------------------------------------------------------------------
def _db_path() -> Path:
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id         TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def load_accounts() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT data FROM accounts ORDER BY updated_at DESC"
        ).fetchall()
    accounts = []
    for row in rows:
        try:
            accounts.append(json.loads(row["data"]))
        except json.JSONDecodeError:
            continue
    return accounts


def save_accounts(accounts: List[Dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _write_lock:
        with _connect() as conn:
            conn.execute("DELETE FROM accounts")
            for item in accounts:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or item.get("email") or "")
                if not item_id:
                    # 没有 id 也允许保存，用 JSON 内容做 id 的兜底
                    item_id = json.dumps(item, ensure_ascii=False, sort_keys=True)
                conn.execute(
                    "INSERT OR REPLACE INTO accounts (id, data, updated_at) VALUES (?, ?, ?)",
                    (item_id, json.dumps(item, ensure_ascii=False), now),
                )


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
def require_auth(request: Request) -> None:
    if request.cookies.get("admin_session") == ADMIN_KEY:
        return
    if request.headers.get("X-Admin-Key") == ADMIN_KEY:
        return
    raise HTTPException(status_code=401, detail="unauthorized: missing or invalid admin key")


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title=APP_NAME, version=APP_VERSION)

init_db()


@app.get("/")
async def index():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "endpoints": [
            "POST /login",
            "GET /admin/accounts-config",
            "PUT /admin/accounts-config",
        ],
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/login")
async def login(response: Response, admin_key: str = Form(...)):
    if admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="bad admin key")

    response.set_cookie(
        "admin_session",
        ADMIN_KEY,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return {"ok": True}


@app.get("/admin/accounts-config")
async def get_accounts_config(request: Request):
    require_auth(request)
    return {"accounts": load_accounts()}


@app.put("/admin/accounts-config")
async def put_accounts_config(request: Request):
    require_auth(request)

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="body must be valid JSON") from exc

    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="expected a JSON list of accounts")

    save_accounts(payload)
    return {"ok": True, "count": len(payload)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
