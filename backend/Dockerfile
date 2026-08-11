FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
