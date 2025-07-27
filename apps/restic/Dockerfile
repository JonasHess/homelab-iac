FROM python:3.11-alpine

# Install system dependencies
RUN apk add --no-cache \
    restic \
    jq \
    && rm -rf /var/cache/apk/*

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