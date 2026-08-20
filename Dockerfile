FROM python:3.14-slim AS builder

WORKDIR /usr/src/app

# To restore QuickJS support for yt-dlp, uncomment this and the runtime COPY below.
# RUN apt-get update -y && \
#     apt-get install -y --no-install-recommends quickjs && \
#     rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pipenv

COPY Pipfile Pipfile.lock ./
RUN pipenv requirements --hash > requirements.txt

FROM python:3.14-slim

# Keep unbuffered if want more logs, keep buffered if want more performance
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /usr/src/app

RUN --mount=type=bind,from=builder,source=/usr/src/app/requirements.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --no-compile --require-hashes -r /tmp/requirements.txt && \
    python -m pip uninstall -y pip

COPY --from=mwader/static-ffmpeg:9.0 /ffmpeg /usr/local/bin/ffmpeg
# COPY --from=builder /usr/bin/qjs /usr/local/bin/qjs

RUN groupadd --gid 10001 sakamoto && \
    useradd --uid 10001 --gid sakamoto --create-home --shell /usr/sbin/nologin sakamoto && \
    mkdir -p data && \
    chown sakamoto:sakamoto data

COPY --chown=sakamoto:sakamoto main.py ./
COPY --chown=sakamoto:sakamoto functions ./functions/

USER sakamoto

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import socket; socket.create_connection(('discord.com', 443), timeout=5)"]

CMD ["python", "./main.py"]
