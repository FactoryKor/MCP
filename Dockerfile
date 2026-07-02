# 빌드 컨텍스트 = "Install File" 루트(pg/ aks/ adx/ eh/ mcp/ 포함).
# 예: az acr build -r $ACR -t diag-mcp:v1 -f mcp/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY pg/  ./pg/
COPY aks/ ./aks/
COPY adx/ ./adx/
COPY eh/  ./eh/
COPY mcp/ ./mcp/
RUN pip install --no-cache-dir mcp \
    -r pg/requirements.txt -r aks/requirements.txt \
    -r adx/requirements.txt -r eh/requirements.txt
EXPOSE 8000
CMD ["python", "mcp/mcp_server.py"]
