#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
李阳疯狂英语300句 · 后端服务

提供账号体系与学习进度云同步，同时托管静态页面。
游客模式下前端完全可用（进度存浏览器本地），登录后才走这里的同步接口。

启动：
    python server.py            # 默认 http://127.0.0.1:8000
    PORT=8080 python server.py
"""
import hashlib
import hmac
import json
import os
import random
import secrets
import smtplib
import sqlite3
import ssl
import string
import time
from email.message import EmailMessage
from pathlib import Path

import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).parent
DB_PATH = BASE / "data.db"
STATIC_DIR = BASE / "public"
SECRET_FILE = BASE / ".secret"

# ---------- 邮件发送配置（未配置时走开发模式：验证码只打印到控制台） ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
MAIL_ON = bool(SMTP_HOST and SMTP_USER)
DEV_ECHO = os.environ.get("DEV_ECHO_CODE", "1") == "1"   # 开发模式：接口直接回显验证码，方便本地联调

# ---------- 密钥 ----------
def load_secret() -> str:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    s = secrets.token_hex(32)
    SECRET_FILE.write_text(s)
    return s

SECRET = os.environ.get("JWT_SECRET") or load_secret()
TOKEN_DAYS = 30

# ---------- 数据库 ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT UNIQUE NOT NULL,
                name       TEXT NOT NULL,
                pw_hash    TEXT NOT NULL,
                salt       TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                user_id    INTEGER PRIMARY KEY,
                sent       TEXT NOT NULL DEFAULT '[]',
                vocab      TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL,
                code       TEXT NOT NULL,
                purpose    TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_email ON codes(email, purpose)")
        conn.commit()

init_db()

# ---------- 图形验证码（内存态，5 分钟有效，用后即焚） ----------
CAPTCHA = {}
CAPTCHA_TTL = 300

def make_captcha_svg(code):
    colors = ["#e0432f", "#2563eb", "#059669", "#7c3aed", "#d97706"]
    parts = []
    for i, ch in enumerate(code):
        x = 18 + i * 30
        rot = random.randint(-22, 22)
        parts.append(
            f'<text x="{x}" y="{34 + random.randint(-4, 4)}" font-size="30" font-family="Menlo,monospace" '
            f'font-weight="bold" fill="{random.choice(colors)}" '
            f'transform="rotate({rot} {x} 30)">{ch}</text>'
        )
    for _ in range(6):
        parts.append(
            f'<line x1="{random.randint(0,120)}" y1="{random.randint(0,46)}" '
            f'x2="{random.randint(0,120)}" y2="{random.randint(0,46)}" '
            f'stroke="{"#cbd5e1"}" stroke-width="1"/>'
        )
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="126" height="46" '
            f'style="background:#f8fafc;border-radius:8px">' + "".join(parts) + "</svg>")

def new_captcha():
    for k, (_, exp) in list(CAPTCHA.items()):
        if exp < time.time():
            CAPTCHA.pop(k, None)
    code = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
    cid = secrets.token_urlsafe(10)
    CAPTCHA[cid] = (code.lower(), time.time() + CAPTCHA_TTL)
    return cid, make_captcha_svg(code)

def check_captcha(cid, ans):
    if not cid or not ans:
        return False
    item = CAPTCHA.pop(cid, None)          # 用后即焚
    if not item:
        return False
    code, exp = item
    return exp >= time.time() and code == str(ans).strip().lower()

# ---------- 简单限流（内存态，够用；上规模再换 Redis） ----------
RATE = {}
def rate_ok(key, limit, window):
    now = time.time()
    hits = [t for t in RATE.get(key, []) if now - t < window]
    if len(hits) >= limit:
        RATE[key] = hits
        return False
    hits.append(now)
    RATE[key] = hits
    return True

FAILS = {}
def fail_count(key):
    now = time.time()
    return len([t for t in FAILS.get(key, []) if now - t < 900])

def note_fail(key):
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < 900] + [now]

def clear_fail(key):
    FAILS.pop(key, None)

def client_ip(request: Request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")

# ---------- 邮箱验证码 ----------
def send_mail(to, subject, body) -> bool:
    if not MAIL_ON:
        print(f"[MAIL-DEV] 收件人={to} 主题={subject} 内容={body}")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, SMTP_FROM, to
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=15) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[MAIL-ERROR] {e}")
        return False

def issue_code(email, purpose) -> str:
    code = "".join(random.choice(string.digits) for _ in range(6))
    with db() as conn:
        conn.execute("UPDATE codes SET used=1 WHERE email=? AND purpose=? AND used=0", (email, purpose))
        conn.execute(
            "INSERT INTO codes(email,code,purpose,expires_at) VALUES(?,?,?,?)",
            (email, code, purpose, int(time.time()) + 600),
        )
        conn.commit()
    return code

def verify_code(email, code, purpose):
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM codes WHERE email=? AND code=? AND purpose=? AND used=0 AND expires_at>=?",
            (email, str(code).strip(), purpose, now),
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE codes SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
    return True

def hash_password(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()

# ---------- 鉴权 ----------
def make_token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"uid": user_id, "iat": now, "exp": now + TOKEN_DAYS * 86400},
        SECRET, algorithm="HS256",
    )

def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        payload = jwt.decode(authorization[7:], SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "登录已失效，请重新登录")
    return int(payload["uid"])

# ---------- 接口 ----------
app = FastAPI(title="疯狂英语300句 · 后端")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

class Cred(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    name: str = ""
    code: str = ""
    captcha_id: str = ""
    captcha_code: str = ""

class CodeReq(BaseModel):
    email: str
    purpose: str = "register"
    captcha_id: str = ""
    captcha_code: str = ""

class ProgressIn(BaseModel):
    sent: list = []
    vocab: list = []
    base_ts: int = 0      # 客户端上次同步到的服务端时间戳，用于判断本地数据是否过期

@app.get("/api/captcha")
def captcha():
    cid, svg = new_captcha()
    return {"id": cid, "svg": svg}

@app.post("/api/email-code")
def email_code(req: Request, r: CodeReq):
    email = r.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "请输入有效的邮箱")
    if r.purpose not in ("register", "reset"):
        raise HTTPException(400, "请求类型不合法")
    ip = client_ip(req)
    if not rate_ok(f"code:ip:{ip}", 10, 3600):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    if not rate_ok(f"code:mail:{email}", 3, 3600):
        raise HTTPException(429, "该邮箱请求验证码次数过多，请 1 小时后再试")
    if not check_captcha(r.captcha_id, r.captcha_code):
        raise HTTPException(400, "图形验证码不正确或已过期")
    if r.purpose == "register":
        with db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HTTPException(400, "该邮箱已注册，请直接登录")
    else:
        with db() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HTTPException(400, "该邮箱尚未注册")
    code = issue_code(email, r.purpose)
    ok = send_mail(email, "疯狂英语300句 · 邮箱验证码",
                   f"你的验证码是 {code}，10 分钟内有效。若非本人操作请忽略。")
    resp = {"ok": True, "sent": ok, "dev": (not MAIL_ON)}
    if not MAIL_ON:
        print(f"[DEV] {email} 的验证码：{code}")
        if DEV_ECHO:
            resp["code"] = code          # 本地联调用，生产请设 DEV_ECHO_CODE=0
    if not ok and MAIL_ON:
        raise HTTPException(500, "邮件发送失败，请稍后重试")
    return resp

@app.post("/api/register")
def register(c: Cred):
    email = c.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "请输入有效的邮箱")
    if not verify_code(email, c.code, "register"):
        raise HTTPException(400, "邮箱验证码不正确或已过期")
    name = (c.name or email.split("@")[0]).strip()[:20]
    salt = secrets.token_hex(16)
    pw_hash = hash_password(c.password, salt)
    now = int(time.time())
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO users(email,name,pw_hash,salt,created_at) VALUES(?,?,?,?,?)",
                (email, name, pw_hash, salt, now),
            )
            uid = cur.lastrowid
            conn.execute(
                "INSERT INTO progress(user_id,sent,vocab,updated_at) VALUES(?,?,?,?)",
                (uid, "[]", "[]", now),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "该邮箱已注册，请直接登录")
    return {"token": make_token(uid), "user": {"email": email, "name": name}}

@app.post("/api/password/reset")
def reset_password(c: Cred):
    email = c.email.strip().lower()
    if not verify_code(email, c.code, "reset"):
        raise HTTPException(400, "邮箱验证码不正确或已过期")
    salt = secrets.token_hex(16)
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            raise HTTPException(400, "该邮箱尚未注册")
        conn.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?",
                     (hash_password(c.password, salt), salt, row["id"]))
        conn.commit()
    clear_fail(email)
    return {"ok": True}

@app.post("/api/login")
def login(req: Request, c: Cred):
    email = c.email.strip().lower()
    ip = client_ip(req)
    if not rate_ok(f"login:ip:{ip}", 20, 600):
        raise HTTPException(429, "尝试过于频繁，请 10 分钟后再试")
    need_captcha = fail_count(email) >= 3 or fail_count(f"ip:{ip}") >= 3
    if need_captcha and not check_captcha(c.captcha_id, c.captcha_code):
        raise HTTPException(400, "请填写图形验证码")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not hmac.compare_digest(row["pw_hash"], hash_password(c.password, row["salt"])):
        note_fail(email); note_fail(f"ip:{ip}")
        raise HTTPException(400, "邮箱或密码不正确")
    clear_fail(email); clear_fail(f"ip:{ip}")
    return {"token": make_token(row["id"]), "user": {"email": email, "name": row["name"]}}

@app.get("/api/me")
def me(authorization: str = Header(default="")):
    uid = current_user(authorization)
    with db() as conn:
        row = conn.execute("SELECT email,name FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(401, "用户不存在")
    return {"user": {"email": row["email"], "name": row["name"]}}

@app.get("/api/progress")
def get_progress(authorization: str = Header(default="")):
    uid = current_user(authorization)
    with db() as conn:
        row = conn.execute("SELECT sent,vocab,updated_at FROM progress WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return {"sent": [], "vocab": [], "updated_at": 0}
    try:
        sent, vocab = json.loads(row["sent"]), json.loads(row["vocab"])
    except Exception:
        sent, vocab = [], []
    return {"sent": sent, "vocab": vocab, "updated_at": row["updated_at"]}

@app.put("/api/progress")
def put_progress(p: ProgressIn, authorization: str = Header(default="")):
    """
    进度上传：
    - 客户端带的 base_ts 与服务端一致 → 本地是最新的，直接覆盖（支持取消勾选同步）
    - 服务端比 base_ts 新 → 说明别的设备先同步过，退化为并集合并，避免覆盖掉别人的进度
    """
    uid = current_user(authorization)
    now = int(time.time())
    sent = [str(x)[:40] for x in p.sent][:5000]
    vocab = [str(x)[:80] for x in p.vocab][:5000]
    with db() as conn:
        row = conn.execute("SELECT sent,vocab,updated_at FROM progress WHERE user_id=?", (uid,)).fetchone()
        server_ts = row["updated_at"] if row else 0
        merged = False
        # base_ts=0 表示这台设备从未同步过，同样按合并处理，避免旧数据覆盖云端
        if row and (not p.base_ts or server_ts > p.base_ts):
            try:
                old_s, old_v = json.loads(row["sent"]), json.loads(row["vocab"])
            except Exception:
                old_s, old_v = [], []
            sent = sorted(set(old_s) | set(sent))
            vocab = sorted(set(old_v) | set(vocab))
            merged = True
        conn.execute(
            """INSERT INTO progress(user_id,sent,vocab,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET sent=excluded.sent,
               vocab=excluded.vocab, updated_at=excluded.updated_at""",
            (uid, json.dumps(sent, ensure_ascii=False), json.dumps(vocab, ensure_ascii=False), now),
        )
        conn.commit()
    return {"ok": True, "updated_at": now, "merged": merged,
            "counts": {"sent": len(sent), "vocab": len(vocab)}}

# ---------- 静态页面 ----------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    def index():
        return JSONResponse({"error": "缺少 public/index.html，请先运行 build.py 或复制页面文件"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    print(f"▶ 本地服务已启动： http://127.0.0.1:{port}")
    print(f"  数据库：{DB_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=port)
