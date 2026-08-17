# GeminiForge Remote API

一个兼容 `register.py` 中 `CredentialSyncer` 的远程凭证存储 API。

## 快速启动

```bash
cd APIs
pip install -r requirements.txt
ADMIN_KEY=123456789 uvicorn app:app --host 0.0.0.0 --port 8000
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `ADMIN_KEY` | 是 | 管理密钥，对应 GitHub Actions 里的 `SYNC_KEY` |
| `DATABASE_PATH` | 否 | SQLite 数据库路径，默认 `data/accounts.db` |

## 接口

### POST /login

```bash
curl -X POST http://127.0.0.1:8000/login \
  -d 'admin_key=123456789'
```

成功后设置 Cookie，后续请求自动带认证。

### GET /admin/accounts-config

```bash
curl http://127.0.0.1:8000/admin/accounts-config \
  -H 'Cookie: admin_session=123456789'
```

或直接带请求头：

```bash
curl http://127.0.0.1:8000/admin/accounts-config \
  -H 'X-Admin-Key: 123456789'
```

响应：

```json
{ "accounts": [] }
```

### PUT /admin/accounts-config

```bash
curl -X PUT http://127.0.0.1:8000/admin/accounts-config \
  -H 'X-Admin-Key: 123456789' \
  -H 'Content-Type: application/json' \
  -d '[{"id":"user@example.com","csesidx":"...","config_id":"...","secure_c_ses":"...","host_c_oses":"...","expires_at":"..."}]'
```

## register.py 对接

在 GitHub Actions Secrets 中配置：

```text
SYNC_URL = https://你的域名或IP:端口
SYNC_KEY = 123456789
```

## 部署

- 本机测试：`uvicorn app:app --host 0.0.0.0 --port 8000`
- 生产环境建议放在 Nginx/Caddy 后面，启用 HTTPS。
