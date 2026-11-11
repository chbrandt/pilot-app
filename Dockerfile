FROM python:3.11-slim AS base

LABEL org.opencontainers.image.title="Pilot App"
LABEL org.opencontainers.image.description="Web GUI for HPC Pilot (HTTP client of pilot-api)"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .

# FLASK_SECRET_KEY must be overridden at runtime (e.g. via a Kubernetes Secret).
ENV FLASK_SECRET_KEY=dev-secret-change-in-production
ENV FLASK_PORT=5000
ENV FLASK_DEBUG=0
# The pilot-api service the GUI forwards every request to.
ENV API_BASE_URL=http://localhost:5000

EXPOSE 5000

CMD ["python", "main.py"]
