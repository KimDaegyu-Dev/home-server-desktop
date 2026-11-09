ARG LITELLM_VERSION=1.106.7
ARG HEADROOM_BUILDER_IMAGE=python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20
FROM ${HEADROOM_BUILDER_IMAGE} AS headroom-deps

ARG HEADROOM_VERSION=0.43.1
ARG HEADROOM_AST_GREP_VERSION=0.51.1
ARG HEADROOM_AST_GREP_PY_VERSION=0.51.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cargo rustc \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --disable-pip-version-check --no-cache-dir --no-deps \
      --target=/opt/headroom "headroom-ai==${HEADROOM_VERSION}" \
      "ast-grep-cli==${HEADROOM_AST_GREP_VERSION}" \
      "ast-grep-py==${HEADROOM_AST_GREP_PY_VERSION}"

FROM docker.litellm.ai/berriai/litellm:v${LITELLM_VERSION}
COPY --from=headroom-deps /opt/headroom /opt/headroom
ENV PYTHONPATH=/opt/headroom

COPY headroom_proxy_callback.py /app/headroom_proxy_callback.py
