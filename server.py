# coding=utf-8
"""BJTU 选课助手 — FastAPI 服务端
一条命令启动: python server.py
然后浏览器打开 http://localhost:8765
"""
import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# 确保能导入 src 目录下的 login 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from login import CourseConfig, CourseGrabber

# ── 路径 ─────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "src", "omis.onnx")
HTML_PATH = os.path.join(BASE_DIR, "index.html")
LOG_PATH = os.path.join(BASE_DIR, "grab.log")

# ── 抢课日志 ────────────────────────────────────────
grab_logger = logging.getLogger("grab")
grab_logger.setLevel(logging.INFO)
_log_handler = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
grab_logger.addHandler(_log_handler)

# ── FastAPI 应用 ──────────────────────────────────
app = FastAPI(title="BJTU 选课助手")

# ── CORS（仅允许同源） ─────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],          # 不允许任何跨域
    allow_credentials=True,
    allow_methods=["GET", "POST"],
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


@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


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
    mis_user = data.get("username", "unknown")
    course_type = data.get("courseType", "unknown")

    try:
        config = CourseConfig.from_dict(data)
    except Exception as e:
        grab_logger.warning(f"[{mis_user}] 配置解析失败: {e}")
        await websocket.send_json(
            {"command": "error", "error": f"配置解析失败: {e}"}
        )
        return

    grabber = CourseGrabber(config)
    _current_grabber = grabber
    grabber.running = True
    grab_logger.info(f"[{mis_user}] 开始抢课 (类型: {course_type})")

    # 登录阶段
    try:
        async for result in grabber.login():
            await websocket.send_json(result)
            cmd = result.get("command")
            std_msg = result.get("std", result.get("error", ""))
            if cmd == "error":
                grab_logger.warning(f"[{mis_user}] 登录失败: {std_msg}")
                await websocket.send_json(
                    {"command": "finished", "std": "登录失败，任务结束"}
                )
                return
            elif cmd == "登录" and "成功" in std_msg:
                grab_logger.info(f"[{mis_user}] 登录成功")
    except Exception as e:
        grab_logger.error(f"[{mis_user}] 登录异常: {e}")
        await websocket.send_json({"command": "error", "error": f"登录异常: {e}"})
        await websocket.send_json({"command": "finished", "std": "任务结束"})
        return

    # 抢课循环
    try:
        while grabber.running and not websocket.client_state.name == "DISCONNECTED":
            try:
                async for result in grabber.grab_course():
                    await websocket.send_json(result)
                    cmd = result.get("command")
                    std_msg = result.get("std", result.get("error", ""))
                    if cmd == "success":
                        grab_logger.info(f"[{mis_user}] ★ 抢课成功: {std_msg}")
                    elif cmd == "error":
                        grab_logger.warning(f"[{mis_user}] 抢课失败: {std_msg}")
                    elif cmd == "stopped":
                        grab_logger.info(f"[{mis_user}] 任务已停止")
                    elif cmd == "选课":
                        grab_logger.info(f"[{mis_user}] {std_msg}")
                    if cmd in ("success", "error", "stopped"):
                        await websocket.send_json(
                            {"command": "finished", "std": "任务结束"}
                        )
                        return
            except Exception as e:
                grab_logger.error(f"[{mis_user}] 抢课循环异常: {e}")
                await websocket.send_json(
                    {"command": "error", "std": f"抢课循环异常: {e}"}
                )
                await websocket.send_json(
                    {"command": "finished", "std": "任务结束"}
                )
                return
            await asyncio.sleep(0.5)
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
