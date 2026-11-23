# Сделки СПб — каталог цен Росреестра

Отдельный сайт (не бот банкротства).

Локально из этой папки:

```bash
pip install -r requirements.txt
export ADMIN_PASSWORD='сложный-пароль'   # или bcrypt-хеш ($2b$…)
export ADMIN_SESSION_SECRET='длинный-случайный-секрет'
export ADMIN_ALLOWED_IPS='127.0.0.1,testclient'
export ADMIN_ALLOW_HTTP=1   # локальный HTTP + разрешение loopback в allowlist
uvicorn main:app --host 127.0.0.1 --port 8787
```

Откройте http://127.0.0.1:8787

Отдельный GitHub-репозиторий (не bankrot-bot): `spb-deals-catalog`.  
`/source.zip` выключен по умолчанию (`ENABLE_SOURCE_ZIP=1`, без БД в архиве, лимит 2/час).  
Деплой: [`../DEPLOY_DEALS_GITHUB.md`](../DEPLOY_DEALS_GITHUB.md), [`../DEPLOY_DEALS_TIMEWEB.md`](../DEPLOY_DEALS_TIMEWEB.md).

## Админка

`/admin` — тексты, страницы (`/p/slug`), ссылки меню.

Обязательные переменные:

- `ADMIN_PASSWORD` — plaintext или bcrypt-хеш; без неё вход закрыт
- `ADMIN_SESSION_SECRET` — подпись cookie сессии
- `ADMIN_ALLOWED_IPS` — **реальный публичный IP** оператора; без списка админка = 404

На публичном хосте **не** указывайте `127.0.0.1` / `::1` / `testclient` в
`ADMIN_ALLOWED_IPS`: за reverse-proxy peer часто общий, и админка открылась бы всем.
Такие адреса учитываются только при `ADMIN_ALLOW_HTTP=1` или `ADMIN_ALLOW_LOOPBACK=1`
(локальная разработка).

На HTTPS-хостинге cookie только по HTTPS.

Данные для хостинга: `data/deals.db.gz` (готовая SQLite). Исходный CSV в
публичный бандл не кладём. Прямой URL к БД закрыт.

## SEO

- `/robots.txt`, `/sitemap.xml` (индекс) + `/sitemaps/…` (фрагменты)
- canonical, Open Graph, JSON-LD
- `/search` → редирект на `/street/…`

Опционально:

```bash
export SITE_BASE_URL=https://example.com   # канонический origin для SEO
export TRUST_PROXY=1                       # читать X-Forwarded-For (правый hop)
export TRUSTED_PROXY_IPS=10.0.0.1          # peer-прокси (обязательно вместе с TRUST_PROXY)
export ENABLE_SOURCE_ZIP=1                 # опционально: /source.zip без БД
export ENABLE_API_DOCS=1                   # /docs и /openapi.json (выкл. по умолчанию)
export GOOGLE_SITE_VERIFICATION=...
export YANDEX_VERIFICATION=...
```

`TRUST_PROXY`: для allowlist/rate-limit берётся правый адрес в `X-Forwarded-For`,
не из списка `TRUSTED_PROXY_IPS`.
