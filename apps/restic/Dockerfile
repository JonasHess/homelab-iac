FROM python:3.15-alpine

# Install system dependencies
RUN apk add --no-cache \
    jq \
    && rm -rf /var/cache/apk/*

# restic comes from its own releases, not Alpine, so its version is pinned here
# instead of moving with whatever the base image's package repository ships.
# renovate: datasource=github-releases depName=restic/restic extractVersion=^v(?<version>.+)$
ARG RESTIC_VERSION=0.19.1
ARG TARGETARCH=amd64
RUN cd /tmp \
    && asset="restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2" \
    && base="https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}" \
    && wget -q "${base}/${asset}" "${base}/SHA256SUMS" \
    && grep " ${asset}$" SHA256SUMS | sha256sum -c - \
    && bunzip2 "${asset}" \
    && install -m 0755 "${asset%.bz2}" /usr/local/bin/restic \
    && rm -f "${asset%.bz2}" SHA256SUMS \
    && restic version

# Install Python dependencies
COPY assets/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --retries 3 --timeout 30 -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy the restic operations script
COPY assets/restic-operations.py /usr/local/bin/restic-operations.py
RUN chmod +x /usr/local/bin/restic-operations.py

# Set the default working directory
WORKDIR /app

# Default command
CMD ["python", "/usr/local/bin/restic-operations.py"]