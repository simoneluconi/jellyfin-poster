FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Copy the entire project into the container
# This replaces the need for git clone
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Start the application
# HOST, PORT, DEBUG, JELLYFIN_URL, JELLYFIN_API_KEY are read from environment variables
CMD ["python", "app.py"]
