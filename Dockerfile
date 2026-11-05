# One container: builds the React dashboard, then serves it with the FastAPI backend.
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/paatti backend/paatti
RUN pip install --no-cache-dir ./backend
COPY --from=web /app/frontend/dist frontend/dist
COPY evals evals
RUN useradd --create-home paatti && mkdir -p /app/backend/data && chown -R paatti /app/backend
USER paatti
WORKDIR /app/backend
ENV PAATTI_STATIC_DIR=/app/frontend/dist
EXPOSE 8000
# Render/Railway/Fly inject $PORT; default 8000 locally.
CMD ["sh", "-c", "uvicorn paatti.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
