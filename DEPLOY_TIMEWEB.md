# Timeweb Cloud Apps

1. Repo: `spb-deals-catalog`, branch `main`, Root: `/`
2. Start:

```text
uvicorn main:app --host 0.0.0.0 --port 8000
```

3. Env (обязательно для админки за Caddy):

| Name | Value |
|------|--------|
| `PORT` | `8000` |
| `ADMIN_PASSWORD` | пароль админки |
| `ADMIN_SESSION_SECRET` | длинная случайная строка |
| `SITE_BASE_URL` | `https://sdelkispb.ru` (без www) |
| `ADMIN_ALLOWED_IPS` | ваш **публичный** IP (https://ifconfig.me), не 127.0.0.1 |
| `TRUST_PROXY` | `1` |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` |
| `CMS_DB_PATH` | `/data/spb_cms.db` (нужен том `/data`, иначе страницы пропадут после редеплоя) |

Без `TRUST_PROXY` + `TRUSTED_PROXY_IPS` за Caddy Timeweb все запросы выглядят
как localhost → `/admin` всегда 404.

Страницы CMS («О проекте» и меню) хранятся в SQLite. Без постоянного тома
файл лежит в `/tmp` и сбрасывается при редеплое. Подключите том к `/data` и
задайте `CMS_DB_PATH=/data/spb_cms.db`. Инстансов приложения лучше держать 1.

Чтобы узнать IP, который видит приложение, временно добавьте:

| `ADMIN_DEBUG_TOKEN` | случайная строка |

Откройте `https://sdelkispb.ru/__whoami?token=ВАШ_ТОКЕН` — в `client_ip`
будет адрес для `ADMIN_ALLOWED_IPS`. После настройки удалите `ADMIN_DEBUG_TOKEN`.
