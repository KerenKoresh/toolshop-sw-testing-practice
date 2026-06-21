FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8080
EXPOSE 8080

# Use the PORT env var that hosting platforms inject
CMD gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2
