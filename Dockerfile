# musings: mortal FastAPI service for agent communication.
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
# Run the FastAPI app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
