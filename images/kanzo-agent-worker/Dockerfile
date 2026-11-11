ARG HERMES_BASE_IMAGE=nousresearch/hermes-agent:v0.23.1@sha256:1a355ebc8981a4b72a28f4e47e347ba4f0f172b43dc876e5768d175a07c67b22
FROM ${HERMES_BASE_IMAGE}
ARG OMH_SOURCE_COMMIT=e5b02595c076b505f71715d4796d28015c6e8c76

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Install OMH into Hermes' own interpreter so its plugin bridge and Kanban
# board engine are importable from agent and gateway processes.
RUN test -n "${OMH_SOURCE_COMMIT}" \
    && /opt/hermes/.venv/bin/python -m ensurepip --upgrade --default-pip \
    && /opt/hermes/.venv/bin/python -m pip install --no-cache-dir \
       "https://github.com/rlaope/oh-my-hermes/archive/${OMH_SOURCE_COMMIT}.tar.gz" \
    && /opt/hermes/.venv/bin/omh --version

COPY --chown=10000:10000 entrypoint.sh /opt/kanzo-agent/entrypoint.sh
RUN chmod 0555 /opt/kanzo-agent/entrypoint.sh

USER 10000:10000
ENV HERMES_HOME=/opt/data \
    HOME=/opt/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["/opt/kanzo-agent/entrypoint.sh"]
