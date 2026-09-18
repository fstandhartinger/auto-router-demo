# The demo does not re-implement the router: it installs the published router
# package at a pinned commit and drives its real classifier, catalog and policy.
FROM python:3.12-slim

ARG ROUTER_REPO=https://github.com/fstandhartinger/auto-model-router.git
ARG ROUTER_REF=8e5ef22a9433e489a68eab91e757baaade68bcbc

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# curl stays in the image on purpose: the platform's container health check
# execs it inside the container, and a slim image has neither curl nor wget.

WORKDIR /srv

RUN git clone --filter=blob:none --no-checkout "$ROUTER_REPO" /tmp/router \
 && git -C /tmp/router checkout --quiet "$ROUTER_REF" \
 && cp -r /tmp/router/auto_router /srv/auto_router \
 && echo "$ROUTER_REF" > /srv/ROUTER_REF \
 && rm -rf /tmp/router

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY app /srv/app
COPY static /srv/static

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    AUTO_ROUTER_CACHE_DIR=/tmp/bench-cache

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
