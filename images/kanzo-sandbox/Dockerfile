ARG NODE_BASE_IMAGE=node:22.23.5-bookworm-slim@sha256:47725aca53d3295ec2d3be8f62726599b3033cc262edc2c7e429909b3ac599fc
ARG PNPM_VERSION=12.18.0
FROM ${NODE_BASE_IMAGE}
ARG PNPM_VERSION=12.18.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/pnpm/bin:${PATH}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv build-essential bash coreutils findutils grep sed gawk tar gzip git \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o 10000 -g 10000 /opt/kanzo \
    && install -d -m 0755 /opt/pnpm \
    && npm install --global --prefix /opt/pnpm "pnpm@${PNPM_VERSION}" \
    && test "$(/opt/pnpm/bin/pnpm --version)" = "${PNPM_VERSION}" \
    && chmod -R a+rX /opt/pnpm

COPY --chown=10000:10000 sandbox_init.py /opt/kanzo/sandbox_init.py
COPY --chown=10000:10000 sandbox_runner.py /opt/kanzo/sandbox_runner.py
RUN chmod 0444 /opt/kanzo/sandbox_init.py /opt/kanzo/sandbox_runner.py

USER 10000:10000
WORKDIR /workspace
