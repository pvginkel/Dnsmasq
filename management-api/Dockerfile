# Built from the repository root: the build stage needs the workspace's lock.
#   kaniko --context . --dockerfile management-api/Dockerfile

FROM python:slim AS build

RUN pip install --no-cache-dir uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
COPY . .
RUN uv sync --locked --no-dev --no-editable --package management-api

FROM python:slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

# The venv's interpreter links to the base image's, which both stages share.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PORT=8080

WORKDIR /app
COPY management-api/main.py /app/main.py

EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py"]
