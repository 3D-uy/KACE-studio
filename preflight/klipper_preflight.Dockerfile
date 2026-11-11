FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39
ARG KLIPPER_REF
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ git libc6-dev libffi-dev && rm -rf /var/lib/apt/lists/*
RUN test "$KLIPPER_REF" = fe4eb8650bd7de4c2100a14eaf09b0965c430e29 \
    && git init /opt/klipper \
    && git -C /opt/klipper remote add origin https://github.com/Klipper3d/klipper.git \
    && git -C /opt/klipper fetch --depth 1 origin "$KLIPPER_REF" \
    && git -C /opt/klipper checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/klipper rev-parse HEAD)" = "$KLIPPER_REF" \
    && git -C /opt/klipper diff --exit-code \
    && test -z "$(git -C /opt/klipper ls-files --others --exclude-standard)"
RUN python -m pip install --no-cache-dir -r /opt/klipper/scripts/klippy-requirements.txt
COPY core/klipper_preflight_worker.py /usr/local/bin/kace-klipper-validator
