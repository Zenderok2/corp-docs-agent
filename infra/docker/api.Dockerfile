FROM python:3.12.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/corp-docs
COPY backend/requirements.lock .
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY backend/pyproject.toml .
COPY backend/src ./src
RUN python -m pip install --no-cache-dir --no-deps . && \
    groupadd --gid 10001 agent && useradd --uid 10001 --gid 10001 --no-create-home agent && \
    mkdir -p /data && chown agent:agent /data
USER 10001:10001
CMD ["uvicorn", "corp_docs_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]
