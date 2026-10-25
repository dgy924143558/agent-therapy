FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server,postgres]"

# Stats go to SQLite at /data by default, or to Postgres when DATABASE_URL is set
# (e.g. a free Neon database on a free Render instance, which has no disk).
# No VOLUME instruction: Railway rejects it. Mount persistent storage at /data instead
# (Railway volume, or `docker run -v agent-therapy-data:/data`).
# Platforms that inject PORT (Railway, Render, Fly) override the default below.
ENV AGENT_THERAPY_DB=/data/agent_therapy.db \
    HOST=0.0.0.0 \
    PORT=8000
RUN mkdir -p /data
EXPOSE 8000

CMD ["agent-therapy-server"]
