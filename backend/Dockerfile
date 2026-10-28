# DemoBank API (FastAPI). Secrets come from the root .env at run time (docker compose env_file); none are baked in.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "mcp>=2.3,<3"
COPY app ./app
COPY evaluation ./evaluation
COPY scripts ./scripts
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
