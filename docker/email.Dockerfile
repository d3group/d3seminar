FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 D3_MAIL_DIR=/mail
WORKDIR /app
RUN groupadd --gid 10001 seminar && useradd --uid 10001 --gid seminar --no-create-home seminar
COPY scripts/mailer.py scripts/seminar.py scripts/api_client.py scripts/mail_templates.py /app/scripts/
USER seminar
ENTRYPOINT ["python", "scripts/mailer.py"]
CMD ["--help"]
