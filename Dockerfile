# Multi-stage build for iClassPro Enrollment Dashboard
FROM python:3.11-slim as builder

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libxcb1 \
    libx11-6 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install

# Copy application code
COPY . .

# Final stage
FROM python:3.11-slim

# Install runtime system dependencies for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libexpat1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libxcb1 \
    libx11-6 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy from builder
COPY --from=builder /app /app
COPY --from=builder /root/.cache /root/.cache

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Copy entrypoint script
COPY --chown=appuser:appuser docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Expose port (Cloud Run uses PORT env var, defaulting to 8080)
EXPOSE 8000

# Run the application via entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]
