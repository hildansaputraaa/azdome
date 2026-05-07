FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir mysql-connector-python python-dotenv
WORKDIR /app
COPY azdome-server.py .
COPY azdome-dashboard.html .
COPY azdome-admin.html .
EXPOSE 8899
CMD ["python3", "azdome-server.py"]
