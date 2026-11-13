FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgl1 libglib2.0-0 libgomp1 libvulkan1 mesa-vulkan-drivers && \
    rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /srv/mineru
COPY requirements.txt requirements.lock ./
RUN uv --no-config pip install --system -r requirements.txt -c requirements.lock
COPY config.yaml content_list_api.py ./
ENV PYTHONUNBUFFERED=1 MINERU_HOME=/var/lib/mineru MINERU_CONFIG=/srv/mineru/config.yaml
ENTRYPOINT ["mineru-kit"]
