[한국어](README.md) | **English**

# diag-tools MCP Server

A server that exposes the Azure diagnostic tool suite (`pg_diagnose` / `aks_diagnose` / `adx_diagnose` / `eh_diagnose`) as **MCP (Model Context Protocol) tools**. Azure SRE Agent or any MCP client can call each diagnostic tool and receive **read-only** diagnostic results as JSON.

> [!NOTE]
> All diagnostics are read-only. They never modify resources, and result JSON is returned after secret/PII/prompt-injection filtering (`_clean`).

---

## Folder Structure

The MCP server anchors each tool's path to the **script location (`__file__`)** (independent of the working directory `cwd`). Each tool has its own folder + `requirements.txt`.

```
Install File/            # = BASE (parent folder of mcp_server.py)
├── mcp/
│   ├── mcp_server.py    # MCP server (tool registration)
│   └── README.md        # (this document)
├── pg/    pg_diagnose.py
├── aks/   aks_diagnose.py
├── adx/   adx_diagnose.py  + requirements.txt
└── eh/    eh_diagnose.py   + requirements.txt
```

`BASE = dirname(dirname(mcp_server.py))`, and each tool is referenced by the rule `BASE/<name>/<name>_diagnose.py`.

> [!IMPORTANT]
> There is no unified `requirements.txt`. To avoid conflicts and over-installation from independent per-tool dependencies, **each tool keeps its own `requirements.txt`**.

---

## Registered Tools

| MCP tool | Target | Key arguments | Internal invocation |
|---|---|---|---|
| `diagnose_postgres` | PostgreSQL Flexible Server | `host`, `dbname`, `resource_id`, `hours` | `pg_diagnose.py --aad --format json` |
| `diagnose_aks` | AKS cluster | `namespace`, `context`, `all_namespaces`, `prometheus_url`, `appinsights_id` | `aks_diagnose.py --format json` |
| `diagnose_adx` | Azure Data Explorer (Kusto) | `cluster`, `database`, `resource_id`, `region`, `hours` | `adx_diagnose.py --auth default --format json` |
| `diagnose_eventhub` | Azure Event Hubs | `resource_id`, `event_hub`, `window_minutes` | `eh_diagnose.py --azure-auth --eh-auth entra --format json` |

Each tool validates input (`RID` / `NS` / `CLUSTER` regex), then runs the diagnostic via `subprocess.run([..., "--format", "json"])` and returns `json.loads(stdout)`. Arguments are passed as an **argv list**, not a shell string.

### Output schema (Finding fields differ per tool)
- Common top level: `tool` / `target` / `health_score` / `findings[]`
- Severity values: **critical / warning / info / ok** (NOT high/medium/low)
- `pg` · `adx`: `findings[]` = severity / category / title / detail / recommendation
- `aks`: severity / component / title / detail / recommendation / **steps (list)**
- `eh`: its own schema (`checks[]` = category / severity / title / detail / evidence, `worst_severity`)

---

## Running

```powershell
# 1) Install each tool's dependencies into the MCP runtime Python env (per-tool requirements)
pip install -r "..\adx\requirements.txt"
pip install -r "..\eh\requirements.txt"
# (install pg/aks dependencies into the same env too)

# 2) Start the server (streamable-http, default port 8000 → endpoint /mcp)
python mcp_server.py
```

- The port can be changed via the `PORT` environment variable (default `8000`).
- Diagnostics run via `sys.executable`, so Azure credentials (Managed Identity / `az login` / environment variables) must be available in the **same Python environment that runs the MCP server**.
- For RBAC, see each diagnostic tool's README (e.g., ADX metrics = cluster `Monitoring Reader`, Event Hubs data plane = `Azure Event Hubs Data Receiver`).

### Container build (optional)
```
az acr build -r $ACR -t diag-mcp:v1 -f mcp/Dockerfile .   # from the BASE (Install File) root
```

---

## Adding a New Diagnostic Tool

Diagnostic tools keep getting added. When attaching a new tool (`<name>_diagnose.py`), follow these 4 steps.

### 1. Place the folder + files
```
Install File/<name>/
├── <name>_diagnose.py
└── requirements.txt
```

### 2. Support JSON output in the diagnostic (required)
Output **pure JSON only** to `stdout` (send logs/notes to `stderr`). Like pg/adx/eh, pass it through secret/PII/prompt-injection sanitization.

The pattern adds roughly 5 spots to the diagnostic source:

| # | Location (anchor) | What |
|---|-------------------|------|
| 0 | `from dataclasses import dataclass, field` | append `, asdict` if needed |
| 1 | after `demo: bool = False` in `class Config:` | add `format: str = "html"` field |
| 2 | right above `def parse_args(...)` | add `emit_json()` + `_clean()` functions |
| 3 | after argparse `--demo` | add `--format {html,json}` (or `{table,json}`) argument |
| 4 | `return Config(... demo=a.demo)` | append `, format=a.format` |
| 5 | after `score = health_score(...)` in `main()` (before snapshot/HTML) | `if cfg.format=="json": emit_json(...); return 0` |

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
    if isinstance(v, list):  return [_clean(x) for x in v]   # recursively mask lists (e.g. aks steps)
    return v

def emit_json(findings, score, target, tool="<name>_diagnose"):
    payload = {"tool": tool, "target": target, "health_score": score,
               "findings": [{k: _clean(val) for k, val in asdict(f).items()} for f in findings]}
    json.dump(payload, sys.stdout, ensure_ascii=False)
```

Add `--format {html|json}` (or `{table|json}`) to the CLI, and when `json`, call `emit_json(...)` and return immediately.

### 3. Add two parts to `mcp_server.py`
One path constant + one thin `@mcp.tool()` wrapper:

```python
# path (BASE/<name>/<name>_diagnose.py)
XXX_TOOL = os.path.join(BASE, "<name>", "<name>_diagnose.py")

@mcp.tool()
def diagnose_<name>(resource_id: str = "", ...) -> dict:
    """<target> diagnosis (read-only, JSON result)."""
    if resource_id and not RID.match(resource_id):     # input validation
        raise ValueError("invalid resource_id")
    cmd = [sys.executable, XXX_TOOL, "--format", "json", ...]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
    return json.loads(out.stdout)
```

- Validation regexes at the top of the server: `RID` (resource id), `NS` (namespace), `CLUSTER` (ADX cluster URI). Add a new pattern for a new type.
- Tune `timeout` to the target scale (default 180–240s).

### 4. Install dependencies + verify
```powershell
pip install -r "..\<name>\requirements.txt"
python ..\<name>\<name>_diagnose.py --demo --format json | python -m json.tool
# → check tool/target/health_score/findings keys
```

---

## Security Principles
- **Read-only**: only query/`.show`/ARM read/Azure Monitor read calls. Never modifies resources.
- **Output sanitization**: all JSON output is filtered for secret/PII/prompt-injection via `_clean`.
- **Input validation**: the MCP wrapper validates resource_id/namespace/cluster via regex before execution.
- **Argument-injection prevention**: user input is passed as an **argv list**, not a shell string (`subprocess.run([...])`).

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `json.loads` fails | The diagnostic mixed non-JSON text into stdout → logs must go to `stderr` |
| `FileNotFoundError` (tool path) | Check the location/spelling of `BASE/<name>/<name>_diagnose.py` |
| `ModuleNotFoundError` | The tool's `requirements.txt` is not installed in the MCP runtime Python env |
| Auth failure | Check Azure credentials (MI/`az login`/env) and RBAC in the MCP runtime env |
| `subprocess ... timeout` | Raise the wrapper's `timeout` for large targets |
