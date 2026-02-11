FROM python:3.13-slim
WORKDIR /app
COPY src/ src/
COPY tools/ tools/
CMD ["python3", "src/mcp_server.py"]
