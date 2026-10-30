# musings: mortal FastAPI service for file injection and command execution.
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

# Expose the port the app runs on
EXPOSE 8000

# Define environment variables
ENV PYTHONUNBUFFERED=1
ENV MUSINGS_BASE_DIR=/data

# The token must be supplied at run time: -e MUSINGS_TOKEN=<token>
# A container is reached from outside by definition, so the service
# listens on every interface. The port is 8000 unless MUSINGS_URL says
# otherwise. Listening on every interface over plain http is refused:
# either mount a PKI and set an https MUSINGS_URL with MUSINGS_TLS_CERT
# and MUSINGS_TLS_KEY (see HOWTORUN.adoc), or override the command with
# `musings-server --public --insecure` behind a TLS-terminating proxy.
CMD ["musings-server", "--public"]
