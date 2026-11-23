# Timeweb Cloud Apps

1. Repo: `spb-deals-catalog`, branch `main`, Root: `/`
2. **Start command** (без `${…}` — Timeweb не раскрывает shell-подстановки):

```text
uvicorn main:app --host 0.0.0.0 --port 8000
```

3. Env:
   - `PORT=8000` (если поле есть отдельно)
   - `ADMIN_SESSION_SECRET` — любая непустая строка
   - `ADMIN_PASSWORD` — пароль админки
   - `SITE_BASE_URL` — финальный домен, например `https://example.ru`
   - `ADMIN_ALLOWED_IPS` — ваш публичный IP (не 127.0.0.1)
