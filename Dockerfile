FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt gunicorn

COPY backend/ backend/
COPY frontend/ frontend/
RUN mkdir -p backend/data

WORKDIR /app/backend

ENV PORT=5000
EXPOSE 5000

# liveness: the process answers (/healthz). Readiness (/readyz: database writable, job worker, stable secret) is
# for load balancers and the deploy check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3   CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','5000'), timeout=4)" || exit 1

# One worker, many threads: the live working-set cache, new-alarm detection, the
# change counter behind /api/events and the sign-in rate limiter are per-process,
# so a single process keeps them consistent. Scale with threads (or put Redis
# behind these in a later version). Every open browser tab holds one thread for
# its push stream (/api/events), so the pool is sized for tabs + requests.
CMD ["sh", "-c", "gunicorn -w 1 --threads ${CAMVIEW_THREADS:-48} --timeout 60 --keep-alive 5 -b 0.0.0.0:${PORT} app:app"]
