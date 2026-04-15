FROM node:20-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && npm install -g mmx-cli@1.0.7 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY minimax_music_tool.py /app/minimax_music_tool.py

ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

CMD ["python3", "minimax_music_tool.py"]
