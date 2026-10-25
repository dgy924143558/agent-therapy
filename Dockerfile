FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"

ENV AGENT_THERAPY_DB=/data/agent_therapy.db \
    HOST=0.0.0.0 \
    PORT=8000
VOLUME /data
EXPOSE 8000

CMD ["agent-therapy-server"]
