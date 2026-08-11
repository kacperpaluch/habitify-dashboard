FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/habits.db \
    PORT=8080

WORKDIR /app
RUN apk add --no-cache tzdata && \
    addgroup -S habits && adduser -S habits -G habits && \
    mkdir -p /app/data && chown -R habits:habits /app
COPY --chown=habits:habits app.py /app/app.py
COPY --chown=root:root docker-entrypoint.py /app/docker-entrypoint.py
COPY --chown=habits:habits static /app/static

EXPOSE 8080
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O - http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
CMD ["python", "/app/app.py"]
