# Use a slim Python base image
FROM python:3.11-slim

# Set environment variables to prevent Python from writing .pyc files and to run in unbuffered mode
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install all system dependencies in a single layer for better Docker image caching.
# - postgresql-client: for psql and pg_isready commands in the entrypoint.
# - openssh-client: for the Pinggy.io ssh tunnel command.
# - wget: required for the HEALTHCHECK command.
# - build-essential tools (gcc) and dev libraries (libgmp-dev, libpq-dev): for compiling Python packages.
RUN apt-get update -y && apt-get upgrade -y && apt-get install -y wget libgmp-dev libpq-dev gcc openssh-client postgresql-client && rm -rf /var/lib/apt/lists/*


# Set the working directory inside the container
WORKDIR /app

# Copy the application code and necessary scripts into the container
COPY ./denaro ./denaro
COPY ./run_node.py .
COPY ./requirements.txt .

# Docker-layer artifacts from docker/ subdir (relative to context root)
# Place both scripts under /app/docker inside the image.
COPY ./docker/docker-entrypoint.sh ./docker/docker-entrypoint.sh
COPY ./docker/docker-healthcheck.py ./docker/docker-healthcheck.py

# Make the entrypoint and healcheck executable
RUN chmod +x ./docker/docker-entrypoint.sh ./docker/docker-healthcheck.py

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r ./requirements.txt


# Execute healthcheck to see if the API is responsive.
# This is crucial for the depends_on: condition: service_healthy in docker-compose.
# It waits 30s before starting, tries 3 times, with a 3s timeout per try.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD ./docker/docker-healthcheck.py

# Set the entrypoint to our setup script, which will run when the container starts
ENTRYPOINT ["./docker/docker-entrypoint.sh"]