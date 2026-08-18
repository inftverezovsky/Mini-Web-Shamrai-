FROM python:3.11-slim@sha256:00af38ae2ed311628970782e8a2d7f014d8909dbc63cb97bc0a158187f4db045

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Avoid mutable OS package repositories. All compiled runtime dependencies have
# wheels; the few source-only packages in the hash lock are pure Python.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Copy source code
COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/static \
    && chown -R appuser:appuser /app

# Expose port
EXPOSE 8000

USER appuser

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
