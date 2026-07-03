# coding=utf-8
"""BJTU 选课助手 — FastAPI 服务端
一条命令启动: python server.py
然后浏览器打开 http://localhost:8765
"""
import asyncio
import json
import os
import signal
import sqlite3
import sys
from datetime import datetime

# 确保能导入 src 目录下的 login 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from login import CourseConfig, CourseGrabber

# ── 路径 ─────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "src", "omis.onnx")
HTML_PATH = os.path.join(BASE_DIR, "index.html")
DB_PATH = os.path.join(BASE_DIR, "accounts.db")

# ── 数据库 ────────────────────────────────────────

def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """初始化数据库表"""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mis_username TEXT NOT NULL UNIQUE,
            mis_password TEXT NOT NULL DEFAULT '',
            tujian_username TEXT NOT NULL DEFAULT '',
            tujian_password TEXT NOT NULL DEFAULT '',
            course_list TEXT NOT NULL DEFAULT '',
            course_type TEXT NOT NULL DEFAULT 'required',
            senior_check INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# ── API 模型 ──────────────────────────────────────

class AccountSave(BaseModel):
    mis_username: str
    mis_password: str = ""
    tujian_username: str = ""
    tujian_password: str = ""
    course_list: str = ""
    course_type: str = "required"
    senior_check: bool = False

# ── FastAPI 应用 ──────────────────────────────────
app = FastAPI(title="BJTU 选课助手")

# ── CORS（仅允许同源） ─────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],          # 不允许任何跨域
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# ── 安全响应头 ─────────────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.on_event("startup")
async def startup():
    """启动时初始化数据库"""
    init_db()


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ── 账号管理 API ──────────────────────────────────

@app.post("/api/accounts")
async def save_account(data: AccountSave):
    """保存或更新账号信息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO accounts (mis_username, mis_password, tujian_username, tujian_password,
                                  course_list, course_type, senior_check, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mis_username) DO UPDATE SET
                mis_password=excluded.mis_password,
                tujian_username=excluded.tujian_username,
                tujian_password=excluded.tujian_password,
                course_list=excluded.course_list,
                course_type=excluded.course_type,
                senior_check=excluded.senior_check,
                updated_at=excluded.updated_at
        """, (
            data.mis_username, data.mis_password,
            data.tujian_username, data.tujian_password,
            data.course_list, data.course_type,
            1 if data.senior_check else 0,
            now, now,
        ))
        conn.commit()
        return {"success": True, "message": "账号信息已保存"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        conn.close()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接，处理登录和抢课"""
    # 来源检查：仅允许同源连接
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        origin_host = parsed.netloc
        if origin_host != host:
            await websocket.close(code=4003, reason="Forbidden origin")
            return
    await websocket.accept()
    grabber = None
    grab_task = None

    try:
        async for raw_message in websocket.iter_text():
            data = json.loads(raw_message)
            command = data.get("command")

            if command == "stop":
                if grabber:
                    grabber.stop()
                await websocket.send_json(
                    {"command": "finished", "std": "任务已停止"}
                )
                continue

            # 取消上一轮任务
            if grab_task and not grab_task.done():
                grab_task.cancel()
                try:
                    await grab_task
                except (asyncio.CancelledError, Exception):
                    pass

            # 启动新一轮
            grab_task = asyncio.create_task(
                _process_course_grab(websocket, data)
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket 异常: {e}")
    finally:
        if grabber:
            grabber.stop()
        if grab_task and not grab_task.done():
            grab_task.cancel()


async def _process_course_grab(websocket: WebSocket, data: dict):
    """核心流程: 登录 → 循环抢课"""
    global _current_grabber

    # 注入 modelPath（前端无需传）
    data["modelPath"] = MODEL_PATH

    try:
        config = CourseConfig.from_dict(data)
    except Exception as e:
        await websocket.send_json(
            {"command": "error", "error": f"配置解析失败: {e}"}
        )
        return

    grabber = CourseGrabber(config)
    _current_grabber = grabber
    grabber.running = True

    # 登录阶段
    try:
        async for result in grabber.login():
            await websocket.send_json(result)
            if result.get("command") == "error":
                await websocket.send_json(
                    {"command": "finished", "std": "登录失败，任务结束"}
                )
                return
    except Exception as e:
        await websocket.send_json({"command": "error", "error": f"登录异常: {e}"})
        await websocket.send_json({"command": "finished", "std": "任务结束"})
        return

    # 抢课循环
    try:
        while grabber.running and not websocket.client_state.name == "DISCONNECTED":
            try:
                async for result in grabber.grab_course():
                    await websocket.send_json(result)
                    if result.get("command") in ("success", "error", "stopped"):
                        await websocket.send_json(
                            {"command": "finished", "std": "任务结束"}
                        )
                        return
            except Exception as e:
                await websocket.send_json(
                    {"command": "error", "std": f"抢课循环异常: {e}"}
                )
                await websocket.send_json(
                    {"command": "finished", "std": "任务结束"}
                )
                return
            await asyncio.sleep(2)
    except Exception:
        pass

    try:
        await websocket.send_json(
            {"command": "success", "std": "任务结束"}
        )
    except Exception:
        pass


_current_grabber = None


# ── 启动 ──────────────────────────────────────────
def main():
    host = "0.0.0.0"
    port = 8765

    print(f"{'='*50}")
    print(f"  BJTU 选课助手已启动")
    print(f"  请在浏览器打开: http://localhost:{port}")
    print(f"{'='*50}")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
