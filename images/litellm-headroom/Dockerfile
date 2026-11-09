ARG LITELLM_VERSION=1.106.7
FROM ghcr.io/berriai/litellm:v${LITELLM_VERSION}

ARG HEADROOM_VERSION=0.43.1
RUN pip install --no-cache-dir "headroom-ai==${HEADROOM_VERSION}"

COPY headroom_proxy_callback.py /app/headroom_proxy_callback.py
