FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
# editable install incl. dev deps: the compose volume mount of ./src stays live
# without rebuilds, and `docker compose run --rm elt python -m pytest -q` works
RUN pip install -e ".[dev]"

COPY alembic.ini ./
COPY alembic ./alembic

CMD ["dxb", "run-scheduler"]
