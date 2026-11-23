# Timeweb Cloud Apps

1. Repo: `spb-deals-catalog`, branch `main`, Root: `/`
2. **Start command (обязательно):**
   `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`
3. Env:
   - `PORT=8000`
   - `ADMIN_SESSION_SECRET` — любая непустая строка
   - `ADMIN_PASSWORD` — пароль админки
   - `SITE_BASE_URL` — финальный домен, например `https://example.ru`
   - `ADMIN_ALLOWED_IPS` — ваш публичный IP (не 127.0.0.1)

Не используйте `uvicorn app:app`, если в логах «No HTTP ports» / restart loop.
