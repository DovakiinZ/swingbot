FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p logs data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s CMD curl -f http://localhost:8080/login || exit 1
CMD ["python", "run.py", "--lang", "en"]
