FROM python:3.12-slim

RUN useradd -m -u 1000 user

WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user app.py .

USER user
ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    SENDS_PER_CONN=25 \
    ACCOUNT_CONCURRENCY=3 \
    DAILY_CAP=400

EXPOSE 7860

CMD ["gunicorn", "-w", "1", "--threads", "32", \
     "-b", "0.0.0.0:7860", "--timeout", "0", \
     "--graceful-timeout", "30", "--access-logfile", "-", "app:app"]