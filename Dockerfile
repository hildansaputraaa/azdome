FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY azdome-server.py .
COPY azdome-dashboard.html .
EXPOSE 8899
CMD ["python3", "azdome-server.py"]
