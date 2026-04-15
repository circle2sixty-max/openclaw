FROM node:20-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && npm install -g mmx-cli@1.0.7 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app.py /app/app.py

ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

CMD ["python3", "app.py"]
