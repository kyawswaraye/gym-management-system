FROM python:3.14.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements ./requirements
RUN pip install --no-cache-dir -r requirements/production.txt
COPY . .

RUN useradd --system --uid 10001 api && chown -R api:api /app
USER api

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
