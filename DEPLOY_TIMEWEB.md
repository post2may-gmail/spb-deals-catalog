# Timeweb Cloud Apps

1. Repo: `spb-deals-catalog`, branch `main`, Root: `/`
2. Start:

```text
uvicorn main:app --host 0.0.0.0 --port 8000
```

3. Env:

| Name | Value |
|------|--------|
| `PORT` | `8000` |
| `ADMIN_PASSWORD` | пароль админки |
| `ADMIN_SESSION_SECRET` | любая непустая строка |
| `SITE_BASE_URL` | ваш домен, `https://example.ru` |
| `ADMIN_ALLOWED_IPS` | ваши публичные IP через запятую |
| `TRUST_PROXY` | `1` |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` |

Без `TRUST_PROXY` + `TRUSTED_PROXY_IPS` за Caddy Timeweb все запросы выглядят
как localhost → `/admin` всегда 404, даже если IP в allowlist верные.

`ADMIN_ALLOWED_IPS` — только ваши реальные IP (не 127.0.0.1). Узнать: https://ifconfig.me
