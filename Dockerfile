# The Zima side of PhotoSift: API, built frontend, worker and, if chosen, preparation. The GPU
# stages and model service are not in this image; they run on the laptop and pull work from it.
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN npx -y pnpm@11.25.0 install --frozen-lockfile
COPY web/ ./
RUN npx -y pnpm@11.25.0 run build

# An exact Python: its version is part of the preparation contract, so a newer one must never
# arrive by itself and stop preparation until the owner chooses again.
FROM python:3.12.16-slim-bookworm
WORKDIR /app
# SQLITE_TMPDIR: freeing space builds a temporary copy; keep it on the disk with the database.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PHOTOSIFT_DB=/data/photosift.sqlite3 PHOTOSIFT_BROWSE_ROOTS=/library SQLITE_TMPDIR=/data
# Only what the server and worker import, at the versions the tests ran against.
COPY pyproject.toml requirements.lock ./
RUN python -c "import tomllib; project = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(project['dependencies'] + project['optional-dependencies']['agent']))" > /tmp/runtime.txt \
    && pip install --no-cache-dir -c requirements.lock -r /tmp/runtime.txt \
    && rm /tmp/runtime.txt
COPY server ./server
COPY worker ./worker
# Only the preparation step: the rest of the agent runs on the laptop.
COPY agent/__init__.py agent/prepare.py ./agent/
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /data /library /exports
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"
CMD ["python", "-m", "uvicorn", "server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
