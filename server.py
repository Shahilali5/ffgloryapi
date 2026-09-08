"""
FFGlory Web Control Dashboard & REST API Server
Runs locally on http://localhost:8000
Provides live control, full-auto monitoring, and 1-click clan deployment.
"""

import sys
import asyncio
import os
import json

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List

from engine import FFGloryClient, AutoPilotSupervisor, load_config, save_config

client: Optional[FFGloryClient] = None
supervisor: Optional[AutoPilotSupervisor] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, supervisor
    client = FFGloryClient()
    supervisor = AutoPilotSupervisor(client)

    async def init_runner():
        try:
            await client.start()
            cfg = client.config.get("auto_pilot", {})
            if cfg.get("enabled", True):
                await supervisor.start()
        except Exception as e:
            print(f"Error starting client in background: {e}")

    asyncio.create_task(init_runner())
    yield
    if supervisor:
        await supervisor.stop()
    if client:
        await client.close()


app = FastAPI(
    title="Production Gateway (api.shahil-codex.xyz/ffv1)",
    description="Free Fire Glory Bot squad launching, squad telemetry, and guild stats API.",
    version="1.0.0",
    lifespan=lifespan
)


class DeployRequest(BaseModel):
    clan_id: str
    region: str = "in"


class ActionRequest(BaseModel):
    group_id: str
    action: str


class ConfigUpdateRequest(BaseModel):
    target_clans: Optional[List[dict]] = None
    auto_pilot: Optional[dict] = None


class BioUpdateRequest(BaseModel):
    group_id: Optional[str] = None
    token: Optional[str] = None
    bio: str = "ｆｆｇｌｏｒｙ．ｘｙｚ"


@app.get("/api/me")
async def get_me():
    res = await client.get_profile()
    return res.get("data", {})


@app.get("/api/groups")
async def get_groups():
    groups = await client.get_my_groups()
    return {"groups": groups}


@app.post("/api/deploy")
async def deploy_clan(req: DeployRequest):
    res = await client.deploy_auto_clan_group(req.clan_id, req.region)
    return res


@app.post("/api/group/action")
async def group_action(req: ActionRequest):
    action = req.action.lower()
    if action == "restart":
        res = await client.restart_group(req.group_id)
    elif action == "stop":
        res = await client.stop_group(req.group_id)
    elif action == "refund":
        res = await client.refund_group(req.group_id)
    elif action == "extend":
        res = await client.extend_group(req.group_id)
    elif action == "get-glory":
        res = await client.get_glory(req.group_id)
    elif action == "get-clan-info":
        res = await client.get_clan_info(req.group_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return res


@app.get("/api/group/tokens/{group_id}")
async def get_tokens(group_id: str):
    path = await client.download_squad_file(group_id)
    if path and os.path.exists(path):
        return FileResponse(path, filename=f"squad_{group_id}.txt")
    raise HTTPException(status_code=404, detail="Squad file not available")


@app.post("/api/bio/update")
async def update_bio_endpoint(req: BioUpdateRequest):
    if req.token:
        res = await client.update_bio(req.token, req.bio)
        return res
    elif req.group_id:
        res = await client.update_all_bios_for_squad(req.group_id, req.bio)
        return {"updated_count": len(res), "details": res}
    raise HTTPException(status_code=400, detail="Must provide token or group_id")


@app.post("/api/bio/update-all")
async def update_all_saved_bios(req: BioUpdateRequest):
    res = await client.update_all_saved_accounts_bios(req.bio)
    return {"updated_count": len(res), "details": res}


@app.get("/api/autopilot/status")
async def autopilot_status():
    if not supervisor:
        return {"running": False}
    return {
        "running": supervisor.running,
        "stats": supervisor.stats,
        "config": client.config.get("auto_pilot", {}),
        "target_clans": client.config.get("target_clans", [])
    }


@app.post("/api/autopilot/toggle")
async def autopilot_toggle():
    global supervisor
    if supervisor.running:
        await supervisor.stop()
    else:
        await supervisor.start()
    return {"running": supervisor.running}


@app.get("/api/config")
async def get_config_api():
    return load_config()


@app.post("/api/config")
async def update_config_api(req: ConfigUpdateRequest):
    cfg = load_config()
    if req.target_clans is not None:
        cfg["target_clans"] = req.target_clans
    if req.auto_pilot is not None:
        cfg["auto_pilot"].update(req.auto_pilot)
    save_config(cfg)
    client.config = cfg
    return {"success": True, "config": cfg}


# ------------------ Production Gateway (api.shahil-codex.xyz/ffv1) ------------------

gateway_router = APIRouter()

class BotLaunchRequest(BaseModel):
    guild_id: str
    server_id: Optional[str] = "630988ba-2649-430f-9369-0e6b352b7462"
    region: Optional[str] = "in"


@gateway_router.post(
    "/api/bot/launch",
    summary="Launch Free Fire Glory Bot squad into a Guild",
    description="Deploys a squad of automated glory bots into the specified Free Fire clan.",
    tags=["Bot"]
)
async def api_bot_launch(req: BotLaunchRequest):
    req_id = f"req_{uuid.uuid4().hex[:12]}"
    deploy_res = await client.deploy_auto_clan_group(req.guild_id, req.region or "in")
    if not deploy_res.get("ok"):
        raise HTTPException(status_code=400, detail=deploy_res.get("error", "Failed to launch bot squad"))

    groups = await client.get_my_groups()
    matching = next((g for g in groups if str(g.get("clan_id")) == str(req.guild_id)), None)
    group_id = matching.get("group_id") if matching else "pending"

    return {
        "success": True,
        "bot_request_id": req_id,
        "glory_group_id": str(group_id),
        "region": (req.region or "IND").upper(),
        "status": "launched"
    }


@gateway_router.get(
    "/api/squad/{group_id}/glory",
    summary="Get Real-time Squad Glory & Bot telemetry",
    description="Returns each individual bot's UID, nickname, level, EXP, and total glory accumulated.",
    tags=["Bot"]
)
async def api_squad_glory(group_id: str):
    res = await client.get_glory(group_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail="Squad glory data not available")

    raw_details = res.get("data", {}).get("details", [])
    details = []
    total_glory = 0
    for d in raw_details:
        g_pts = int(d.get("glory", 0))
        total_glory += g_pts
        details.append({
            "account_id": str(d.get("account_id")),
            "nickname": str(d.get("nickname")),
            "level": int(d.get("level", 1)),
            "exp": int(d.get("exp", 0)),
            "glory": g_pts
        })

    return {
        "success": True,
        "data": {
            "group_id": str(group_id),
            "total_glory": total_glory,
            "details": details
        }
    }


@gateway_router.get(
    "/api/bot/history",
    summary="Get History of Launched Bot Requests",
    description="Returns list of previously launched bot requests and their status.",
    tags=["Bot"]
)
async def api_bot_history():
    groups = await client.get_my_groups()
    history = []
    for g in groups:
        history.append({
            "glory_group_id": g.get("group_id"),
            "guild_id": g.get("clan_id"),
            "region": g.get("region", "IND"),
            "status": g.get("status"),
            "running_containers": g.get("running_count", 0),
            "total_containers": g.get("container_count", 4),
            "uptime": g.get("uptime", "0s"),
            "games_played": g.get("games_played", 0),
            "created_at": g.get("created_at"),
            "tokens_downloaded": g.get("squad_downloaded", False)
        })
    return {"success": True, "history": history}


@gateway_router.get(
    "/api/guild/{id}/stats",
    summary="Get Free Fire Guild / Clan Stats",
    description="Fetches live clan name, level, member count, capacity, and captain UID.",
    tags=["Guild"]
)
async def api_guild_stats(id: str, group_id: Optional[str] = None):
    target_gid = group_id
    groups = await client.get_my_groups()
    if not target_gid:
        matching = next((g for g in groups if str(g.get("clan_id")) == str(id)), None)
        if matching:
            target_gid = matching.get("group_id")
        elif groups:
            target_gid = groups[0].get("group_id")

    if not target_gid:
        raise HTTPException(status_code=404, detail=f"No bot squad found for guild {id}")

    info_res = await client.get_clan_info(target_gid)
    if not info_res.get("ok"):
        raise HTTPException(status_code=400, detail="Failed to fetch clan info from game gateway")

    decoded = info_res.get("data", {}).get("decoded", {})
    return {
        "success": True,
        "data": {
            "clan_id": int(decoded.get("clan_id", id)),
            "clan_name": str(decoded.get("clan_name", "")),
            "captain_id": int(decoded.get("captain_id", 0)),
            "clan_level": int(decoded.get("clan_level", 1)),
            "capacity": int(decoded.get("capacity", 30)),
            "member_num": int(decoded.get("member_num", 0)),
            "slogan": str(decoded.get("slogan", "")),
            "region": str(decoded.get("region", "IND"))
        }
    }


class SquadActionRequest(BaseModel):
    group_id: str

class SquadBioRequest(BaseModel):
    bio: str = "ｆｆｇｌｏｒｙ．ｘｙｚ"


@gateway_router.post(
    "/api/squad/{group_id}/restart",
    summary="Restart Free Fire Glory Bot Squad",
    description="Restarts frozen or lagging bot containers in a squad.",
    tags=["Bot"]
)
@gateway_router.post("/api/bot/restart", tags=["Bot"])
async def api_restart_squad(group_id: Optional[str] = None, req: Optional[SquadActionRequest] = None):
    gid = group_id or (req.group_id if req else None)
    if not gid:
        raise HTTPException(status_code=400, detail="Missing group_id")
    res = await client.restart_group(gid)
    return {"success": res.get("ok", False), "group_id": gid, "response": res.get("data")}


@gateway_router.post(
    "/api/squad/{group_id}/stop",
    summary="Stop Free Fire Glory Bot Squad",
    description="Stops running bot containers for a squad.",
    tags=["Bot"]
)
@gateway_router.post("/api/bot/stop", tags=["Bot"])
async def api_stop_squad(group_id: Optional[str] = None, req: Optional[SquadActionRequest] = None):
    gid = group_id or (req.group_id if req else None)
    if not gid:
        raise HTTPException(status_code=400, detail="Missing group_id")
    res = await client.stop_group(gid)
    return {"success": res.get("ok", False), "group_id": gid, "response": res.get("data")}


@gateway_router.post(
    "/api/squad/{group_id}/refund",
    summary="Request Refund for Squad",
    description="Requests credit refund for a squad running over 30 minutes without token download.",
    tags=["Bot"]
)
async def api_refund_squad(group_id: str):
    res = await client.refund_group(group_id)
    return {"success": res.get("ok", False), "group_id": group_id, "response": res.get("data")}


@gateway_router.get(
    "/api/squad/{group_id}/details",
    summary="Get Complete Squad Details & Telemetry",
    description="Fetches full squad status, uptime, games played, bot telemetry, and clan details.",
    tags=["Bot"]
)
async def api_squad_details(group_id: str):
    groups = await client.get_my_groups()
    matching = next((g for g in groups if str(g.get("group_id")) == str(group_id)), None)
    if not matching:
        raise HTTPException(status_code=404, detail=f"Squad {group_id} not found")

    glory_res = await client.get_glory(group_id)
    raw_details = glory_res.get("data", {}).get("details", []) if glory_res.get("ok") else []
    total_glory = sum(int(d.get("glory", 0)) for d in raw_details)

    return {
        "success": True,
        "squad": {
            "group_id": matching.get("group_id"),
            "guild_id": matching.get("clan_id"),
            "region": matching.get("region", "IND"),
            "status": matching.get("status"),
            "running_containers": matching.get("running_count", 0),
            "total_containers": matching.get("container_count", 4),
            "uptime": matching.get("uptime", "0s"),
            "games_played": matching.get("games_played", 0),
            "created_at": matching.get("created_at"),
            "can_extend": matching.get("can_extend", True),
            "tokens_downloaded": matching.get("squad_downloaded", False),
            "total_glory": total_glory,
            "bots": [
                {
                    "account_id": str(d.get("account_id")),
                    "nickname": str(d.get("nickname")),
                    "level": int(d.get("level", 1)),
                    "exp": int(d.get("exp", 0)),
                    "glory": int(d.get("glory", 0)),
                    "credit_score": int(d.get("credit_score", 100))
                }
                for d in raw_details
            ]
        }
    }


@gateway_router.get(
    "/api/bot/balance",
    summary="Get User Balance & Limits",
    description="Returns available basic and premium credits, account role, and max group limits.",
    tags=["Bot"]
)
async def api_bot_balance():
    res = await client.get_profile()
    data = res.get("data", {})
    return {
        "success": True,
        "username": data.get("username"),
        "role": data.get("role"),
        "basic_credits": data.get("basic_credits", 0),
        "premium_credits": data.get("premium_credits", 0),
        "max_groups": data.get("max_groups", 100)
    }


@gateway_router.post(
    "/api/squad/{group_id}/bio",
    summary="Update In-Game Bio for Squad Bots",
    description="Updates signature/bio on Free Fire for all 4 bots in the squad.",
    tags=["Accounts & Bio"]
)
async def api_squad_bio(group_id: str, req: SquadBioRequest):
    res = await client.update_all_bios_for_squad(group_id, req.bio)
    return {"success": True, "group_id": group_id, "updated_count": len(res), "details": res}


@gateway_router.get(
    "/api/accounts/list",
    summary="Get List of Saved Bot Accounts",
    description="Returns all extracted Free Fire bot accounts saved in the accounts vault.",
    tags=["Accounts & Bio"]
)
async def api_accounts_list():
    accounts_file = os.path.join(os.path.dirname(__file__), "accounts", "accounts.txt")
    if not os.path.exists(accounts_file):
        return {"success": True, "count": 0, "accounts": []}

    accounts = []
    with open(accounts_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            accounts.append({
                "uid": parts[0] if len(parts) > 0 else "",
                "password": parts[1] if len(parts) > 1 else "",
                "jwt_token": parts[2] if len(parts) > 2 else "",
                "nickname": parts[3] if len(parts) > 3 else "",
                "proxy_ip": parts[4] if len(parts) > 4 else ""
            })
    return {"success": True, "count": len(accounts), "accounts": accounts}


app.include_router(gateway_router)
app.include_router(gateway_router, prefix="/ffv1")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FFGlory Full-Auto Command Center</title>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --border: #1f2937;
      --accent: #f59e0b;
      --accent-glow: rgba(245, 158, 11, 0.18);
      --success: #10b981;
      --danger: #ef4444;
      --text: #f9fafb;
      --muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Space Grotesk', sans-serif;
      min-height: 100vh;
      padding: 24px;
    }
    .container {
      max-width: 1240px;
      margin: 0 auto;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .badge {
      background: var(--accent-glow);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--accent);
    }
    .top-actions {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .btn {
      background: var(--card-bg);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 8px 16px;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      font-size: 14px;
      transition: all 0.2s ease;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .btn:hover { border-color: var(--accent); }
    .btn-primary {
      background: var(--accent);
      color: #000;
      border-color: var(--accent);
    }
    .btn-primary:hover {
      background: #d97706;
      box-shadow: 0 0 15px var(--accent-glow);
    }
    .btn-success { background: #065f46; border-color: #059669; color: #34d399; }
    .btn-danger { background: #7f1d1d; border-color: #dc2626; color: #f87171; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
    }
    .card-title {
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 8px;
    }
    .card-val {
      font-size: 28px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }
    .card-val.accent { color: var(--accent); }
    .card-val.green { color: var(--success); }
    
    .panel {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 24px;
      overflow: hidden;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
    }
    .panel-title {
      font-size: 16px;
      font-weight: 700;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }
    th, td {
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
    }
    th {
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 12px;
    }
    .tag {
      padding: 4px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      font-family: 'JetBrains Mono', monospace;
    }
    .tag-running { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }
    .tag-stopped { background: rgba(156, 163, 175, 0.15); color: #9ca3af; border: 1px solid rgba(156, 163, 175, 0.3); }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .actions-cell {
      display: flex;
      gap: 8px;
    }
    .btn-sm {
      padding: 4px 10px;
      font-size: 12px;
    }
    .logs-box {
      background: #060911;
      padding: 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: #38bdf8;
      height: 220px;
      overflow-y: auto;
      border-radius: 8px;
      line-height: 1.6;
    }
    .modal {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.8);
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .modal.active { display: flex; }
    .modal-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 24px;
      border-radius: 12px;
      width: 420px;
    }
    .modal-title { margin-bottom: 16px; }
    .input-field {
      width: 100%;
      background: #0b0f19;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 8px;
      margin-bottom: 16px;
      font-family: inherit;
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 12px;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <h1>FFGlory Full-Auto</h1>
        <span class="badge">LIVE ENGINE</span>
      </div>
      <div class="top-actions">
        <button id="toggle-pilot-btn" class="btn btn-success" onclick="togglePilot()">
          <span>AUTO-PILOT: ACTIVE</span>
        </button>
        <button class="btn" onclick="promptChangeAllBios()">✏️ Change All Bios</button>
        <button class="btn btn-primary" onclick="openDeployModal()">+ Deploy Clan Squad</button>
      </div>
    </header>

    <div class="grid">
      <div class="card">
        <div class="card-title">User Balance</div>
        <div class="card-val accent" id="credits-val">-- Credits</div>
      </div>
      <div class="card">
        <div class="card-title">Active Running Squads</div>
        <div class="card-val green" id="active-squads-val">0</div>
      </div>
      <div class="card">
        <div class="card-title">Auto-Restarts Healed</div>
        <div class="card-val" id="restarts-val">0</div>
      </div>
      <div class="card">
        <div class="card-title">Completed Squads</div>
        <div class="card-val" id="completed-val">0</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">My Squad Groups</div>
        <button class="btn btn-sm" onclick="refreshData()">Refresh</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>Group ID</th>
            <th>Clan ID</th>
            <th>Region</th>
            <th>Status</th>
            <th>Containers</th>
            <th>Uptime</th>
            <th>Games</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="groups-table-body">
          <tr><td colspan="8" style="text-align:center; color: var(--muted);">Loading squads...</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">Real-Time Auto-Pilot Live Feed</div>
      </div>
      <div style="padding: 16px;">
        <div class="logs-box" id="logs-feed">Connecting to live supervisor...</div>
      </div>
    </div>
  </div>

  <!-- Deploy Modal -->
  <div class="modal" id="deploy-modal">
    <div class="modal-box">
      <h3 class="modal-title">Deploy Full-Auto Clan Squad</h3>
      <label style="font-size: 13px; color: var(--muted); margin-bottom: 6px; display: block;">Free Fire Clan ID</label>
      <input type="text" id="deploy-clan-id" class="input-field" placeholder="e.g. 3053079503" value="3053079503">

      <label style="font-size: 13px; color: var(--muted); margin-bottom: 6px; display: block;">Region</label>
      <input type="text" id="deploy-region" class="input-field" placeholder="e.g. in" value="in">

      <div class="modal-actions">
        <button class="btn" onclick="closeDeployModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitDeploy()">Deploy Now (1 Credit)</button>
      </div>
    </div>
  </div>

  <script>
    async function refreshData() {
      try {
        const [meRes, groupsRes, statusRes] = await Promise.all([
          fetch('/api/me').then(r => r.json()),
          fetch('/api/groups').then(r => r.json()),
          fetch('/api/autopilot/status').then(r => r.json())
        ]);

        if (meRes.username) {
          document.getElementById('credits-val').innerText = `${meRes.basic_credits} Basic / ${meRes.premium_credits} Prem`;
        }

        const stats = statusRes.stats || {};
        document.getElementById('active-squads-val').innerText = stats.active_squads_count || 0;
        document.getElementById('restarts-val').innerText = stats.restarts_triggered || 0;
        document.getElementById('completed-val').innerText = stats.squads_completed || 0;

        const pilotBtn = document.getElementById('toggle-pilot-btn');
        if (statusRes.running) {
          pilotBtn.className = 'btn btn-success';
          pilotBtn.innerHTML = '<span>AUTO-PILOT: ACTIVE</span>';
        } else {
          pilotBtn.className = 'btn btn-danger';
          pilotBtn.innerHTML = '<span>AUTO-PILOT: STOPPED</span>';
        }

        // Render logs
        const logsBox = document.getElementById('logs-feed');
        if (stats.logs && stats.logs.length) {
          logsBox.innerHTML = stats.logs.slice().reverse().map(l => `<div>${l}</div>`).join('');
        }

        // Render groups
        const tbody = document.getElementById('groups-table-body');
        const groups = groupsRes.groups || [];
        if (!groups.length) {
          tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--muted);">No active squads found.</td></tr>';
          return;
        }

        tbody.innerHTML = groups.map(g => {
          const isRunning = g.status === 'running';
          const tagClass = isRunning ? 'tag-running' : 'tag-stopped';
          return `
            <tr>
              <td class="mono">${g.group_id}</td>
              <td class="mono">${g.clan_id}</td>
              <td>${g.region || 'IND'}</td>
              <td><span class="tag ${tagClass}">${g.status}</span></td>
              <td class="mono">${g.running_count || 0}/${g.container_count || 4}</td>
              <td>${g.uptime || '0s'}</td>
              <td>${g.games_played || 0}</td>
              <td class="actions-cell">
                ${isRunning ? `
                  <button class="btn btn-sm" onclick="doAction('${g.group_id}', 'restart')">Restart</button>
                  <button class="btn btn-sm btn-danger" onclick="doAction('${g.group_id}', 'stop')">Stop</button>
                ` : `
                  <button class="btn btn-sm btn-success" onclick="doAction('${g.group_id}', 'restart')">Start</button>
                `}
                <button class="btn btn-sm" onclick="viewGlory('${g.group_id}')">Glory</button>
                <button class="btn btn-sm" onclick="downloadTokens('${g.group_id}')">Tokens</button>
                <button class="btn btn-sm" onclick="changeSquadBio('${g.group_id}')">Bio</button>
              </td>
            </tr>
          `;
        }).join('');

      } catch (err) {
        console.error('Refresh error:', err);
      }
    }

    async function doAction(groupId, action) {
      try {
        const res = await fetch('/api/group/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({group_id: groupId, action: action})
        }).then(r => r.json());
        alert(`Action ${action}: ${JSON.stringify(res.data || res)}`);
        refreshData();
      } catch (e) {
        alert('Action error: ' + e);
      }
    }

    async function viewGlory(groupId) {
      try {
        const res = await fetch('/api/group/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({group_id: groupId, action: 'get-glory'})
        }).then(r => r.json());
        const details = res.data && res.data.details ? res.data.details : [];
        let msg = `Squad ${groupId} Details:\n\n`;
        let total = 0;
        details.forEach(d => {
          total += d.glory || 0;
          msg += `Bot: ${d.nickname} (${d.account_id}) | Glory: ${d.glory} | Credit: ${d.credit_score} | Level: ${d.level}\n`;
        });
        msg += `\nTotal Glory Farmed: ${total}`;
        alert(msg);
      } catch (e) {
        alert('Glory check error: ' + e);
      }
    }

    function downloadTokens(groupId) {
      window.open(`/api/group/tokens/${groupId}`, '_blank');
    }

    async function changeSquadBio(groupId) {
      const bio = prompt('Enter new bio for this squad bots:', 'ｆｆｇｌｏｒｙ．ｘｙｚ');
      if (!bio) return;
      try {
        const res = await fetch('/api/bio/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({group_id: groupId, bio: bio})
        }).then(r => r.json());
        alert(`Bio updated for ${res.updated_count || 0} bots!`);
      } catch (err) {
        alert('Bio update error: ' + err);
      }
    }

    async function promptChangeAllBios() {
      const bio = prompt('Enter new in-game bio for ALL saved bot accounts:', 'ｆｆｇｌｏｒｙ．ｘｙｚ');
      if (!bio) return;
      try {
        const res = await fetch('/api/bio/update-all', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({bio: bio})
        }).then(r => r.json());
        alert(`Bio changed for all ${res.updated_count || 0} saved accounts!`);
      } catch (err) {
        alert('Bio update error: ' + err);
      }
    }

    async function togglePilot() {
      await fetch('/api/autopilot/toggle', {method: 'POST'});
      refreshData();
    }

    function openDeployModal() {
      document.getElementById('deploy-modal').classList.add('active');
    }

    function closeDeployModal() {
      document.getElementById('deploy-modal').classList.remove('active');
    }

    async function submitDeploy() {
      const clanId = document.getElementById('deploy-clan-id').value.trim();
      const region = document.getElementById('deploy-region').value.trim();
      if (!clanId) return alert('Enter Clan ID');
      closeDeployModal();
      alert('Deploying clan squad in background. Watch live feed for progress!');
      await fetch('/api/deploy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clan_id: clanId, region: region})
      });
      refreshData();
    }

    setInterval(refreshData, 10000);
    refreshData();
  </script>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3001))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("server:app", host=host, port=port, loop="asyncio")
