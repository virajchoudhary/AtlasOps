FROM --platform=linux/amd64 python:3.12.11-slim-bookworm@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace/AtlasOps

COPY requirements/sft-pilot-linux-py312.lock /tmp/sft-pilot-linux-py312.lock

RUN python -m pip --isolated install \
        --disable-pip-version-check \
        --no-cache-dir \
        --only-binary=:all: \
        --require-hashes \
        --index-url=https://pypi.org/simple \
        -r /tmp/sft-pilot-linux-py312.lock \
    && python -m pip --isolated check

CMD ["python", "-c", "pass"]
