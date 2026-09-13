FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN adduser --disabled-password --gecos "" --uid 10001 gateway \
    && mkdir -p /data/state \
    && chown -R gateway:gateway /data

COPY gateway_server.py scope.py telemetry.py sample_data.json ./

USER gateway

CMD ["python", "gateway_server.py"]
