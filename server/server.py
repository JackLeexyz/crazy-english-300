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
import math
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

# ---------- 配置：优先读 .env，环境变量可覆盖 ----------
def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_dotenv(BASE / ".env")

# ---------- 邮件发送配置（未配置时走开发模式：验证码只打印到控制台） ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
MAIL_ON = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)
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

# 说明：验证码用自绘「单线笔画字体」渲染成 <path>，绝不输出 <text>。
# 每次生成都会对笔画做低频平滑随机扰动 + 随机旋转/倾斜/缩放，并把变换烘焙进坐标
# （不输出 <g transform>），因此同一个字符每次的 path d 都不同，无法用 d 做指纹查表。
def _cap_arc(cx, cy, rx, ry, a0, a1, n=18):
    """角度按数学惯例（0°=右，逆时针为正）；y 轴向下，故取负号。"""
    pts = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        pts.append((cx + rx * math.cos(a), cy - ry * math.sin(a)))
    return pts

def _cap_ell(cx, cy, rx, ry, n=20):
    return _cap_arc(cx, cy, rx, ry, 0, 360, n)

def _cap_smooth(pts, seg=6):
    """Catmull-Rom 平滑：粗糙折线 → 平滑曲线。"""
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(seg):
            t = j / seg
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
            ))
    out.append(P[-2])
    return out

def _cap_poly(*pts):
    return [tuple(p) for p in pts]

def _cap_curve(pts, seg=6):
    return _cap_smooth(pts, seg)

# 字形表：坐标 x∈[0,6] y∈[0,10]，y 向下
_CAP_GLYPHS = {
    "A": [_cap_poly((0, 10), (3, 0.4)), _cap_poly((3, 0.4), (6, 10)), _cap_poly((1.05, 6.9), (4.95, 6.9))],
    "B": [_cap_poly((0, 0), (0, 10)), _cap_arc(0, 2.6, 3.1, 2.3, 90, -90), _cap_arc(0, 7.4, 3.6, 2.6, 90, -90)],
    "C": [_cap_arc(3.15, 5, 2.55, 4.6, 45, 315)],
    "D": [_cap_poly((0, 0), (0, 10)), _cap_arc(0, 5, 3.9, 4.7, 90, -90)],
    "E": [_cap_poly((0, 0), (0, 10)), _cap_poly((0, 0.35), (4.9, 0.35)), _cap_poly((0, 5), (3.9, 5)), _cap_poly((0, 9.65), (4.9, 9.65))],
    "F": [_cap_poly((0, 0), (0, 10)), _cap_poly((0, 0.35), (4.9, 0.35)), _cap_poly((0, 5), (3.9, 5))],
    "G": [_cap_arc(3.15, 5, 2.55, 4.6, 45, 352), _cap_poly((5.66, 5.85), (5.85, 5.2), (5.85, 4.65), (3.95, 4.65))],
    "H": [_cap_poly((0, 0), (0, 10)), _cap_poly((5.2, 0), (5.2, 10)), _cap_poly((0, 5), (5.2, 5))],
    "J": [_cap_poly((3.6, 0), (3.6, 8.5)), _cap_arc(1.8, 8.5, 1.8, 1.5, 0, -180)],
    "K": [_cap_poly((0, 0), (0, 10)), _cap_poly((5.2, 0), (0.7, 5.6)), _cap_poly((2.0, 4.3), (5.3, 10))],
    "L": [_cap_poly((0, 0), (0, 10)), _cap_poly((0, 10), (5.0, 10))],
    "M": [_cap_poly((0, 10), (0, 0.4), (2.8, 6.7), (5.6, 0.4), (5.6, 10))],
    "N": [_cap_poly((0, 10), (0, 0.4), (5.0, 9.6), (5.0, 0))],
    "P": [_cap_poly((0, 10), (0, 0)), _cap_arc(0, 2.7, 3.3, 2.4, 90, -90)],
    "Q": [_cap_ell(2.95, 5.0, 2.95, 4.7), _cap_poly((3.7, 7.4), (6.0, 10.3))],
    "R": [_cap_poly((0, 10), (0, 0)), _cap_arc(0, 2.7, 3.3, 2.4, 90, -90), _cap_poly((1.5, 5.15), (5.35, 10))],
    "S": [_cap_curve([(5.2, 1.35), (4.35, 0.3), (2.75, 0.0), (1.2, 0.5), (0.5, 1.75),
                      (0.8, 3.15), (2.05, 4.15), (3.9, 4.85), (5.15, 5.9), (5.5, 7.35),
                      (4.8, 8.75), (3.05, 9.6), (1.35, 9.35), (0.35, 8.25)], 5)],
    "T": [_cap_poly((0, 0.35), (5.6, 0.35)), _cap_poly((2.8, 0.35), (2.8, 10))],
    "U": [_cap_poly((0, 0), (0, 7.0)), _cap_arc(2.7, 7.0, 2.7, 3.0, 180, 360), _cap_poly((5.4, 0), (5.4, 7.0))],
    "V": [_cap_poly((0, 0.4), (2.9, 10)), _cap_poly((2.9, 10), (5.8, 0.4))],
    "W": [_cap_poly((0, 0.4), (1.5, 10), (3.0, 4.2), (4.5, 10), (6.0, 0.4))],
    "X": [_cap_poly((0, 0.4), (5.6, 10)), _cap_poly((5.6, 0.4), (0, 10))],
    "Y": [_cap_poly((0, 0.4), (2.8, 5.5)), _cap_poly((5.6, 0.4), (2.8, 5.5)), _cap_poly((2.8, 5.5), (2.8, 10))],
    "Z": [_cap_poly((0, 0.35), (5.45, 0.35)), _cap_poly((5.45, 0.35), (0, 9.65)), _cap_poly((0, 9.65), (5.45, 9.65))],
    "2": [_cap_curve([(0.45, 1.95), (1.15, 0.7), (2.75, 0.05), (4.4, 0.55), (5.25, 1.85),
                      (5.15, 3.3), (3.6, 5.4), (0.35, 9.65), (5.5, 9.65)], 5)],
    "3": [_cap_curve([(0.5, 1.6), (1.5, 0.4), (3.1, 0.0), (4.7, 0.6), (5.4, 2.0), (5.0, 3.5), (3.4, 4.5), (2.35, 4.6)], 5),
          _cap_curve([(2.35, 4.6), (4.0, 4.75), (5.35, 5.9), (5.55, 7.6), (4.7, 9.2), (3.0, 9.9), (1.3, 9.6), (0.4, 8.5)], 5)],
    "4": [_cap_poly((4.35, 0.35), (0.3, 6.7), (5.7, 6.7)), _cap_poly((4.35, 0.35), (4.35, 10))],
    "5": [_cap_poly((5.2, 0.4), (0.75, 0.4), (0.55, 4.3)),
          _cap_curve([(0.55, 4.3), (2.2, 3.85), (4.0, 4.5), (5.2, 5.85), (5.4, 7.5), (4.4, 9.1), (2.7, 9.9), (1.1, 9.5), (0.3, 8.6)], 5)],
    "6": [_cap_curve([(5.0, 0.8), (3.8, 0.05), (2.2, 0.35), (1.1, 1.7), (0.55, 3.9), (0.5, 6.3),
                      (1.1, 8.4), (2.6, 9.75), (4.2, 9.5), (5.2, 8.1), (5.15, 6.4), (4.1, 5.3),
                      (2.6, 5.15), (1.4, 6.0), (0.62, 7.35)], 5)],
    "7": [_cap_poly((0.35, 0.4), (5.55, 0.4)), _cap_poly((5.55, 0.4), (2.3, 10)), _cap_poly((4.25, 5.3), (1.55, 5.3))],
    "8": [_cap_ell(2.85, 2.75, 2.4, 2.55, 18), _cap_ell(2.85, 7.2, 2.95, 2.75, 18)],
    "9": [_cap_ell(3.0, 3.3, 2.9, 2.9, 18), _cap_poly((5.9, 3.3), (5.8, 6.1), (5.0, 8.4), (3.5, 9.75), (1.85, 9.5), (0.8, 8.3))],
}

CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混的 I O 0 1
CAPTCHA_COLORS = ["#e0432f", "#2563eb", "#059669", "#7c3aed", "#d97706", "#0f766e"]
CAPTCHA_NOISE_COLORS = ["#e2e8f0", "#cbd5e1", "#dbeafe", "#fee2e2", "#d1fae5"]

def _cap_wobble(pts, amp):
    """沿路径做低频平滑随机扰动 —— 这是让每次 path d 都不同的关键。"""
    if len(pts) < 3:
        return pts
    k = random.randint(3, 5)
    ctrl = [random.uniform(-1.0, 1.0) for _ in range(k + 3)]
    n = len(pts)
    out = []
    for i, (x, y) in enumerate(pts):
        t = i / (n - 1) * k
        i0 = min(int(t), k + 1)
        f = t - i0
        s = f * f * (3 - 2 * f)                      # smoothstep
        a, b = ctrl[i0], ctrl[i0 + 1]
        off = (a + (b - a) * s) * amp
        if i == 0:
            dx, dy = pts[1][0] - x, pts[1][1] - y
        elif i == n - 1:
            dx, dy = x - pts[-2][0], y - pts[-2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        L = math.hypot(dx, dy) or 1.0
        out.append((x - dy / L * off, y + dx / L * off))
    return out

def _cap_thin(pts, keep=2):
    """抽稀，控制 path 字符串长度。只对密点曲线生效；
    稀疏折线（M/W/N 的顶点）抽稀会毁掉字形，必须原样保留。"""
    if len(pts) < 24:
        return pts
    out = pts[::keep]
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out

def make_captcha_svg(code, width=126, height=46):
    parts = []
    slot = (width - 12) / len(code)
    for i, ch in enumerate(code):
        glyph = _CAP_GLYPHS.get(ch)
        if not glyph:
            continue
        # 该字符实例的随机变换参数（全部烘焙进坐标，不输出 transform）
        cx0 = 6 + slot * (i + 0.5) + random.uniform(-1.2, 1.2)
        cy0 = height / 2 + random.uniform(-1.4, 1.4)
        sc = random.uniform(2.85, 3.35)      # 纵向缩放
        scx = sc * 0.85                      # 横向收窄（避免旋转后越界重叠）
        rot = math.radians(random.uniform(-18, 18))
        skew = random.uniform(-0.16, 0.16)
        ca, sa = math.cos(rot), math.sin(rot)
        color = random.choice(CAPTCHA_COLORS)
        for stroke in glyph:
            pts = _cap_wobble(_cap_thin(stroke, 2), amp=0.42 / 3.0 * sc)
            xy = []
            for gx, gy in pts:
                X = (gx - 3.0) * scx
                Y = (gy - 5.0) * sc
                X += Y * skew                              # 倾斜
                X, Y = X * ca - Y * sa, X * sa + Y * ca    # 旋转
                xy.append((cx0 + X, cy0 + Y))
            d = "M" + " ".join(f"{x:.1f} {y:.1f}" for x, y in xy)
            parts.append(
                f'<path d="{d}" fill="none" stroke="{color}" '
                f'stroke-width="{random.uniform(2.5, 3.5):.1f}" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'opacity="{random.uniform(0.82, 1.0):.2f}"/>'
            )
    # 干扰线 / 干扰点（同样是 path，深浅颜色混合，增加过滤难度）
    for _ in range(random.randint(4, 6)):
        x1, y1 = random.uniform(0, width), random.uniform(0, height)
        x2, y2 = random.uniform(0, width), random.uniform(0, height)
        mx = (x1 + x2) / 2 + random.uniform(-14, 14)
        my = (y1 + y2) / 2 + random.uniform(-12, 12)
        parts.append(
            f'<path d="M{x1:.1f} {y1:.1f}Q{mx:.1f} {my:.1f} {x2:.1f} {y2:.1f}" fill="none" '
            f'stroke="{random.choice(CAPTCHA_NOISE_COLORS)}" stroke-width="{random.uniform(1.0, 1.8):.1f}" '
            f'opacity="{random.uniform(0.55, 0.85):.2f}"/>'
        )
    for _ in range(3):
        x, y = random.uniform(4, width - 4), random.uniform(4, height - 4)
        parts.append(
            f'<path d="M{x:.1f} {y:.1f}q{random.uniform(-5, 5):.1f} {random.uniform(-4, 4):.1f} '
            f'{random.uniform(-6, 6):.1f} {random.uniform(-5, 5):.1f}" fill="none" '
            f'stroke="{random.choice(CAPTCHA_COLORS)}" stroke-width="{random.uniform(1.6, 2.6):.1f}" '
            f'opacity="{random.uniform(0.28, 0.5):.2f}"/>'
        )
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'style="background:#f8fafc;border-radius:8px">' % (width, height, width, height)
            ) + "".join(parts) + "</svg>"

def new_captcha():
    for k, (_, exp) in list(CAPTCHA.items()):
        if exp < time.time():
            CAPTCHA.pop(k, None)
    code = "".join(random.choice(CAPTCHA_CHARS) for _ in range(4))
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
