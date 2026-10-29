# local-ai-reels — образ для процессора (CPU).
# Собирает ролики из готовой озвучки и музыки (voice.engine: file, music.engine: file),
# распознаёт речь faster-whisper на процессоре, рендерит HyperFrames через системный Chromium.
#
#   docker build -t local-ai-reels .
#   docker run --rm -v "$PWD:/work" local-ai-reels run examples/povodok-templates --yes
#
# Полный конвейер на видеокарте (F5-TTS, MusicGen) — Dockerfile.cuda.
# Docker Hub недоступен или упёрся в лимит — базовые образы с зеркала:
#   docker build --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim-bookworm \
#                --build-arg NODE_IMAGE=mirror.gcr.io/library/node:22-bookworm-slim -t local-ai-reels .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}

ARG HF_VERSION=0.8.75
ARG EXTRAS=asr

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HYPERFRAMES_BROWSER_PATH=/usr/bin/chromium \
    HYPERFRAMES_NO_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    NPM_CONFIG_CACHE=/opt/npm-cache \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    HF_HOME=/cache/huggingface \
    TTS_MODELS=/models \
    PIPER_MODELS=/models/piper \
    RUACCENT_DIR=/models/ruaccent

# ffmpeg, Chromium для HyperFrames и шрифты с кириллицей (на случай, если Google Fonts недоступен)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg chromium fonts-dejavu-core fonts-liberation ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 # в контейнере Chromium работает от root и без user namespaces: песочница отключается флагом
 && printf 'export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox --disable-dev-shm-usage"\n' > /etc/chromium.d/docker

# Node.js 22 (npx запускает HyperFrames)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npx --version

# HyperFrames заранее в кэш npx, чтобы первый рендер не ждал скачивания
RUN mkdir -p "$NPM_CONFIG_CACHE" /cache/huggingface /models \
 && (npx --yes "hyperframes@${HF_VERSION}" --version || true) \
 && chmod -R a+rwX "$NPM_CONFIG_CACHE" /cache /models

WORKDIR /opt/local-ai-reels
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY reels ./reels
COPY tools ./tools
RUN pip install -e ".[${EXTRAS}]"

WORKDIR /work
ENTRYPOINT ["reels"]
CMD ["--help"]
