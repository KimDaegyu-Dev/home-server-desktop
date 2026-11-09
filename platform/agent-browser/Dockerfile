FROM python:3.13-slim@sha256:48d884813cf44e927cf64c5529dd630dd3b18338c6dc727991eef9b71e934a20

ARG AGENT_BROWSER_VERSION
ARG AGENT_BROWSER_SHA256
ARG SUPERGATEWAY_VERSION
ARG SUPERGATEWAY_SHA256

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL --retry 3 "https://github.com/vercel-labs/agent-browser/releases/download/${AGENT_BROWSER_VERSION}/agent-browser-linux-x64" -o /usr/local/bin/agent-browser; \
    echo "${AGENT_BROWSER_SHA256}  /usr/local/bin/agent-browser" | sha256sum -c -; \
    chmod 0555 /usr/local/bin/agent-browser; \
    curl -fsSL --retry 3 "https://github.com/supercorp-ai/supergateway/releases/download/${SUPERGATEWAY_VERSION}/supergateway-linux-x64.tar.gz" -o /tmp/supergateway.tar.gz; \
    echo "${SUPERGATEWAY_SHA256}  /tmp/supergateway.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/supergateway.tar.gz -C /tmp; \
    install -m 0555 /tmp/supergateway /usr/local/bin/supergateway; \
    rm -f /tmp/supergateway.tar.gz /tmp/supergateway

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin agent-browser
ENV HOME=/home/agent-browser \
    XDG_CACHE_HOME=/home/agent-browser/.cache \
    AGENT_BROWSER_HEADED=false \
    AGENT_BROWSER_DOWNLOAD_PATH=/tmp/downloads

RUN set -eu; \
    /usr/local/bin/agent-browser install --with-deps; \
    mkdir -p /home/agent-browser/.cache /home/agent-browser/.config /tmp/downloads; \
    chown -R 10001:10001 /home/agent-browser /tmp/downloads

USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/supergateway"]
CMD ["--stdio", "agent-browser mcp", "--outputTransport", "streamableHttp", "--port", "8080", "--host", "0.0.0.0", "--streamableHttpPath", "/mcp", "--stateful", "--healthEndpoint", "/healthz", "--healthCheck", "server", "--apiKeyFile", "/run/secrets/agent-browser-token"]
