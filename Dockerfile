# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DEBIAN_FRONTEND=noninteractive

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    tesseract-ocr \
    libtesseract-dev \
    poppler-utils \
    libmagic1 \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Create a non-root user
RUN adduser --disabled-password --gecos '' appuser

# Copy the current directory contents into the container
COPY --chown=appuser:appuser . .

# Copy the template folder into the container
COPY --chown=appuser:appuser template /app/template

# Copy and set permissions for Google credentials
COPY --chown=appuser:appuser google_credentials.json /app/google_credentials.json
RUN chmod 600 /app/google_credentials.json

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create necessary directories for logs
RUN mkdir -p /var/log/supervisor

# Switch to non-root user
USER appuser

# Make port available to the world outside this container
EXPOSE $PORT

# Run supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

