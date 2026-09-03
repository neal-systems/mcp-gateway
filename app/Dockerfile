FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN adduser --disabled-password --gecos "" --uid 10001 gateway \
    && mkdir -p /data/client_storage \
    && chown -R gateway:gateway /data

COPY gateway_server.py scope.py sample_data.json ./

USER gateway

CMD ["python", "gateway_server.py"]
