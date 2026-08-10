# The conferencing service, as a container so Prosody's event_sync can reach it
# at http://conferencing:8080 on the shared docker network.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY conferencing/ ./conferencing/

# Matches the service default and the api_prefix in event_sync.cfg.lua.
EXPOSE 8080

# All configuration comes from the environment (see conferencing/config.py); the
# process refuses to start if a required secret is missing.
CMD ["python", "-m", "conferencing"]
