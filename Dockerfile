FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
EXPOSE 8080
COPY pyproject.toml README.md ./
COPY bamboook ./bamboook
RUN pip install --no-cache-dir .

CMD ["python", "-m", "bamboook"]
