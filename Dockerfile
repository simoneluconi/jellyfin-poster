FROM python:3.15-slim

# Print logs immediately (no output buffering)
ENV PYTHONUNBUFFERED=1

# Set working directory inside the container
WORKDIR /app

# Install Python dependencies first, so code changes don't invalidate this layer
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project into the container (secrets and local files excluded via .dockerignore)
COPY . /app

# Start the application
# All settings (JELLYFIN_URL, JELLYFIN_API_KEY, PORT, SHOW_TITLE, ...) are read from environment variables
CMD ["python", "app.py"]
