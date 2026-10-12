FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    D3_DATA_DIR=/data \
    D3_CONFIG=/data/config.json \
    D3_BIND_HOST=0.0.0.0
WORKDIR /app
RUN groupadd --gid 10001 seminar && useradd --uid 10001 --gid seminar --no-create-home seminar \
    && mkdir /data && chown seminar:seminar /data
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/server.py scripts/seminar.py scripts/organizer.py scripts/presentations.py scripts/backup.py scripts/users.py scripts/models.py scripts/database.py scripts/api_client.py scripts/email_queue.py /app/scripts/
COPY alembic.ini /app/alembic.ini
COPY migrations/ /app/migrations/
COPY site/ /app/site/
RUN chmod -R a+rX /app/site
COPY docker/backend-entrypoint.sh /app/docker-entrypoint.sh
USER seminar
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/health', timeout=2)" || exit 1
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["python", "scripts/server.py"]
