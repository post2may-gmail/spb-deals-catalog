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
| `ADMIN_SESSION_SECRET` | длинная случайная строка (**одна и та же** между редеплоями, иначе выкидывает из сессии) |
| `ADMIN_BASE_PATH` | секретный путь без `/`, напр. `fuckingcrazyadmin` → вход `/fuckingcrazyadmin/login` |
| `SITE_BASE_URL` | `https://sdelkispb.ru` (без www) |
| `ADMIN_ALLOWED_IPS` | необязательно; если пусто — вход только по секретному URL + пароль |
| `TRUST_PROXY` | `1` |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` |
| `CMS_S3_BUCKET` | имя бакета из [Хранилище S3](https://timeweb.cloud/my/storage) (не отображаемое имя, а ID бакета) |
| `CMS_S3_ACCESS_KEY` | Access Key из настроек S3 / пользователя хранилища |
| `CMS_S3_SECRET_KEY` | Secret Key |
| `CMS_S3_ENDPOINT` | `https://s3.twcstorage.ru` (можно не задавать) |
| `CMS_S3_REGION` | `ru-1` |
| `CMS_S3_KEY` | необязательно, по умолчанию `cms.db` в корне бакета |

Обычный `/admin` после смены `ADMIN_BASE_PATH` не работает (и не светится).

У Timeweb Apps **нет постоянного диска** — `/my/storage` это S3.
После деплоя откройте админку → **Проверить S3**. Если OK — создайте страницу
и нажмите **Залить CMS в S3**; в бакете должен появиться файл `cms.db`.
В сообщении после сохранения страницы будет либо «Сохранено в s3://…», либо явная ошибка.

Чтобы узнать IP, который видит приложение, временно добавьте:

| `ADMIN_DEBUG_TOKEN` | случайная строка |

Откройте `https://sdelkispb.ru/__whoami?token=ВАШ_ТОКЕН` — в `client_ip`
будет адрес для `ADMIN_ALLOWED_IPS`. После настройки удалите `ADMIN_DEBUG_TOKEN`.
