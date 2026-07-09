import os, sys, json, subprocess, re
from mcp.server.fastmcp import FastMCP

# 스크립트 위치 기준으로 도구 경로 고정 → 실행 위치(cwd)와 무관하게 동작
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # diag-platform 루트
PG_TOOL  = os.path.join(BASE, "pg",  "pg_diagnose.py")
AKS_TOOL = os.path.join(BASE, "aks", "aks_diagnose.py")
ADX_TOOL = os.path.join(BASE, "adx", "adx_diagnose.py")
EH_TOOL  = os.path.join(BASE, "eh",  "eh_diagnose.py")

mcp = FastMCP("diag-tools", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
RID = re.compile(r"^/subscriptions/[0-9a-fA-F-]+/.+$")
NS  = re.compile(r"^[a-z0-9-]{1,63}$")
CLUSTER = re.compile(r"^https://[A-Za-z0-9.-]+\.kusto\.[A-Za-z0-9.]+/?$", re.IGNORECASE)

@mcp.tool()
def diagnose_postgres(host: str, dbname: str = "postgres", resource_id: str = "", hours: int = 24) -> dict:
    """Azure Database for PostgreSQL Flexible Server 진단(읽기 전용, 결과 JSON)."""
    if resource_id and not RID.match(resource_id):
        raise ValueError("invalid resource_id")
    cmd = [sys.executable, PG_TOOL, "--host", host, "--dbname", dbname,
           "--aad", "--format", "json", "--hours", str(hours)]   # --aad=Entra 토큰
    if resource_id:
        cmd += ["--resource-id", resource_id]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
    return json.loads(out.stdout)

@mcp.tool()
def diagnose_aks(namespace: str = "default", context: str = "", all_namespaces: bool = False,
                 prometheus_url: str = "", appinsights_id: str = "") -> dict:
    """AKS 클러스터 진단(읽기 전용, 외부 kubeconfig/context)."""
    if not NS.match(namespace):
        raise ValueError("invalid namespace")
    cmd = [sys.executable, AKS_TOOL, "--namespace", namespace, "--format", "json"]
    if context:        cmd += ["--context", context]
    if all_namespaces: cmd += ["--all-namespaces"]
    if prometheus_url: cmd += ["--prometheus-url", prometheus_url, "--prometheus-aad"]
    if appinsights_id: cmd += ["--appinsights-id", appinsights_id]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
    return json.loads(out.stdout)

@mcp.tool()
def diagnose_adx(cluster: str, database: str = "", resource_id: str = "",
                 region: str = "", hours: int = 24) -> dict:
    """Azure Data Explorer(ADX/Kusto) 진단(읽기 전용, 결과 JSON).
    cluster: https://<name>.<region>.kusto.windows.net
    database 지정 시 .show queries/캐시/extents 분석, resource_id+region 지정 시 Azure Monitor 메트릭."""
    if not CLUSTER.match(cluster or ""):
        raise ValueError("invalid cluster URI")
    if resource_id and not RID.match(resource_id):
        raise ValueError("invalid resource_id")
    cmd = [sys.executable, ADX_TOOL, "--cluster", cluster,
           "--auth", "default", "--format", "json", "--hours", str(hours)]
    if database:    cmd += ["--database", database]
    if resource_id: cmd += ["--resource-id", resource_id]
    if region:      cmd += ["--region", region]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
    return json.loads(out.stdout)

@mcp.tool()
def diagnose_eventhub(resource_id: str, event_hub: str = "", region: str = "",
                      window_minutes: int = 60) -> dict:
    """Azure Event Hubs 진단(읽기 전용, 결과 JSON).
    resource_id: namespace ARM 리소스 ID. event_hub 미지정 시 모든 event hub를 개별 진단.
    region 미지정 시 ARM location에서 자동 유도. 결과에는 summary/health_score/recommended_actions 포함."""
    if not RID.match(resource_id or ""):
        raise ValueError("invalid resource_id")
    cmd = [sys.executable, EH_TOOL, "--resource-id", resource_id, "--azure-auth",
           "--eh-auth", "entra", "--format", "json", "--window-minutes", str(window_minutes)]
    if event_hub:
        cmd += ["--event-hub", event_hub]
    if region:
        cmd += ["--region", region]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
    return json.loads(out.stdout)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")   # 엔드포인트: /mcp
