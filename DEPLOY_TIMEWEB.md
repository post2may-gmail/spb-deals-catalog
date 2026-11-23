# Timeweb Cloud Apps

Отдельный репозиторий каталога (не бот банкротства).

1. Apps → Backend → FastAPI
2. GitHub → этот репозиторий, ветка `main`
3. Root directory: `/`
4. Build: `pip3 install --upgrade -r requirements.txt`
5. Start: `uvicorn main:app --host 0.0.0.0 --port 80`
6. Env: `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `PORT=80`;
   после деплоя `SITE_BASE_URL`, `ADMIN_ALLOWED_IPS` (ваш публичный IP, не 127.0.0.1)
