# Built from the repository root: the build stage needs the workspace's lock.
#   kaniko --context . --dockerfile config-generator/Dockerfile

FROM python:slim AS build

RUN pip install --no-cache-dir uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY . .
RUN uv sync --locked --no-dev --no-editable --package config-generator

FROM python:slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

# The venv's interpreter links to the base image's, which both stages share.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PORT=9000

WORKDIR /app
COPY config-generator/main.py /app/main.py

EXPOSE 9000

ENTRYPOINT ["tini", "--"]
# `serve` is the long-running default; an init container overrides this with
# `python main.py generate` to render the static config once and exit.
CMD ["python", "main.py", "serve"]
