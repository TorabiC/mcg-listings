FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["/bin/sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-5050} --workers 2 --timeout 300"]
