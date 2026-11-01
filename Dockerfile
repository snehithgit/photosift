# The Zima side of PhotoSift: API, built frontend and worker. The GPU laptop's agent and model
# service are not in this image; they run on the laptop and pull work from it.
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN npx -y pnpm@11.25.0 install --frozen-lockfile
COPY web/ ./
RUN npx -y pnpm@11.25.0 run build

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PHOTOSIFT_DB=/data/photosift.sqlite3 PHOTOSIFT_BROWSE_ROOTS=/library
# Only what the server and worker import, at the versions the tests ran against.
COPY pyproject.toml requirements.lock ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/runtime.txt \
    && pip install --no-cache-dir -c requirements.lock -r /tmp/runtime.txt \
    && rm /tmp/runtime.txt
COPY server ./server
COPY worker ./worker
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /data /library /exports
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"
CMD ["python", "-m", "uvicorn", "server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
