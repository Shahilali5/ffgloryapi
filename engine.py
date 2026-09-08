"""
FFGlory Full-Auto Engine & Core Client
Handles Cloudflare bypass via Playwright persistent Chrome session and exposes
clean Python / REST / CLI APIs for 100% full automation.
"""

import sys
import asyncio
import os
import json
import logging
import datetime
import time
from typing import Dict, Any, List, Optional

import httpx

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from playwright.async_api import async_playwright, BrowserContext, Page

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("FFGloryEngine")


def to_fullwidth(text: str) -> str:
    """
    Converts ASCII alphanumeric and punctuation characters into fullwidth unicode characters
    (e.g., 'ffglory.xyz' -> 'ｆｆｇｌｏｒｙ．ｘｙｚ', matching 'ｗｗｗ．ｆｆｇｌｏｒｙ．ｐｒｏ').
    Leaves already-fullwidth or special unicode characters unchanged.
    """
    if not text:
        return text
    res = []
    for c in text:
        code = ord(c)
        if 0x21 <= code <= 0x7E:
            res.append(chr(code + 0xFEE0))
        elif code == 0x20:
            res.append(chr(0x3000))
        else:
            res.append(c)
    return "".join(res)


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_CONFIG = {
    "username": "ffgloryindia",
    "password": "@peyvDK5@_bfTfc",
    "target_clans": [],
    "auto_pilot": {
        "enabled": True,
        "poll_interval_seconds": 30,
        "auto_restart_frozen": True,
        "auto_refund_idle_30m": False,
        "auto_extend_if_needed": True,
        "auto_download_tokens": True,
        "auto_change_bio": True,
        "bio_interval_seconds": 3600,
        "bot_bio": "ｆｆｇｌｏｒｙ．ｘｙｚ",
        "max_concurrent_squads": 5
    },
    "tokens_dir": os.path.join(os.path.dirname(__file__), "accounts")
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading config: {e}")
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class FFGloryClient:
    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_config()
        self.base_url = "https://www.ffglory.pro"
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_logged_in = False
        self.user_profile = {}
        self.lock = asyncio.Lock()
        self.user_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".session"))
        self.last_bio_updates: Dict[str, float] = {}

        tokens_dir = self.config.get("tokens_dir", "accounts")
        os.makedirs(tokens_dir, exist_ok=True)

    async def start(self):
        """Initializes the browser context and clears Cloudflare"""
        if self.context and self.page and not self.page.is_closed():
            return

        logger.info("Launching Chrome session for FFGlory...")
        # Auto-clean lockfile if previous session closed abruptly
        for lf in ["lockfile", "SingletonLock"]:
            p = os.path.join(self.user_data_dir, lf)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        self.playwright = await async_playwright().start()
        
        if sys.platform != "win32":
            chrome_exe = "/usr/bin/google-chrome"
            if not os.path.exists(chrome_exe):
                chrome_exe = "/usr/bin/google-chrome-stable"
            has_display = bool(os.environ.get("DISPLAY"))
            headless_mode = not has_display
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            extra_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,800"
            ]
        else:
            chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if not os.path.exists(chrome_exe):
                chrome_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            headless_mode = False
            ua = None
            extra_args = []

        self.context = await self.playwright.chromium.launch_persistent_context(
            self.user_data_dir,
            executable_path=chrome_exe,
            headless=headless_mode,
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check"
            ] + extra_args
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if stealth_async:
            await stealth_async(self.page)
        
        logger.info("Navigating to FFGlory portal...")
        await self.page.goto(f"{self.base_url}/login", wait_until="domcontentloaded")

        await self.restore_cookies_cache()
        # Cloudflare clearance loop with active Turnstile solver
        await self.solve_turnstile_if_present(max_duration=35)
        await self.ensure_login()

    def get_cookies_cache_path(self) -> str:
        return os.path.join(self.user_data_dir, "session_cookies.json")

    def save_cookies_cache(self, cookies: List[dict]):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            p = self.get_cookies_cache_path()
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            logger.info(f"Saved {len(cookies)} session cookies to cache")
        except Exception as e:
            logger.debug(f"Failed to save cookies: {e}")

    async def restore_cookies_cache(self):
        try:
            p = self.get_cookies_cache_path()
            if os.path.exists(p) and self.context:
                with open(p, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                if cookies and isinstance(cookies, list):
                    await self.context.add_cookies(cookies)
                    logger.info(f"Restored {len(cookies)} session cookies from cache")
        except Exception as e:
            logger.debug(f"Failed to restore cookies: {e}")

    async def solve_turnstile_if_present(self, max_duration: int = 35) -> bool:
        """Detects Cloudflare Turnstile challenge and solves it with human-like mouse click"""
        if not self.page or self.page.is_closed():
            return False

        start_time = time.time()
        last_click = 0

        while time.time() - start_time < max_duration:
            try:
                title = await self.page.title()
                if "Just a moment" not in title and "Cloudflare" not in title and "Verifying" not in title:
                    logger.info(f"Cloudflare Turnstile cleared! Title: {title}")
                    if self.context:
                        cookies = await self.context.cookies()
                        self.save_cookies_cache(cookies)
                    return True
            except Exception:
                pass

            now = time.time()
            if now - last_click >= 4:
                for f in self.page.frames:
                    if "challenges.cloudflare.com" in f.url:
                        try:
                            body = await f.query_selector("body")
                            if body:
                                box = await body.bounding_box()
                                if box and box.get("width", 0) > 60:
                                    cx = box["x"] + 24
                                    cy = box["y"] + 30
                                    logger.info(f"Found Turnstile iframe. Clicking checkbox at ({cx}, {cy})...")
                                    await self.page.mouse.move(cx - 40, cy - 25)
                                    await asyncio.sleep(0.1)
                                    await self.page.mouse.move(cx, cy, steps=6)
                                    await asyncio.sleep(0.15)
                                    await self.page.mouse.down()
                                    await asyncio.sleep(0.12)
                                    await self.page.mouse.up()
                                    last_click = now
                                    logger.info("Clicked Turnstile checkbox! Waiting for clearance...")
                                    break
                        except Exception:
                            pass

            await asyncio.sleep(1)

        try:
            title = await self.page.title()
            passed = "Just a moment" not in title
            if passed and self.context:
                cookies = await self.context.cookies()
                self.save_cookies_cache(cookies)
            return passed
        except Exception:
            return False

    async def ensure_login(self, force_refresh: bool = False) -> bool:
        """Logs into FFGlory account and verifies session; auto self-fixes if token expired"""
        async with self.lock:
            if not force_refresh:
                # Check current auth status
                me_res = await self.evaluate_fetch("/api/auth/me", check_login=False)
                if me_res.get("status") == 200 and me_res.get("data", {}).get("username"):
                    self.is_logged_in = True
                    self.user_profile = me_res["data"]
                    logger.info(f"Already authenticated as: {self.user_profile.get('username')} (Credits: Basic={self.user_profile.get('basic_credits')}, Premium={self.user_profile.get('premium_credits')})")
                    return True

            logger.info("Session expired or unauthenticated. Auto-recovering session & logging in...")
            login_payload = {
                "username": self.config["username"],
                "password": self.config["password"]
            }
            res = await self.evaluate_fetch("/api/auth/login", method="POST", payload=login_payload, check_login=False)
            if res.get("status") == 200 and res.get("data", {}).get("success"):
                self.is_logged_in = True
                logger.info("Auto-login successful! Session recovered.")
                
                # Refresh profile info
                me_res = await self.evaluate_fetch("/api/auth/me", check_login=False)
                self.user_profile = me_res.get("data", {})
                
                # Save fresh cookies to cache
                if self.context:
                    cookies = await self.context.cookies()
                    self.save_cookies_cache(cookies)
                return True
            else:
                logger.error(f"Login failed: {res}")
                return False

    async def evaluate_fetch(self, endpoint: str, method: str = "GET", payload: Optional[Any] = None, check_login: bool = True) -> Dict[str, Any]:
        """Executes a fetch request with Cloudflare auto-clearance and expired token self-healing"""
        if not self.page or self.page.is_closed():
            await self.start()

        script = """
        async ([endpoint, method, payload]) => {
            const opts = {
                method: method,
                headers: {}
            };
            if (payload) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(payload);
            }
            try {
                const res = await fetch(endpoint, opts);
                const contentType = res.headers.get('content-type') || '';
                let data = null;
                if (contentType.includes('application/json') || contentType.includes('text/plain')) {
                    const text = await res.text();
                    try {
                        data = JSON.parse(text);
                    } catch (e) {
                        data = text;
                    }
                } else {
                    data = await res.text();
                }
                return { status: res.status, ok: res.ok, data: data };
            } catch (err) {
                return { error: err.toString(), status: 0, ok: false };
            }
        }
        """
        for attempt in range(2):
            try:
                title = await self.page.title()
                if "Just a moment" in title or "Cloudflare" in title or "Verifying" in title:
                    logger.info("Cloudflare challenge detected on page, solving Turnstile...")
                    await self.solve_turnstile_if_present(max_duration=25)

                res = await self.page.evaluate(script, [endpoint, method, payload])
                data_val = res.get("data")
                if isinstance(data_val, str) and ("Just a moment" in data_val or "<!DOCTYPE html>" in data_val):
                    logger.info("Cloudflare challenge intercepted API fetch. Refreshing page session to clear...")
                    async with self.lock:
                        await self.page.goto(f"{self.base_url}/client", wait_until="domcontentloaded")
                        await self.solve_turnstile_if_present(max_duration=25)
                    continue

                # Auto self-fix if session token expired
                if check_login and (res.get("status") in (401, 403) or (isinstance(data_val, dict) and data_val.get("message") == "Unauthorized")):
                    logger.warning(f"Session token expired on {method} {endpoint}! Auto self-fixing session...")
                    logged_in = await self.ensure_login(force_refresh=True)
                    if logged_in:
                        logger.info("Session refreshed. Retrying request...")
                        return await self.page.evaluate(script, [endpoint, method, payload])

                return res
            except Exception as e:
                logger.error(f"Execution error on {method} {endpoint}: {e}")
                if attempt == 1:
                    return {"status": 0, "ok": False, "error": str(e)}
                await asyncio.sleep(1)
        return {"status": 0, "ok": False, "error": "Cloudflare challenge timeout"}

    # ------------------ High Level API Methods ------------------

    async def get_profile(self) -> dict:
        """Fetch user credits, role, and max limits"""
        res = await self.evaluate_fetch("/api/auth/me")
        if res.get("ok"):
            self.user_profile = res.get("data", {})
        return res

    async def get_my_groups(self) -> List[dict]:
        """Returns all running and stopped squad groups"""
        res = await self.evaluate_fetch("/api/client/my-groups")
        if res.get("ok") and isinstance(res.get("data"), list):
            return res.get("data", [])
        return []

    async def get_clan_info(self, group_id: str) -> dict:
        """Query clan meta (name, captain, level, capacity, member count)"""
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "get-clan-info"}
        )

    async def get_glory(self, group_id: str) -> dict:
        """Fetch real-time glory details for each bot in the squad"""
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "get-glory"}
        )

    async def restart_group(self, group_id: str) -> dict:
        """Restart frozen or lagging bot containers in a squad"""
        logger.info(f"Restarting squad group: {group_id}")
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "restart"}
        )

    async def stop_group(self, group_id: str) -> dict:
        """Stop a squad once glory target is achieved"""
        logger.info(f"Stopping squad group: {group_id}")
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "stop"}
        )

    async def refund_group(self, group_id: str) -> dict:
        """Refund a squad that has been running for 30+ minutes"""
        logger.info(f"Requesting refund for group: {group_id}")
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "refund"}
        )

    async def extend_group(self, group_id: str) -> dict:
        """Extend squad duration"""
        logger.info(f"Extending squad group: {group_id}")
        return await self.evaluate_fetch(
            "/api/client/group-action",
            method="POST",
            payload={"group_id": str(group_id), "action": "extend"}
        )

    async def download_squad_file(self, group_id: str) -> Optional[str]:
        """Download raw bot tokens & credentials for the squad into accounts/"""
        res = await self.evaluate_fetch(f"/api/client/squad-file?group_id={group_id}")
        if res.get("ok") and res.get("data"):
            accounts_dir = os.path.join(os.path.dirname(__file__), "accounts")
            os.makedirs(accounts_dir, exist_ok=True)
            raw_content = str(res["data"])

            # 1. Save specific squad file: accounts/<group_id>.txt
            squad_file = os.path.join(accounts_dir, f"{group_id}.txt")
            with open(squad_file, "w", encoding="utf-8") as f:
                f.write(raw_content)

            # 2. Append all accounts to accounts/accounts.txt & accounts/tokens.txt
            all_accounts_file = os.path.join(accounts_dir, "accounts.txt")
            tokens_only_file = os.path.join(accounts_dir, "tokens.txt")

            parsed_accounts = []
            for line in raw_content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    acc = json.loads(line)
                    parsed_accounts.append(acc)
                except Exception:
                    pass

            if parsed_accounts:
                with open(all_accounts_file, "a", encoding="utf-8") as f_acc:
                    for acc in parsed_accounts:
                        uid = acc.get("uid", "")
                        pwd = acc.get("password", "")
                        jwt = acc.get("jwt_token", "")
                        nick = acc.get("nickname", "")
                        ip = acc.get("ip", "")
                        f_acc.write(f"{uid}:{pwd}:{jwt}:{nick}:{ip}\n")

                with open(tokens_only_file, "a", encoding="utf-8") as f_tok:
                    for acc in parsed_accounts:
                        jwt = acc.get("jwt_token", "")
                        if jwt:
                            f_tok.write(f"{jwt}\n")

            logger.info(f"Saved squad tokens to {squad_file}, accounts/accounts.txt, and accounts/tokens.txt")
            return squad_file
        return None

    async def deploy_auto_clan_group(self, clan_id: str, region: str = "in") -> dict:
        """
        Dispatches 4 auto bot accounts directly to the specified clan.
        Monitors creation stream until completion.
        """
        logger.info(f"Deploying new full-auto squad for Clan: {clan_id} (Region: {region})...")
        
        # Check credits first
        await self.get_profile()
        basic_credits = self.user_profile.get("basic_credits", 0)
        if basic_credits < 1:
            logger.warning("Zero basic credits available! Cannot deploy squad.")
            return {"ok": False, "error": "Insufficient credits", "credits": basic_credits}

        script = """
        async ([clan_id, region]) => {
            try {
                const response = await fetch('/api/client/auto-clan-group', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ clan_id: String(clan_id), region: region })
                });
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let fullText = '';
                let newGroupId = null;
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    const chunk = decoder.decode(value, { stream: true });
                    fullText += chunk;
                    const match = chunk.match(/"group_id":\\s*"(\\\\d+)"/);
                    if (match) newGroupId = match[1];
                }
                return { status: response.status, ok: response.ok, body: fullText, group_id: newGroupId };
            } catch (err) {
                return { error: err.toString(), ok: false };
            }
        }
        """
        res = await self.page.evaluate(script, [clan_id, region])
        logger.info(f"Deployment result for {clan_id}: {res.get('ok')}")

        if res.get("ok"):
            new_gid = res.get("group_id")
            if not new_gid:
                await asyncio.sleep(1)
                groups = await self.get_my_groups()
                new_group = next((g for g in groups if str(g.get("clan_id")) == str(clan_id)), None)
                if new_group:
                    new_gid = new_group.get("group_id")

            if new_gid:
                logger.info(f"Squad {new_gid} launched! Instant downloading accounts txt...")
                # 1. Instant download accounts txt
                await self.download_squad_file(new_gid)

                # 2. Instant change all bios after launch
                cfg = self.config.get("auto_pilot", {})
                if cfg.get("auto_change_bio", True):
                    bio = cfg.get("bot_bio", "ｆｆｇｌｏｒｙ．ｘｙｚ")
                    formatted_bio = to_fullwidth(bio)
                    logger.info(f"Instant changing all bios after launch for squad {new_gid} to '{formatted_bio}'...")
                    bio_results = await self.update_all_bios_for_squad(new_gid, formatted_bio)
                    res["bio_updates"] = bio_results
                    res["group_id"] = new_gid

        # Update profile credits
        await self.get_profile()
        return res

    async def update_bio(self, token: str, bio: Optional[str] = None) -> dict:
        """
        Update Free Fire in-game signature/bio for a bot account using its JWT token
        via FFTools Bio API (captured in netcapture_1788749897482.json)
        """
        cfg = self.config.get("auto_pilot", {})
        raw_bio = bio or cfg.get("bot_bio", "ｆｆｇｌｏｒｙ．ｘｙｚ")
        formatted_bio = to_fullwidth(raw_bio)
        logger.info(f"Updating bio for bot token ({token[:20]}...) to: '{formatted_bio}'")

        url = "https://www.fftools.site/api/update-bio"
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://www.fftools.site",
            "Referer": "https://www.fftools.site/free-fire-long-bio",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        payload = {"token": token, "bio": formatted_bio}

        try:
            async with httpx.AsyncClient(timeout=15.0) as http_client:
                resp = await http_client.post(url, json=payload, headers=headers)
                try:
                    data = resp.json()
                except Exception:
                    data = {"text": resp.text}

                ok = resp.status_code == 200 and (data.get("success") is True or data.get("nickname") == "Success")
                return {"status": resp.status_code, "ok": ok, "data": data}
        except Exception as e:
            logger.error(f"HTTP error calling bio update API: {e}")
            return {"status": 0, "ok": False, "error": str(e)}

    async def update_all_bios_for_squad(self, group_id: str, bio: Optional[str] = None) -> List[dict]:
        """Downloads squad tokens and changes bio for all 4 bots in the squad"""
        cfg = self.config.get("auto_pilot", {})
        target_bio = to_fullwidth(bio or cfg.get("bot_bio", "ｆｆｇｌｏｒｙ．ｘｙｚ"))
        squad_file = await self.download_squad_file(group_id)
        if not squad_file or not os.path.exists(squad_file):
            accounts_dir = os.path.join(os.path.dirname(__file__), "accounts")
            fallback = os.path.join(accounts_dir, f"{group_id}.txt")
            if os.path.exists(fallback):
                squad_file = fallback
            else:
                logger.warning(f"Could not locate tokens for squad {group_id}")
                return []

        results = []
        with open(squad_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    acc = json.loads(line)
                    jwt = acc.get("jwt_token")
                    nick = acc.get("nickname", "")
                    if jwt:
                        res = await self.update_bio(jwt, target_bio)
                        success = res.get("ok") and (res.get("data", {}).get("success") or res.get("data", {}).get("nickname") == "Success")
                        if not success and "Invalid Token" in str(res.get("data", {})):
                            logger.warning(f"Bot token expired for {nick} ({acc.get('uid')})! Auto re-downloading fresh squad tokens from portal...")
                            await self.download_squad_file(group_id)
                        logger.info(f"Bio updated for bot {nick} ({acc.get('uid')}): {success}")
                        results.append({"nickname": nick, "account_id": acc.get("account_id"), "result": res})
                except Exception as e:
                    logger.error(f"Error updating bio: {e}")

        self.last_bio_updates[str(group_id)] = time.time()
        return results

    async def update_all_saved_accounts_bios(self, bio: Optional[str] = None) -> List[dict]:
        """Changes bio for all accounts currently saved in accounts/accounts.txt"""
        cfg = self.config.get("auto_pilot", {})
        target_bio = to_fullwidth(bio or cfg.get("bot_bio", "ｆｆｇｌｏｒｙ．ｘｙｚ"))
        accounts_file = os.path.join(os.path.dirname(__file__), "accounts", "accounts.txt")
        if not os.path.exists(accounts_file):
            return []

        results = []
        with open(accounts_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    jwt = parts[2]
                    nick = parts[3] if len(parts) > 3 else "bot"
                    res = await self.update_bio(jwt, target_bio)
                    results.append({"nickname": nick, "result": res})
        return results

    async def close(self):
        """Close browser resources"""
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


# ------------------ Full-Auto Supervisor Loop ------------------

class AutoPilotSupervisor:
    """
    Watches all running squads, auto-restarts frozen containers,
    calculates farmed glory, stops squads when target is reached,
    and auto-deploys pending clans from config queue.
    """
    def __init__(self, client: FFGloryClient):
        self.client = client
        self.running = False
        self.task = None
        self.stats = {
            "total_glory_farmed": 0,
            "restarts_triggered": 0,
            "squads_completed": 0,
            "active_squads_count": 0,
            "total_bio_updates": 0,
            "last_bio_update": None,
            "last_check": None,
            "logs": []
        }

    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        logger.info(entry)
        self.stats["logs"].append(entry)
        if len(self.stats["logs"]) > 100:
            self.stats["logs"].pop(0)

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())
        self.log("Full-Auto Watchdog Pilot started!")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
        self.log("Full-Auto Watchdog Pilot stopped.")

    async def _loop(self):
        while self.running:
            try:
                cfg = self.client.config.get("auto_pilot", {})
                poll_interval = cfg.get("poll_interval_seconds", 30)

                await self._tick()
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log(f"Supervisor error: {e}")
                await asyncio.sleep(10)

    async def _tick(self):
        self.stats["last_check"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg = self.client.config.get("auto_pilot", {})
        
        # 1. Fetch live groups
        groups = await self.client.get_my_groups()
        running_groups = [g for g in groups if g.get("status") == "running"]
        self.stats["active_squads_count"] = len(running_groups)

        for group in running_groups:
            group_id = group.get("group_id")
            clan_id = group.get("clan_id")
            container_count = group.get("container_count", 4)
            running_count = group.get("running_count", 0)

            # Auto download token file if enabled
            if cfg.get("auto_download_tokens", True) and not group.get("squad_downloaded"):
                await self.client.download_squad_file(group_id)

            # Auto-update bio for newly detected squads or periodic 1h schedule
            if cfg.get("auto_change_bio", True):
                bio_interval = cfg.get("bio_interval_seconds", 3600)
                last_bio_time = self.client.last_bio_updates.get(str(group_id), 0)
                now = time.time()
                if last_bio_time == 0 or (now - last_bio_time) >= bio_interval:
                    reason = "Initial launch / newly active" if last_bio_time == 0 else f"Periodic 1-hour schedule (elapsed {int(now - last_bio_time)}s)"
                    self.log(f"Squad {group_id}: Triggering auto-bio update ({reason})...")
                    target_bio = cfg.get("bot_bio", "ｆｆｇｌｏｒｙ．ｘｙｚ")
                    bio_results = await self.client.update_all_bios_for_squad(group_id, target_bio)
                    ok_count = sum(1 for r in bio_results if r.get("result", {}).get("ok"))
                    self.log(f"Squad {group_id}: Bio update complete ({ok_count}/{len(bio_results)} bots updated)")
                    self.stats["total_bio_updates"] += ok_count
                    self.stats["last_bio_update"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Self-healing: Check if container crashed
            if cfg.get("auto_restart_frozen", True) and running_count < container_count:
                self.log(f"Alert: Group {group_id} has {running_count}/{container_count} containers running! Triggering auto-restart...")
                await self.client.restart_group(group_id)
                self.stats["restarts_triggered"] += 1
                continue

            # Query real-time glory farmed (informational telemetry)
            glory_res = await self.client.get_glory(group_id)
            if glory_res.get("ok") and glory_res.get("data"):
                details = glory_res["data"].get("details", [])
                squad_glory = sum(int(d.get("glory", 0)) for d in details)
                self.log(f"Squad {group_id} (Clan {clan_id}): Farmed = {squad_glory} Glory (Running continuously)")
