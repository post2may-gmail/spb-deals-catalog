FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=80

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p data/processed templates static

# Только runtime-файлы (без исходных CSV и утилит импорта).
COPY main.py app.py admin_app.py security.py seo.py cms.py db.py \
     normalize.py periods.py clean.py street_titles.py __init__.py ./
COPY templates/ templates/
COPY static/ static/
COPY data/deals.db.gz data/street_titles.json data/
COPY data/processed/.gitkeep data/processed/

EXPOSE 80
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-80}"]
