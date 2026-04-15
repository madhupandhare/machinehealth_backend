FROM python:3.10-slim

WORKDIR /app

# Copy files
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 5000

# Run with Gunicorn (production server)
CMD ["gunicorn", "-b", "0.0.0.0:5000", "run:app"]