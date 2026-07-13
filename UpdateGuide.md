# diag-tools MCP · SRE Agent — 업데이트/배포 운영 가이드

> 대상: 이미 배포된 **diag-mcp**(Azure Container Apps 상주) + **Azure SRE Agent** 연동 환경에
> 새 기능/버그픽스를 반영하고, GitHub 경로 변경에 대응하기 위한 실무 런북.
> 진단기(`eh/adx/pg/aks_diagnose`)는 모두 **읽기 전용**이며, 이 가이드의 어떤 단계도 진단 대상 리소스를 변경하지 않습니다.

---

## 0. 먼저 이해할 것 — "소스가 있는 곳 ≠ 실행되는 곳"

```
[소스]  GitHub 리포(Install File/)
           │  git push(main) / tag v* / 수동 dispatch
           ▼
[배포]  GitHub Actions (.github/workflows/deploy-mcp.yml)
           │  az acr build  →  ACR: diag-mcp:<version> (+ :latest)
           ▼
        az containerapp update  →  Azure Container Apps 에 diag-mcp 상주
           ▲   (User-Assigned Managed Identity 로 ADX/PG/EH/AKS 읽기 전용 접근)
           │  MCP 호출(/mcp, streamable-http) — 매 요청
[런타임] Azure SRE Agent  ←  사용자/인시던트 트리거
```

핵심 원칙 3가지:

1. **런타임 루프에 GitHub가 없다.** SRE Agent → `/mcp`(ACA) → 진단 실행 → JSON. 코드를 배포할 때만 GitHub가 개입한다.
2. **엔드포인트(`https://<fqdn>/mcp`)는 코드 배포로 바뀌지 않는다.** 그래서 대부분의 업데이트에서 SRE Agent는 손댈 필요가 없다.
3. **이미지는 버전 고정(`diag-mcp:v1.2.0` 또는 `sha-<short>`)으로 배포**한다. 재현성·롤백을 위해 `latest`에만 의존하지 않는다.

---

## 1. 사전 준비 (최초 1회 확인)

### 1-1. 로컬 도구
실행 머신에 아래가 설치돼 있어야 한다.

| 도구 | 확인 명령 | 용도 |
|---|---|---|
| Git | `git --version` | 소스 푸시 |
| Azure CLI | `az version` | 수동 빌드/배포·검증 |
| (선택) Docker | `docker --version` | 로컬 이미지 테스트 |

> `az acr build`는 **클라우드(ACR Tasks)에서 빌드**하므로 로컬 Docker 없이도 이미지 빌드가 된다.

### 1-2. 배포에 필요한 값 (미리 메모)

| 이름 | 예시/설명 | 확인 방법 |
|---|---|---|
| `ACR_NAME` | 이미지 레지스트리 이름 | `az acr list -o table` |
| `ACA_NAME` | Container App 이름(`diagmcp-app`) | `az containerapp list -o table` |
| `ACA_RG` | ACA 리소스 그룹 | 위 명령 출력 |
| `MCP_ENDPOINT` | `https://<fqdn>/mcp` | Bicep 출력 `mcpEndpoint` 또는 아래 명령 |
| `UAMI_PRINCIPAL_ID` | 진단 자격(관리 ID) principalId | Bicep 출력 `identityPrincipalId` |

```powershell
# 엔드포인트(FQDN) 확인
$fqdn = az containerapp show -n <ACA_NAME> -g <ACA_RG> --query properties.configuration.ingress.fqdn -o tsv
"MCP endpoint: https://$fqdn/mcp"
```

### 1-3. GitHub 리포 시크릿 (CI/CD용, 최초 1회)
`Settings → Secrets and variables → Actions`:

| 시크릿 | 용도 |
|---|---|
| `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | OIDC 로그인용 Entra 앱(연합 자격) |
| `ACR_NAME` | 이미지 빌드/보관 ACR |
| `ACA_NAME` / `ACA_RESOURCE_GROUP` | 배포 대상 Container App |

---

## 2. 시나리오 A — 배포된 MCP 서버에 새 기능 반영

### A-1. (권장) CI/CD 자동 배포 — "커밋 후 푸시"

워크플로는 `pg/** aks/** adx/** eh/** mcp/**` 또는 `.github/workflows/deploy-mcp.yml` 변경, 그리고 `v*` 태그에 반응한다.

```powershell
# 리포 루트(= Install File)에서
git switch main
git pull                       # 최신화

# 변경 파일 스테이징 (예: eh 개선 + mcp 파라미터 추가)
git add eh/ adx/ pg/ mcp/
git commit -m "feat(eh): region/checkpoint_store 파라미터, 리포트 완결화(양호/미평가), 한글화"

git push origin main
```

푸시 직후 Actions가 자동 수행:

1. **버전 결정** — 태그 푸시면 `v1.2.0`, 아니면 `sha-<short>`.
2. **`az acr build`** — `diag-mcp:<version>` + `diag-mcp:latest` 를 ACR에 푸시(빌드 컨텍스트 = 리포 루트, Dockerfile = `mcp/Dockerfile`).
3. **`az containerapp update --image ...:<version>`** — ACA에 **새 리비전** 생성. 단일 리비전 모드라 트래픽이 새 리비전으로 롤링 전환.

Actions 실행 상황은 리포의 **Actions 탭**과 워크플로 요약(`### Deployed diag-mcp`)에서 확인.

### A-2. 릴리스 버전 고정 배포 (권장 운영 방식)

```powershell
git tag v1.2.0
git push origin v1.2.0        # → diag-mcp:v1.2.0 로 고정 배포 (롤백 지점 확보)
```

### A-3. 수동 배포 폴백 (Actions 없이 손으로)

CI가 막혔거나 긴급 배포일 때, 실행 머신에서 직접:

```powershell
cd "<리포 루트 = Install File>"
az login                      # 또는 관리 ID/서비스 주체
az account set --subscription <SUB_ID>

$VER = "v1.2.0"
# 1) 빌드 + 푸시 (버전 + latest)
az acr build --registry <ACR_NAME> `
  --image "diag-mcp:$VER" --image "diag-mcp:latest" `
  --file mcp/Dockerfile .

# 2) ACA 이미지 교체
$acrLogin = az acr show -n <ACR_NAME> --query loginServer -o tsv
az containerapp update -n <ACA_NAME> -g <ACA_RG> `
  --image "$acrLogin/diag-mcp:$VER"
```

### A-4. 배포 전 로컬 스모크 테스트 (선택, 권장)

이미지에 담기 전 진단기 JSON이 정상인지 확인:

```powershell
# 진단기 단독 (데모 데이터)
$env:PYTHONIOENCODING = "utf-8"
python eh\eh_diagnose.py --demo --format json  | python -m json.tool
python adx\adx_diagnose.py --demo --format json | python -m json.tool
python pg\pg_diagnose.py  --demo --format json | python -m json.tool

# MCP 서버 기동 → /mcp 살아있는지
cd mcp
pip install mcp -r ..\eh\requirements.txt -r ..\adx\requirements.txt
python mcp_server.py           # streamable-http, 포트 8000, 엔드포인트 /mcp
```

---

## 3. 시나리오 B — SRE Agent 업데이트

SRE Agent는 **엔드포인트만 가리키고, 도구 스키마는 MCP `tools/list`로 캐시**한다. 그래서 변경 종류에 따라 필요한 조치가 다르다.

| 변경 내용 | SRE Agent 조치 | 이유 |
|---|---|---|
| 도구 **내부 동작**만 변경 (버그픽스, 한글 라벨, 완결화, health_score 계산 등) | **없음** | 같은 도구를 같은 인자로 호출 → 새 동작이 자동 반영 |
| 도구 **스키마 변경** (파라미터 추가/삭제/이름변경, 새 도구, 설명 변경) | **커넥터 도구 목록 새로고침** (재동기화 또는 제거 후 재등록) | 에이전트가 캐시한 tool manifest를 갱신해야 새 인자를 인식 |
| **엔드포인트 URL 변경** (ACA 재생성, external↔internal 전환 등) | 커넥터의 MCP 엔드포인트 주소 수정 | 가리키는 대상 자체가 바뀜 |
| **새 리소스 유형**을 다루는 도구 추가 | UAMI에 read 역할 부여(`infra/assign-roles.ps1`) | 관리 ID가 새 대상에 접근 권한 필요 |

### B-1. 스키마가 바뀐 경우(이번 개선 해당) — 커넥터 재동기화

`diagnose_eventhub`에 `region`·`checkpoint_store` 파라미터가 **추가**됐으므로, SRE Agent가 새 인자를 쓰게 하려면 커넥터를 한 번 새로고침한다.

1. Azure Portal → **Azure SRE Agent** → 해당 에이전트 → **Tools / MCP 커넥터** 설정.
2. 등록된 diag-mcp 커넥터에서 **Refresh / Re-sync**(또는 커넥터 제거 후 `MCP_ENDPOINT`로 재등록).
3. 도구 목록에 `diagnose_eventhub`의 새 파라미터(`region`, `checkpoint_store`)가 보이는지 확인.

> 기존 인자만 쓰는 호출은 재동기화 없이도 계속 동작한다. 새 파라미터 활용을 원할 때만 재동기화가 필요.

### B-2. 엔드포인트 등록/변경

```powershell
# 등록에 쓸 엔드포인트 재확인
$fqdn = az containerapp show -n <ACA_NAME> -g <ACA_RG> --query properties.configuration.ingress.fqdn -o tsv
"등록/수정할 값: https://$fqdn/mcp"
```

SRE Agent 커넥터에 위 `https://<fqdn>/mcp` 를 등록/수정한다.

> **보안 주의**: `externalIngress=true`(PoC 기본)는 `/mcp`를 공개한다. 운영에서는 `externalIngress=false`(내부) + Private Endpoint/APIM 인증, 또는 최소한 IP 제한을 건다. 진단 출력은 `_clean`으로 secret/PII/prompt-injection을 필터링하지만, 엔드포인트 접근 제어는 별도로 확보한다.

### B-3. 새 도구가 새 리소스 유형을 다룰 때 — 권한 부여

```powershell
./infra/assign-roles.ps1 -PrincipalId <UAMI_PRINCIPAL_ID> `
  -MonitoringScope "/subscriptions/<sub>/resourceGroups/<rg>" `
  -EventHubNamespaceId "<eh-namespace-resource-id>"
# ADX/PG/AKS 데이터 평면 권한은 스크립트 실행 후 출력되는 안내대로 각 서비스 내부에서 부여
```

---

## 4. 시나리오 C — GitHub 경로가 바뀔 때

### C-1. 리포지토리 자체가 이동/이름 변경 (`OrgA/repoA` → `OrgB/repoB`)

**런타임은 영향 없음** — SRE Agent→`/mcp`→ACA 경로에 GitHub가 없으므로 진단은 계속 동작한다. **CI/CD만** 고친다.

1. **OIDC 연합 자격 subject 갱신 (가장 중요)**
   Entra 앱의 federated credential subject를 새 리포로 바꾼다. 안 고치면 Actions의 `azure/login`이 실패한다.
   - main 브랜치: `repo:OrgB/repoB:ref:refs/heads/main`
   - 태그 배포도 쓰면: `repo:OrgB/repoB:ref:refs/tags/*` (또는 태그별 subject)
   - environment 보호를 쓰면: `repo:OrgB/repoB:environment:production`
   ```powershell
   # 기존 자격 확인
   az ad app federated-credential list --id <AZURE_CLIENT_ID/appId> -o table
   # 새 subject 추가 (예: main)
   az ad app federated-credential create --id <appId> --parameters '{
     \"name\": \"gh-OrgB-repoB-main\",
     \"issuer\": \"https://token.actions.githubusercontent.com\",
     \"subject\": \"repo:OrgB/repoB:ref:refs/heads/main\",
     \"audiences\": [\"api://AzureADTokenExchange\"]
   }'
   ```
2. **새 리포에 시크릿 재설정** — 1-3의 시크릿 6종을 새 리포 Settings에 다시 등록.
3. **워크플로 파일은 리포와 함께 이동**하므로 1·2만 하면 그대로 동작.
4. **실행 머신 remote 갱신**
   ```powershell
   git remote set-url origin https://github.com/OrgB/repoB.git
   git remote -v
   ```

### C-2. 리포 **내부 폴더 경로**가 바뀔 때 (루트 이동 / 도구 폴더명 변경)

경로를 하드코딩한 **세 곳**만 맞춘다.

1. **`.github/workflows/deploy-mcp.yml`** — 트리거 `paths:` 필터와 빌드 컨텍스트.
   ```yaml
   on:
     push:
       paths:
         - "pg/**"        # ← 폴더명이 바뀌면 여기 갱신
         - "adx/**"
         - "eh/**"
         - "mcp/**"
   # ...
   # az acr build ... --file mcp/Dockerfile .   ← 루트가 바뀌면 컨텍스트('.')/경로 갱신
   ```
2. **`mcp/Dockerfile`** — COPY 라인과 빌드 컨텍스트.
   ```dockerfile
   COPY pg/  ./pg/          # ← 폴더명 변경 시 함께 수정
   COPY adx/ ./adx/
   COPY eh/  ./eh/
   COPY mcp/ ./mcp/
   ```
3. **`mcp/mcp_server.py`** — `BASE = dirname(dirname(__file__))`라 **트리 전체를 옮겨도 안전**(cwd 무관). 단 **개별 도구 폴더명을 바꾸면** 아래 상수를 수정.
   ```python
   PG_TOOL  = os.path.join(BASE, "pg",  "pg_diagnose.py")   # ← 폴더/파일명 변경 시
   ADX_TOOL = os.path.join(BASE, "adx", "adx_diagnose.py")
   EH_TOOL  = os.path.join(BASE, "eh",  "eh_diagnose.py")
   ```

> **불변식**: `mcp/`, `pg/`, `adx/`, `eh/`, `aks/`가 **같은 부모(BASE) 아래 형제**로 유지되는 한, 트리 위치가 바뀌어도 서버는 정상 동작한다.

### C-3. 진단기 **소스 리포**가 별도이고 그 경로가 바뀔 때

진단기(`FactoryKor/eh` 등)는 빌드 전에 `Install File`로 **복사(벤더링)**되어 이미지에 담긴다. 따라서 소스 리포 경로 변경은 **빌드된 이미지에 영향 없음**(Dockerfile은 로컬 폴더만 COPY). 벤더링 스크립트/서브모듈을 쓴다면 그 참조 URL만 갱신한다.

---

## 5. 검증 & 롤백

### 5-1. 배포 검증

```powershell
# 새 리비전이 Active/Running 인지
az containerapp revision list -n <ACA_NAME> -g <ACA_RG> -o table

# 현재 서비스 중인 이미지 태그 확인 (버전 고정 확인)
az containerapp show -n <ACA_NAME> -g <ACA_RG> `
  --query "properties.template.containers[0].image" -o tsv

# 컨테이너 로그 (기동/에러)
az containerapp logs show -n <ACA_NAME> -g <ACA_RG> --tail 50
```

**기능 검증(종단)**: SRE Agent 채팅에서
> "Diagnose Event Hub `<namespace resource id>` in region `koreacentral`."

응답 JSON에 `summary`, `health_score`, `severity_counts`, `recommended_actions`, 그리고 consumer lag이 잡히면 `checkpoint` 관련 finding이 보이는지 확인.

### 5-2. 롤백 (이전 버전으로 즉시 복귀)

```powershell
# 이전에 배포했던 버전 태그로 다시 update
$acrLogin = az acr show -n <ACR_NAME> --query loginServer -o tsv
az containerapp update -n <ACA_NAME> -g <ACA_RG> `
  --image "$acrLogin/diag-mcp:v1.1.0"     # ← 직전 안정 버전

# 또는 리비전 기반 롤백 (해당 리비전으로 트래픽 100%)
az containerapp revision list -n <ACA_NAME> -g <ACA_RG> -o table
az containerapp ingress traffic set -n <ACA_NAME> -g <ACA_RG> `
  --revision-weight <이전리비전이름>=100
```

> **버전 고정 배포**를 지켜야 롤백이 쉽다. `latest`만 쓰면 "이전 것"을 특정하기 어렵다.

---

## 6. 트러블슈팅

| 증상 | 원인 후보 | 해결 |
|---|---|---|
| Actions `azure/login` 실패 | OIDC subject 불일치(리포 이동/브랜치·태그) | C-1의 federated credential subject 갱신 |
| `az acr build` 권한 오류 | 로그인 주체에 ACR push 권한 없음 | `AcrPush`/기여자 역할 확인 |
| ACA가 이미지 pull 실패 | UAMI에 `AcrPull` 없음 | `infra/main.bicep`이 부여(재배포) 또는 수동 역할 부여 |
| SRE Agent가 새 파라미터 못 씀 | 도구 manifest 캐시 | B-1 커넥터 재동기화 |
| `diagnose_eventhub`가 `error` 반환 | 진단기 실패(권한/네트워크) | 반환 JSON의 `stderr`, ACA 로그 확인. EH Data Receiver / Storage Blob Data Reader 역할 점검 |
| consumer lag이 `미평가` | 체크포인트 스토리지 접근 불가/미탐색 | UAMI에 `Storage Blob Data Reader` 부여, 또는 `checkpoint_store` 명시 |
| 한글 깨짐(로컬 테스트) | 콘솔 인코딩 | `$env:PYTHONIOENCODING="utf-8"` (HTML/JSON 산출물은 UTF-8이라 무관) |

---

## 7. 빠른 체크리스트

**신규 기능 배포 (평상시)**
- [ ] 로컬 스모크(`--demo --format json`) 통과
- [ ] `git commit` + `git push origin main` (또는 `git tag v* && git push --tags`)
- [ ] Actions 성공 확인 (Actions 탭)
- [ ] `az containerapp revision list` 새 리비전 Active
- [ ] 스키마 변경 시 → SRE Agent 커넥터 재동기화
- [ ] SRE Agent 종단 테스트("Diagnose ...")

**GitHub 리포 이동**
- [ ] OIDC federated credential subject 갱신
- [ ] 새 리포에 시크릿 6종 재등록
- [ ] `git remote set-url origin <새 URL>`
- [ ] 푸시 → Actions 성공 확인 (런타임/ SRE Agent는 무영향)

**리포 내부 경로 변경**
- [ ] `deploy-mcp.yml` `paths:` / 빌드 컨텍스트
- [ ] `mcp/Dockerfile` COPY 라인
- [ ] (도구 폴더명 변경 시) `mcp_server.py` `*_TOOL` 상수
- [ ] `mcp/`·`pg/`·`adx/`·`eh/`·`aks/` 형제 구조 유지 확인

---

*최종 업데이트: 2026-07-13 · 관련 파일: `.github/workflows/deploy-mcp.yml`, `mcp/Dockerfile`, `mcp/mcp_server.py`, `infra/main.bicep`, `infra/assign-roles.ps1`*
