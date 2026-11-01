FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git libraqm0 libfribidi0 libharfbuzz0b \
      tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
# REVISION changes whenever the stream code changes, so a rebuild fetches it.
ARG REVISION=main
RUN git clone https://github.com/ashivam-dot/live-desk /app && git -C /app checkout "$REVISION" \
    && pip install --no-cache-dir -r /app/requirements.txt
RUN useradd -m -u 1000 user && mkdir -p /tmp/live-desk && chown -R user /app /tmp/live-desk
USER user
WORKDIR /app
ENV PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python", "space/run.py"]
