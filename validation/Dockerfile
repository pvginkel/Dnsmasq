# The validation image: the whole workspace with every member's dev
# dependencies, which the Jenkins build's Test stage runs as a Job. Built from
# the repository root:
#   kaniko --context . --dockerfile validation/Dockerfile

FROM python:slim

RUN pip install --no-cache-dir uv

ENV UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /src
COPY . .
RUN uv sync --locked --all-packages --no-cache

ENV PATH=/src/.venv/bin:$PATH

ENTRYPOINT ["/src/validation/run.sh"]
