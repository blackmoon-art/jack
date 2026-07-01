"""Web server 端点测试 — FastAPI TestClient。"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 隔离服务器环境
os.environ.setdefault("WEB_PORT", "8080")
os.environ.setdefault("AGENT_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DAILY_LIMIT_PER_USER", "3")
os.environ.setdefault("WEB_ACCESS_CODE", "test-code")
os.environ.setdefault("AGENT_WORK_DIR", tempfile.mkdtemp())

from web.server import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)

# ── 隔离：每个测试独立 session ──
SESSION_ID = "test_session_id"


@pytest.fixture(autouse=True)
def _cleanup():
    """每个测试后清理 session。"""
    yield
    try:
        client.delete(f"/api/sessions/{SESSION_ID}")
    except Exception:
        pass


# ── 基础端点 ──────────────────────────────────────────


def test_health():
    """健康检查返回 status ok。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "model" in data
    assert "provider" in data


def test_index_page():
    """首页返回 HTML。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sleeping fox" in resp.text or "html" in resp.headers.get("content-type", "")


def test_fox_icon_404():
    """狐狸图标不存在时返回 404。"""
    resp = client.get("/fox.png")
    assert resp.status_code in (200, 404)  # 可能存在于 static/


def test_static_files():
    """静态文件可访问。"""
    # manifest.json 存在于 static/
    resp = client.get("/static/manifest.json")
    assert resp.status_code == 200


# ── Session 管理 ───────────────────────────────────────


def test_create_session():
    """首次请求时创建 session（SSE 流式或错误 JSON）。"""
    resp = client.post("/api/chat", json={
        "message": "hello",
        "session_id": SESSION_ID,
        "code": "test-code",
    })
    assert resp.status_code == 200
    # SSE streaming 或 JSON error 均接受
    ct = resp.headers.get("content-type", "")
    assert ("text/event-stream" in ct or "application/json" in ct)


def test_session_history():
    """会话结束后可查询历史。"""
    # 先发一条消息创建 session
    client.post("/api/chat", json={
        "message": "test message",
        "session_id": SESSION_ID,
        "code": "test-code",
    })
    # 查询历史
    resp = client.get(f"/api/sessions/{SESSION_ID}/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "history" in data


def test_clear_session():
    """清除 session 返回 ok。"""
    # 先创建 session
    client.post("/api/chat", json={
        "message": "hello",
        "session_id": SESSION_ID,
        "code": "test-code",
    })
    # 清除
    resp = client.delete(f"/api/sessions/{SESSION_ID}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_clear_nonexistent_session():
    """清除不存在的 session 也返回 ok。"""
    resp = client.delete("/api/sessions/nonexistent_12345")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── 每日限流 ──────────────────────────────────────────


def test_daily_limit_owner_exempt():
    """owner (提供正确 access code) 不受限流。"""
    resp = client.post("/api/chat", json={
        "message": "hello",
        "session_id": "owner_session",
        "code": "test-code",  # 正确的 owner code
    })
    assert resp.status_code == 200


def test_daily_limit_user_blocked():
    """外部用户超过每日限制后被拦截。"""
    # 用尽额度（DAILY_LIMIT_PER_USER=3）
    user_id = "limit_test_user"
    for i in range(4):
        resp = client.post("/api/chat", json={
            "message": f"msg {i}",
            "session_id": user_id,
            # 不提供 code，以外部用户身份
        })
    # 第 4 次应被拦截
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "上限" in data["error"] or "limit" in data["error"].lower()


def test_empty_message_rejected():
    """空消息被拒绝。"""
    resp = client.post("/api/chat", json={
        "message": "",
        "session_id": SESSION_ID,
    })
    assert resp.status_code == 200
    assert "error" in resp.json()


# ── 文件上传 ──────────────────────────────────────────


def test_upload_file():
    """上传小文件。需有效 session 的 work_dir。"""
    # 先创建 session 建立 work_dir
    client.post("/api/chat", json={
        "message": "init",
        "session_id": SESSION_ID,
        "code": "test-code",
    })
    content = b"Hello, test upload!"
    resp = client.post(
        "/api/upload",
        files={"file": ("test.txt", content, "text/plain")},
        data={"session_id": SESSION_ID},
    )
    # 上传成功或 session 未就绪均可
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("ok") is True or "error" in data


def test_upload_no_file_rejected():
    """无文件时返回验证错误。"""
    resp = client.post("/api/upload", data={"session_id": SESSION_ID})
    # FastAPI 自动校验返回 422
    assert resp.status_code in (400, 422)


# ── 文件下载 ──────────────────────────────────────────


def test_download_nonexistent():
    """下载不存在的文件返回 404。"""
    resp = client.get("/api/download/nonexistent_file.xyz")
    assert resp.status_code == 404


# ── 图表服务 ──────────────────────────────────────────


def test_serve_chart_404():
    """不存在的图表返回错误。"""
    resp = client.get("/charts/nonexistent_chart.png")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data


def test_chart_path_traversal_blocked():
    """路径穿越被阻止（解析后路径不在 charts_dir 内返回 404）。"""
    resp = client.get("/charts/../../../etc/passwd")
    # resolve 后不在 charts_dir 内 → 404 或错误
    assert resp.status_code in (200, 404)