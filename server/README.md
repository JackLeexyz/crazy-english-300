# 疯狂英语300句 · 本地后端（账号 + 进度云同步）

## 快速开始

```bash
pip install -r requirements.txt
python server.py                 # 默认 http://127.0.0.1:8000
PORT=8080 python server.py       # 换端口
```

打开 http://127.0.0.1:8000 即可使用（页面由后端一起托管）。

## 做了什么

| 能力 | 说明 |
|---|---|
| 注册 | 邮箱验证码验证后开通，密码 pbkdf2_hmac 加盐哈希（12 万次迭代），JWT 有效期 30 天 |
| 登录 | 邮箱 + 密码；**连续失败 3 次后要求图形验证码**，防暴力破解 |
| 找回密码 | 邮箱验证码验证后重置 |
| 图形验证码 | 服务端生成 SVG（扭曲字符 + 干扰线），5 分钟有效、**用后即焚**，防止刷邮件 |
| 限流 | 每邮箱 1 小时最多 3 次验证码；每 IP 每小时 10 次、登录 10 分钟 20 次 |
| 进度云同步 | 登录后自动合并，勾选后 1.2 秒防抖上传；**带时间戳守卫防覆盖** |
| 游客模式 | 不登录照样能用，进度存浏览器本地；**没有后端时账号入口自动隐藏** |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/captcha` | 获取图形验证码 `{id, svg}` |
| POST | `/api/email-code` | 申请邮箱验证码 `{email, purpose:"register"\|"reset", captcha_id, captcha_code}` |
| POST | `/api/register` | 注册（需邮箱验证码），返回 token |
| POST | `/api/login` | 登录；失败 3 次后需带 `captcha_id/captcha_code` |
| POST | `/api/password/reset` | 凭邮箱验证码重置密码 |
| GET | `/api/me` | 当前用户（需 Bearer token） |
| GET | `/api/progress` | 拉取云端进度（返回 `updated_at`） |
| PUT | `/api/progress` | 上传进度 `{sent:[...], vocab:[...], base_ts}` |

## 邮件服务配置

未配置 SMTP 时走**开发模式**：验证码打印到控制台并回显在接口里（本地联调用）。

```bash
export SMTP_HOST=smtp.qq.com
export SMTP_PORT=465
export SMTP_USER=你的邮箱@qq.com
export SMTP_PASS=邮箱授权码      # 不是登录密码，在邮箱设置里生成
export DEV_ECHO_CODE=0           # 生产环境务必关掉回显
python server.py
```

## 多端同步防覆盖

进度上传带 `base_ts`（客户端上次同步到的服务端时间戳）：

- 服务端比 `base_ts` 新，或客户端从未同步过（`base_ts=0`）→ **并集合并**，旧设备不会冲掉新进度
- 否则 → 正常覆盖，支持"取消勾选"同步到云端

## 设计要点

- **登录是可选的**：不影响"发链接即用"这个核心体验，登录只解决多端同步
- **前后端同源**：页面由后端托管，前端用相对路径调 `/api/*`，换域名不用改代码
- **离线优先**：后端不可用时前端静默降级为纯本地模式，不报错、不弹窗打扰
- **同步策略**：登录瞬间做一次**并集合并**（本机 ∪ 云端），之后以本机为准增量上传

## 数据与文件

- `data.db`：SQLite 数据库（用户表 + 进度表），首次运行自动创建
- `.secret`：JWT 签名密钥，首次运行自动生成（**不要提交到仓库**）
- `public/index.html`：托管的页面（从 `crazy300/index.html` 复制，重新构建后需再复制一次）

## 上生产前还要补

1. `.secret` 换成环境变量 `JWT_SECRET`，并加入备份
2. 加 HTTPS（Nginx 反向代理 + 证书）
3. 注册加邮箱验证码 / 图形验证码，防刷号
4. 登录失败次数限制、接口限流
5. 定期备份 `data.db`
6. 用户量上千后把 SQLite 换成 MySQL / PostgreSQL
