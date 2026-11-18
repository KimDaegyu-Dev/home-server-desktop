ARG HERMES_BASE_IMAGE=nousresearch/hermes-agent:v0.23.1@sha256:1a355ebc8981a4b72a28f4e47e347ba4f0f172b43dc876e5768d175a07c67b22
FROM ${HERMES_BASE_IMAGE}
ARG OMH_SOURCE_COMMIT=e5b02595c076b505f71715d4796d28015c6e8c76

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Keep OMH's CLI dependencies isolated from Hermes' locked runtime venv. The
# Hermes PM drift check compares that venv against uv.lock and must stay clean.
RUN test -n "${OMH_SOURCE_COMMIT}" \
    && /opt/hermes/.venv/bin/python -m venv --system-site-packages /opt/omh/.venv \
    && /opt/omh/.venv/bin/python -m ensurepip --upgrade \
    && /opt/omh/.venv/bin/python -m pip install --no-cache-dir \
       "https://github.com/rlaope/oh-my-hermes/archive/${OMH_SOURCE_COMMIT}.tar.gz" \
    && /opt/omh/.venv/bin/omh --version \
    && ln -s /opt/omh/.venv/bin/omh /usr/local/bin/omh

# Hermes v0.23.1 declares pnpm required in its PM lockfile even in the
# docker-managed runtime image. Bake the pinned PM artifact into this derived
# image so every Hermes command passes its runtime drift check.
RUN HERMES_HOME=/opt/hermes HOME=/opt/hermes \
    /opt/hermes/.venv/bin/python -c 'from pm.install import ensure, installed_package; ensure("pnpm", explicit=True); assert installed_package("pnpm") is not None' \
    && chmod 0644 /opt/hermes/tools/facts.json \
    && chmod -R a+rX /opt/hermes/tools/pnpm-* \
    && /opt/hermes/.venv/bin/python -c 'from pm.install import activate; problems = activate(); print(problems); assert not problems, problems'

COPY patches/fail_closed_hygiene.py /opt/kanzo-agent/fail_closed_hygiene.py
RUN /opt/hermes/.venv/bin/python /opt/kanzo-agent/fail_closed_hygiene.py \
    && rm /opt/kanzo-agent/fail_closed_hygiene.py

COPY --chown=10000:10000 entrypoint.sh /opt/kanzo-agent/entrypoint.sh
RUN chmod 0555 /opt/kanzo-agent/entrypoint.sh

USER 10000:10000
ENV HERMES_HOME=/opt/data \
    HOME=/opt/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["/opt/kanzo-agent/entrypoint.sh"]
