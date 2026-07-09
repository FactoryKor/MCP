**한국어** | [English](README.en.md)

# diag-tools MCP Server

Azure 진단 도구 제품군(`pg_diagnose` / `aks_diagnose` / `adx_diagnose` / `eh_diagnose`)을
**MCP(Model Context Protocol) 도구**로 노출하는 서버입니다. Azure SRE Agent 또는 임의의
MCP 클라이언트가 각 진단기를 호출해 **읽기 전용(read-only)** 진단 결과를 JSON으로 받습니다.

> [!NOTE]
> 모든 진단기는 읽기 전용입니다. 리소스를 변경하지 않으며, 결과 JSON은 secret/PII/prompt-injection
> 필터링(`_clean`)을 거쳐 반환됩니다.

---

## 폴더 구조

MCP 서버는 **스크립트 위치(`__file__`) 기준**으로 각 도구 경로를 고정합니다
(실행 위치 `cwd`와 무관). 각 도구는 자신만의 폴더 + `requirements.txt`를 가집니다.

```
Install File/            # = BASE (mcp_server.py 기준 상위 폴더)
├── mcp/
│   ├── mcp_server.py    # MCP 서버 (도구 등록)
│   └── README.md        # (이 문서)
├── pg/    pg_diagnose.py
├── aks/   aks_diagnose.py
├── adx/   adx_diagnose.py  + requirements.txt
└── eh/    eh_diagnose.py   + requirements.txt
```

`BASE = dirname(dirname(mcp_server.py))` 이며, 각 도구는 `BASE/<name>/<name>_diagnose.py`
규칙으로 참조됩니다.

> [!IMPORTANT]
> 통합 `requirements.txt`는 두지 않습니다. 도구별 의존성이 독립적이라 충돌·과설치를 피하기
> 위해 **도구마다 자체 `requirements.txt`**를 유지합니다.

---

## 등록된 도구

| MCP 도구 | 대상 | 주요 인자 | 내부 호출 |
|---|---|---|---|
| `diagnose_postgres` | PostgreSQL Flexible Server | `host`, `dbname`, `resource_id`, `hours` | `pg_diagnose.py --aad --format json` |
| `diagnose_aks` | AKS 클러스터 | `namespace`, `context`, `all_namespaces`, `prometheus_url`, `appinsights_id` | `aks_diagnose.py --format json` |
| `diagnose_adx` | Azure Data Explorer(Kusto) | `cluster`, `database`, `resource_id`, `region`, `hours` | `adx_diagnose.py --auth default --format json` |
| `diagnose_eventhub` | Azure Event Hubs | `resource_id`, `event_hub`, `region`, `window_minutes` | `eh_diagnose.py --azure-auth --eh-auth entra --format json` |

각 도구는 입력을 검증(`RID`/`NS`/`CLUSTER` 정규식)한 뒤 `subprocess.run([... , "--format", "json"])`으로
진단기를 실행하고 `json.loads(stdout)`을 반환합니다. 인자는 셸 문자열이 아닌 **argv 리스트**로 전달합니다.

### 출력 스키마 (도구별 Finding 필드 차이)
- 공통 최상위: `tool` / `target` / `health_score` / `findings[]`
- 심각도 값: **critical / warning / info / ok** (※ high/medium/low 아님)
- `pg` · `adx`: `findings[]` = severity / category / title / detail / recommendation
- `aks`: severity / component / title / detail / recommendation / **steps(리스트)**
- `eh`: 자체 스키마(`checks[]` = category / severity / title / detail / **recommendation** / evidence) + 최상위 `worst_severity` / **`health_score`** / **`severity_counts`** / **`summary`**(자연어 한 줄) / **`recommended_actions[]`**(우선순위 조치: severity / category / title / action)

---

## 실행

```powershell
# 1) MCP 실행 Python 환경에 각 진단기 의존성 설치 (도구별 requirements)
pip install -r "..\adx\requirements.txt"
pip install -r "..\eh\requirements.txt"
# (pg/aks 의존성도 동일 환경에 설치)

# 2) 서버 기동 (streamable-http, 기본 포트 8000 → 엔드포인트 /mcp)
python mcp_server.py
```

- 포트는 환경변수 `PORT`로 변경 가능(기본 `8000`).
- 진단기는 `sys.executable`로 실행되므로, **MCP를 구동하는 동일 Python 환경**에
  Azure 자격(Managed Identity / `az login` / 환경변수 등)이 준비돼 있어야 합니다.
- 권한(RBAC)은 각 진단기 README 참고 (예: ADX 메트릭 = 클러스터 `Monitoring Reader`,
  Event Hubs 데이터 평면 = `Azure Event Hubs Data Receiver`).

### 컨테이너 빌드 (선택)
```
az acr build -r $ACR -t diag-mcp:v1 -f mcp/Dockerfile .   # BASE(Install File) 루트에서
```

---

## 배포 (CI/CD → ACR → ACA)

> [!IMPORTANT]
> **소스가 있는 곳(GitHub) ≠ 실행되는 곳(상주 MCP 서버)**.
> GitHub는 **배포 시점에만** 개입한다: 이미지 빌드 → ACR 푸시(**버전 고정**) → ACA 재배포.
> 런타임 요청 루프(SRE Agent → `/mcp` 호출 → 진단 실행 → JSON)에 GitHub는 **없다**.
> 매 요청마다 clone/`pip install` 하지 않는다(지연·공급망·재현성·인증 문제 회피).

```
GitHub 리포(Install File/)
   │  git push (main) / tag v1.2.0
   ▼
GitHub Actions (.github/workflows/deploy-mcp.yml)
   │  az acr build  →  ACR: diag-mcp:<version> (버전 고정) + :latest
   ▼
Azure Container Apps 에 diag-mcp 상주  ← 진단 코드가 이미지에 이미 설치됨
   ▲   (User-Assigned Managed Identity 로 ADX/PG/AKS/EH 읽기 전용 접근)
   │  MCP 호출 (/mcp, streamable-http) — 매 요청
SRE Agent  ←  사용자/인시던트 트리거
```

### 1. 인프라 프로비저닝 (Bicep)
`infra/main.bicep` — Log Analytics + Container Apps 환경 + **User-Assigned Managed Identity** + Container App(포트 8000, `/mcp`) + ACR Pull role.

```powershell
az deployment group create -g <rg> `
  -f infra/main.bicep -p infra/main.bicepparam `
  -p acrName=<ACR 이름>
# 출력: mcpEndpoint / identityPrincipalId / identityClientId
```

### 2. 최소 권한 부여 (읽기 전용)
진단은 전부 read-only 이므로 MI 권한도 조회/메트릭 read 로 최소화한다.
`infra` 출력의 `identityPrincipalId` 를 사용:

```powershell
./infra/assign-roles.ps1 -PrincipalId <identityPrincipalId> `
  -MonitoringScope "/subscriptions/<sub>/resourceGroups/<rg>" `
  -EventHubNamespaceId "<eh-namespace-resource-id>" `
  -AksClusterId "<aks-resource-id>"
```

| 대상 | Azure RBAC(스크립트가 부여) | 데이터 평면(별도 부여) |
|---|---|---|
| 공통 | `Reader`, `Monitoring Reader` | — |
| PostgreSQL | `Reader`/`Monitoring Reader` | MI를 Entra 사용자로 등록 후 `GRANT pg_monitor` |
| ADX | `Reader`/`Monitoring Reader` | 데이터베이스 `Viewer`(또는 AllDatabasesViewer) |
| Event Hubs | `Azure Event Hubs Data Receiver` | — |
| AKS | `AKS Cluster User Role` | K8s RBAC `view` ClusterRole 바인딩 |

> [!NOTE]
> 데이터 평면 권한은 Azure RBAC가 아니라 각 서비스 내부에서 부여한다(스크립트 실행 후 안내 출력 참고).

### 3. CI/CD (GitHub Actions)
`.github/workflows/deploy-mcp.yml` — `main` 푸시/`v*` 태그 시 실행. **OIDC 로그인**(시크릿 없는 자격),
`az acr build`(버전 고정 태그 = 릴리스 태그 또는 `sha-<short>`), `az containerapp update`.

리포 시크릿(Settings → Secrets → Actions):

| 시크릿 | 용도 |
|---|---|
| `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | Azure OIDC 로그인용 앱(연합 자격) |
| `ACR_NAME` | 이미지 빌드/보관 ACR |
| `ACA_NAME` / `ACA_RESOURCE_GROUP` | 배포 대상 Container App |

> 배포 승인 게이트가 필요하면 워크플로의 `environment: production` 에 보호 규칙을 건다.

### 4. SRE Agent 커넥터 등록
`infra` 출력 `mcpEndpoint`(`https://<fqdn>/mcp`)를 SRE Agent의 MCP 커넥터로 등록한다.

> [!WARNING]
> `externalIngress=true`(PoC 기본)는 MCP 엔드포인트를 공개한다. 운영에서는 `externalIngress=false`(내부)로
> 두고 **APIM/Private Endpoint 뒤에 인증**을 두거나, 최소한 IP 제한을 건다. 진단 출력은 `_clean`으로
> secret/PII/prompt-injection을 필터링하지만, 엔드포인트 자체 접근 제어는 별도로 확보해야 한다.

---

## 새 진단 도구 추가하기

진단기는 계속 추가됩니다. 새 도구(`<name>_diagnose.py`)를 붙일 때 아래 4단계를 따르세요.

### 1. 폴더 + 파일 배치
```
Install File/<name>/
├── <name>_diagnose.py
└── requirements.txt
```

### 2. 진단기에 JSON 출력 지원 (필수)
`stdout`에 **순수 JSON**만 출력합니다(로그·참고 메시지는 `stderr`로). pg/adx/eh와 동일하게
secret/PII/prompt-injection sanitization을 거칩니다.

각 진단기 소스에 대략 5곳을 추가하는 패턴입니다:

| # | 위치(앵커) | 무엇을 |
|---|-----------|--------|
| 0 | `from dataclasses import dataclass, field` | 필요 시 끝에 `, asdict` 추가 |
| 1 | `class Config:` 의 `demo: bool = False` 다음 | `format: str = "html"` 필드 추가 |
| 2 | `def parse_args(...)` 바로 위 | `emit_json()` + `_clean()` 함수 추가 |
| 3 | argparse 의 `--demo` 다음 | `--format {html,json}` (또는 `{table,json}`) 인자 추가 |
| 4 | `return Config(... demo=a.demo)` | 끝에 `, format=a.format` 추가 |
| 5 | `main()` 의 `score = health_score(...)` 다음 (스냅샷/HTML 앞) | `if cfg.format=="json": emit_json(...); return 0` |

```python
import re, sys, json
from dataclasses import asdict

_SECRET = re.compile(r'(?i)(password|pwd|secret|connection ?string|accountkey|sas|token|apikey)\s*[=:]\s*\S+')
_RRN    = re.compile(r'\b\d{6}-\d{7}\b')
_EMAIL  = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')
_INJECT = re.compile(r'(?i)(ignore (all|previous)|system prompt|<\s*important\s*>|assistant\s*:|tool_call)')

def _clean(v):
    if isinstance(v, str):
        v = _SECRET.sub(r'\1=***', v); v = _RRN.sub('[PII]', v)
        v = _EMAIL.sub('[PII]', v);    v = _INJECT.sub('[filtered]', v)
        return v
    if isinstance(v, dict):  return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):  return [_clean(x) for x in v]   # aks steps 등 리스트도 재귀 마스킹
    return v

def emit_json(findings, score, target, tool="<name>_diagnose"):
    payload = {"tool": tool, "target": target, "health_score": score,
               "findings": [{k: _clean(val) for k, val in asdict(f).items()} for f in findings]}
    json.dump(payload, sys.stdout, ensure_ascii=False)
```

### 3. `mcp_server.py`에 두 부분 추가
경로 상수 1줄 + `@mcp.tool()` 얇은 wrapper 1개:

```python
# 경로 (BASE/<name>/<name>_diagnose.py)
XXX_TOOL = os.path.join(BASE, "<name>", "<name>_diagnose.py")

@mcp.tool()
def diagnose_<name>(resource_id: str = "", ...) -> dict:
    """<대상> 진단(읽기 전용, 결과 JSON)."""
    if resource_id and not RID.match(resource_id):     # 입력 검증
        raise ValueError("invalid resource_id")
    cmd = [sys.executable, XXX_TOOL, "--format", "json", ...]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
    return json.loads(out.stdout)
```

- 서버 상단 검증 정규식: `RID`(resource id), `NS`(namespace), `CLUSTER`(ADX cluster URI).
  새 유형이면 패턴을 추가하세요.
- `timeout`은 대상 규모에 맞게 조정(기본 180~240초).

### 4. 의존성 설치 + 검증
```powershell
pip install -r "..\<name>\requirements.txt"
python ..\<name>\<name>_diagnose.py --demo --format json | python -m json.tool
# → tool/target/health_score/findings 키 확인
```

---

## 보안 원칙
- **읽기 전용**: 조회/`.show`/ARM read/Azure Monitor read 만 호출. 리소스를 변경하지 않음.
- **출력 sanitization**: 모든 JSON 출력은 `_clean`으로 secret/PII/prompt-injection을 필터링.
- **입력 검증**: MCP wrapper에서 resource_id/namespace/cluster를 정규식으로 검증한 뒤 실행.
- **인자 주입 방지**: 사용자 입력을 셸 문자열이 아닌 **argv 리스트**로 전달(`subprocess.run([...])`).

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `json.loads` 실패 | 진단기가 stdout에 JSON 외 텍스트를 섞음 → 로그는 `stderr`로 보내야 함 |
| `FileNotFoundError` (도구 경로) | `BASE/<name>/<name>_diagnose.py` 위치·철자 확인 |
| `ModuleNotFoundError` | MCP 실행 Python 환경에 해당 도구 `requirements.txt` 미설치 |
| 인증 실패 | MCP 구동 환경의 Azure 자격(MI/`az login`/env) 및 RBAC 확인 |
| `subprocess ... timeout` | 대상 규모가 크면 wrapper의 `timeout` 상향 |
