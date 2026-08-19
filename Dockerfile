FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data/raw /app/config

# Run bot
CMD ["python", "main.py"]
