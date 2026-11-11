ARG HERMES_BASE_IMAGE=nousresearch/hermes-agent:v0.23.1@sha256:1a355ebc8981a4b72a28f4e47e347ba4f0f172b43dc876e5768d175a07c67b22
FROM ${HERMES_BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=10000:10000 worker.py /opt/kanzo-agent/worker.py
COPY --chown=10000:10000 entrypoint.sh /opt/kanzo-agent/entrypoint.sh
RUN chmod 0555 /opt/kanzo-agent/entrypoint.sh /opt/kanzo-agent/worker.py

USER 10000:10000
ENV HERMES_HOME=/opt/data \
    HOME=/opt/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["/opt/kanzo-agent/entrypoint.sh"]
