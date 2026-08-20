FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config
COPY examples ./examples

RUN pip install --no-cache-dir .

EXPOSE 5000/udp 9600/udp 1502/udp 15000/udp

CMD ["plcmock", "serve", "--config", "/app/config/example.yml"]
