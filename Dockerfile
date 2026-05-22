FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev \
    libcairo2 libgdk-pixbuf2.0-0 libjpeg62-turbo libopenjp2-7 libtiff6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8000
