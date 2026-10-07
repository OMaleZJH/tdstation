# -*- coding: utf-8 -*-
"""
TD Station —— TokenDance AI 中转站（环球黑客松参赛作品）

功能：
- OAuth（Authorization Code + S256 PKCE）接入 TokenDance，用户授权自己的 Token 钱包
- 对话（OpenAI Chat Completions 流式）、图片生成、视频生成（异步任务）
- 余额查询、充值会话
- 所有模型调用带 X-App-URL 应用归因

两种 Key 模式：
1. BYOK 模式（默认）：用户逐个授权自己的 Token 钱包，Key 存服务端 data/keys.json
2. 全局 Key 模式：设置环境变量 TOKENDANCE_API_KEY，所有用户共用站点自己的 Key，免授权

部署：
    pip install -r requirements.txt
    export APP_URL=https://你的站点地址
    export CALLBACK_URL=https://你的站点地址/api/auth/callback
    export TOKENDANCE_API_KEY=可选，设置后走全局 Key 模式
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
import base64
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ============ 配置 ============
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8000")                     # 应用归因唯一标识
CALLBACK_URL = os.getenv("CALLBACK_URL", "http://127.0.0.1:8000/api/auth/callback")
GATEWAY = "https://tokendance.space/gateway/v1"                              # 模型网关
PORTAL = "https://tokendance.space/portal/api/v1"                            # 开放平台 API
AUTH_PAGE = "https://tokendance.space/auth"                                  # OAuth 授权页
KEY_NAME = "TD Station"                                                      # 授权页展示的 Key 名称
GLOBAL_KEY = os.getenv("TOKENDANCE_API_KEY", "")                             # 全局 Key 模式（部署时填）
DATA_DIR = Path(__file__).parent / "data"
KEY_FILE = DATA_DIR / "keys.json"                                            # BYOK 模式持久化用户 Key（勿外泄）

app = FastAPI(title="TD Station")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# session_id -> {"verifier": str, "key": str | None}
sessions: dict = {}


# ============ 工具函数 ============
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 43~128 字符，符合要求
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def load_keys() -> dict:
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text(encoding="utf-8"))
    return {}


def save_keys(keys: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    KEY_FILE.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")


def get_session(request: Request) -> dict:
    sid = request.cookies.get("td_sid")
    if not sid or sid not in sessions:
        sid = uuid.uuid4().hex
        sessions[sid] = {"verifier": None, "key": None}
    return sessions[sid], sid


def _restore_key(request: Request) -> str | None:
    """会话丢失时从持久化文件恢复 Key"""
    sid = request.cookies.get("td_sid")
    if sid:
        return load_keys().get(sid)
    return None


def _current_key(request: Request) -> str | None:
    """取当前请求使用的 Key：全局 Key 优先，否则走 BYOK 会话"""
    if GLOBAL_KEY:
        return GLOBAL_KEY
    sess, _ = get_session(request)
    return sess.get("key") or _restore_key(request)


# ============ OAuth 授权（BYOK 模式） ============
@app.get("/api/auth/start")
async def auth_start(request: Request):
    """生成 PKCE 并返回授权 URL（前端跳转）"""
    if GLOBAL_KEY:
        return {"url": "", "global": True}
    sess, sid = get_session(request)
    verifier, challenge = make_pkce()
    sess["verifier"] = verifier
    sess["key"] = None
    url = (f"{AUTH_PAGE}?callback_url={CALLBACK_URL}"
           f"&code_challenge={challenge}&code_challenge_method=S256"
           f"&app_url={APP_URL}&key_name={KEY_NAME}")
    resp = JSONResponse({"url": url})
    resp.set_cookie("td_sid", sid, httponly=True, samesite="lax")
    return resp


@app.get("/api/auth/callback")
async def auth_callback(code: str, request: Request):
    """TokenDance 授权回调：一次性 Code 换 API Key"""
    if GLOBAL_KEY:
        return FileResponse(Path(__file__).parent / "static" / "auth_ok.html")
    sess, sid = get_session(request)
    verifier = sess.get("verifier")
    if not verifier:
        return JSONResponse({"error": "未找到授权会话，请重新发起授权"}, status_code=400)
    payload = {"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{PORTAL}/auth/keys", json=payload)
    if r.status_code != 200:
        return JSONResponse({"error": f"换 Key 失败: {r.status_code} {r.text[:200]}"}, status_code=400)
    key = r.json().get("key")
    if not key:
        return JSONResponse({"error": "响应中没有 Key"}, status_code=400)
    sess["key"] = key
    sess["verifier"] = None  # code 一次性，清掉 verifier
    keys = load_keys()
    keys[sid] = key
    save_keys(keys)
    return FileResponse(Path(__file__).parent / "static" / "auth_ok.html")


@app.get("/api/auth/status")
async def auth_status(request: Request):
    if GLOBAL_KEY:
        return {"connected": True, "global": True}
    sess, _ = get_session(request)
    return {"connected": bool(sess.get("key")), "global": False}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    sess, sid = get_session(request)
    sess["key"] = None
    keys = load_keys()
    keys.pop(sid, None)
    save_keys(keys)
    return {"ok": True}


# ============ 模型调用 ============
def _gw_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "X-App-URL": APP_URL}


@app.get("/api/models")
async def list_models(request: Request):
    """拉取实时模型目录并按用途分类"""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GATEWAY}/models")
    if r.status_code != 200:
        return JSONResponse({"error": "模型目录拉取失败"}, status_code=502)
    cats = {"chat": [], "image": [], "video": [], "other": []}
    for m in r.json().get("data", []):
        proto = m.get("supported_protocols", [])
        name = m.get("name", m.get("id", ""))
        item = {"id": m.get("id"), "name": name, "ctx": m.get("context_length", 0)}
        if any(p in ("openai:chat-completions", "openai:responses", "anthropic:messages", "google:generate-content") for p in proto):
            cats["chat"].append(item)
        elif any(p in ("openai:image-generations", "openai:image-edits", "ark:image-generations") for p in proto):
            cats["image"].append(item)
        elif any("video" in p or "seedance" in p or "vidu" in p for p in proto):
            cats["video"].append(item)
        else:
            cats["other"].append(item)
    return cats


@app.post("/api/chat")
async def chat(request: Request):
    """对话代理：流式转发 OpenAI Chat Completions"""
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    body = await request.json()
    async with httpx.AsyncClient(timeout=None) as client:
        req = client.build_request("POST", f"{GATEWAY}/chat/completions",
                                   headers=_gw_headers(key), json=body)
        r = await client.send(req, stream=True)
    if r.status_code != 200:
        return JSONResponse({"error": f"调用失败 {r.status_code}: {(await r.aread())[:300]}"},
                            status_code=r.status_code)
    return StreamingResponse(r.aiter_raw(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/image")
async def image(request: Request):
    """图片生成"""
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    body = await request.json()
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{GATEWAY}/images/generations", headers=_gw_headers(key), json=body)
    return JSONResponse(r.json(), status_code=r.status_code)


# ============ 视频生成（Vidu 异步） ============
@app.post("/api/video")
async def video_create(request: Request):
    """提交视频生成任务，返回 task_id"""
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    body = await request.json()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"https://tokendance.space/gateway/vidu/v2/text2video",
                              headers=_gw_headers(key), json=body)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/video/status")
async def video_status(task_id: str, request: Request):
    """查询视频任务结果"""
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"https://tokendance.space/gateway/vidu/v2/tasks/{task_id}/creations",
                             headers=_gw_headers(key))
    return JSONResponse(r.json(), status_code=r.status_code)


# ============ 钱包：余额 / 充值 ============
@app.get("/api/balance")
async def balance(request: Request):
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{PORTAL}/user/balance", headers=_gw_headers(key))
    if r.status_code != 200:
        return JSONResponse({"error": f"{r.status_code} {r.text[:200]}"}, status_code=r.status_code)
    d = r.json().get("balance", {})
    # 微元 -> 元
    return {"credits": d.get("credits", 0) / 1e6,
            "used": d.get("credits_used", 0) / 1e6,
            "balance": d.get("balance", 0) / 1e6}


@app.post("/api/pay")
async def pay(request: Request):
    """创建充值会话（单位：元）"""
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    body = await request.json()
    amount = int(body.get("amount", 10))
    if amount < 1 or amount > 100000:
        return JSONResponse({"error": "金额需在 1~100000 元之间"}, status_code=400)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{PORTAL}/payment/sessions", headers=_gw_headers(key),
                              json={"amount": amount})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/pay/status/{session_id}")
async def pay_status(session_id: str, request: Request):
    key = _current_key(request)
    if not key:
        return JSONResponse({"error": "请先连接 Token 钱包"}, status_code=401)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{PORTAL}/payment/sessions/{session_id}", headers=_gw_headers(key))
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
