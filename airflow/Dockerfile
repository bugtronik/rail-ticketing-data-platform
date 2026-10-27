# Airflow image of the pipeline: the official Airflow image, plus the
# project (generator, extractor) and dbt in a virtualenv of their own.
#
# Why a separate virtualenv: Airflow pins its dependencies with a
# constraints file (Airflow 3.3.2 pins numpy 2.5.3 and psycopg 3.3.5) and
# uv.lock pins the project's (numpy >= 2.5.4, psycopg >= 3.3.6): no single
# environment satisfies both. Tasks call the tools by their full path
# (/opt/pipeline/.venv/bin/...), so the two environments never mix.
#
# Build context: the repository root (see docker-compose.yml).
FROM apache/airflow:3.3.2-python3.12

# uv, copied from its official image: one static binary, pinned version.
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

USER root
RUN mkdir -p /opt/pipeline && chown airflow:0 /opt/pipeline
USER airflow
WORKDIR /opt/pipeline

# Use the image's Python 3.12 (never download one), precompile .py files so
# the first run starts fast, keep no download cache in the image.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1

# 1. Dependencies only. This layer is rebuilt only when uv.lock changes,
#    not at each code change: rebuilding after editing a model takes seconds.
#    --locked: fail if uv.lock is out of date, never resolve again.
#    --no-default-groups --group transform: runtime + dbt, no dev tools.
COPY --chown=airflow:0 pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-default-groups --group transform --no-install-project

# 2. The project itself, installed as a regular package (not editable):
#    the image does not depend on the source folder staying in place.
COPY --chown=airflow:0 src ./src
RUN uv sync --locked --no-default-groups --group transform --no-editable

# 3. What the tools read at run time (relative to /opt/pipeline).
COPY --chown=airflow:0 config ./config
COPY --chown=airflow:0 transform ./transform
COPY --chown=airflow:0 airflow/dags /opt/airflow/dags

ENV PIPELINE_HOME=/opt/pipeline
# Back to Airflow's own working directory for the Airflow components.
WORKDIR /opt/airflow
