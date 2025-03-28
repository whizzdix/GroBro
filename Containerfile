FROM python:3.11-slim

WORKDIR /app

COPY grobro/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY grobro/ ./grobro/

WORKDIR /app/grobro

ENTRYPOINT ["python3", "ha_bridge.py"]
