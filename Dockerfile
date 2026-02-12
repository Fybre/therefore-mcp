FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
COPY tools/ tools/
# Use ENTRYPOINT so arguments pass directly to the script
ENTRYPOINT ["python3", "src/mcp_server.py"]
# Default: stdio only. Use --http PORT for HTTP, or --stdio --http PORT for both.
CMD ["--stdio"]
