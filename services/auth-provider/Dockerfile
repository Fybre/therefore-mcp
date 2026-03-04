FROM python:3.13-slim

WORKDIR /app

# Install dependencies directly to keep image slim
RUN pip install --no-cache-dir fastapi uvicorn PyJWT pydantic

# Copy only the auth provider service code
COPY auth.py main.py ./

# Create a placeholder for tenants.json if not provided
RUN touch tenants.json

EXPOSE 8001

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
