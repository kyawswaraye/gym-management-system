FROM python:3.14.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --uid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin api

COPY requirements/base.txt requirements/production.txt requirements/
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip==26.2.2 \
    && python -m pip install --no-cache-dir -r requirements/production.txt \
    && python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

COPY gunicorn.conf.py /app/gunicorn.conf.py
COPY manage.py /app/manage.py
COPY config /app/config
COPY apps /app/apps
COPY docker/healthcheck.py /app/docker/healthcheck.py

RUN chown -R api:api /app
ENV HOME=/tmp
USER api

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "docker/healthcheck.py"]

CMD ["gunicorn", "--config", "gunicorn.conf.py", "config.wsgi:application"]
