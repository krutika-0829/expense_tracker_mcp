# claude-agent-sdk requires Python 3.10+
FROM python:3.11-slim
 
WORKDIR /app
 
# psycopg2-binary needs libpq at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
 

CMD ["uvicorn", "agent_api:app", "--host", "0.0.0.0", "--port", "8000"]