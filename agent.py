#!/usr/bin/env python3
"""Robinhood Chain faucet monitor with a human-in-the-loop claim flow.

This agent watches the faucet's public status endpoint and reports when the
reservoir can cover a drop. It never handles private keys, bypasses the site's
human tile check, or tries to evade the faucet's one-claim-per-person rules.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

FAUCET = os.getenv("FAUCET_URL", "https://rhfaucet.fun").rstrip("/")
STATUS_ID = "56d84fbd4fb79fe610dfcc3bb503140a8d91839e043aa196bcc467de2e343069"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TEST_ALERT_TOKEN = os.getenv("TEST_ALERT_TOKEN", "").strip()
STATE_FILE = Path(os.getenv("STATE_FILE", "faucet-state.json"))

HEADERS = {
    "user-agent": "robinhood-faucet-agent/1.0 (human-in-the-loop)",
    "origin": FAUCET,
    "referer": FAUCET + "/",
    "x-tsr-serverFn": "true",
    "accept": "application/json, application/x-ndjson",
}

LATEST_HEALTH: dict[str, Any] = {
    "ok": False,
    "service": "robinhood-faucet-monitor",
    "last_check": None,
    "funded": False,
}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(LATEST_HEALTH).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/test-discord":
            self.send_response(404)
            self.end_headers()
            return
        supplied = self.headers.get("X-Test-Alert-Token", "")
        if not TEST_ALERT_TOKEN or not secrets.compare_digest(supplied, TEST_ALERT_TOKEN):
            self.send_response(401)
            self.end_headers()
            return
        try:
            result = send_test_discord(simulate_funded=True)
            if result == 0:
                body = b'{"ok":true,"message":"test Discord alert sent"}'
                self.send_response(200)
            else:
                body = b'{"ok":false,"message":"Discord webhook is not configured"}'
                self.send_response(503)
        except Exception:
            body = b'{"ok":false,"message":"test alert failed"}'
            self.send_response(502)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


def start_health_server() -> None:
    port = int(os.getenv("PORT", "0"))
    if port <= 0:
        return
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health endpoint listening on :{port}/healthz", flush=True)


@dataclass
class FaucetStatus:
    configured: bool | None
    payout_wei: int | None
    balance_wei: int | None
    claims_remaining: int | None
    rpc_ok: bool | None
    raw: str

    @property
    def funded(self) -> bool:
        return (
            self.configured is True
            and self.rpc_ok is True
            and self.payout_wei is not None
            and self.balance_wei is not None
            and self.balance_wei >= self.payout_wei
            and (self.claims_remaining is None or self.claims_remaining > 0)
        )


def _get_scalar(raw: str, key: str) -> str | None:
    # The faucet uses TanStack Start's serialized response format. These
    # fields are public and are also rendered on the faucet page.
    m = re.search(rf'"{re.escape(key)}":\{{"t":(?:0|1),"s":(?:"([^"]*)"|([^}}]+))\}}', raw)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2).rstrip(",")


def _get_bool(raw: str, key: str) -> bool | None:
    m = re.search(rf'"{re.escape(key)}":\{{"t":2,"s":([12])\}}', raw)
    if not m:
        return None
    return m.group(1) == "2"


def _int_field(raw: str, key: str) -> int | None:
    value = _get_scalar(raw, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _serialized_fields(raw: str) -> dict[str, Any]:
    """Decode the small public object returned by the faucet status function."""
    try:
        root = json.loads(raw)
        result = root["p"]["v"][0]
        payload = result["p"]
        keys, values = payload["k"], payload["v"]
        out: dict[str, Any] = {}
        for key, value in zip(keys, values):
            if not isinstance(value, dict):
                out[key] = value
            elif value.get("t") == 0:
                out[key] = int(value.get("s"))
            elif value.get("t") == 1:
                out[key] = value.get("s")
            elif value.get("t") == 2:
                out[key] = value.get("s") == 2
            else:
                out[key] = None
        return out
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def parse_status(raw: str) -> FaucetStatus:
    fields = _serialized_fields(raw)
    return FaucetStatus(
        configured=fields.get("configured"),
        payout_wei=_as_int(fields.get("payoutWei")),
        balance_wei=_as_int(fields.get("balanceWei")),
        claims_remaining=_as_int(fields.get("claimsRemaining")),
        rpc_ok=fields.get("rpcOk"),
        raw=raw,
    )


def eth(wei: int | None) -> str:
    return "—" if wei is None else f"{wei / 10**18:.6f} ETH"


def get_status(session: requests.Session) -> FaucetStatus:
    # A first page request obtains the same Cloudflare cookie a normal browser
    # receives. We do not attempt to defeat Cloudflare or any human check.
    session.get(FAUCET + "/", timeout=25, headers={"user-agent": HEADERS["user-agent"]})
    response = session.get(FAUCET + "/_serverFn/" + STATUS_ID, timeout=25, headers=HEADERS)
    response.raise_for_status()
    return parse_status(response.text)


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(data: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        # A read-only host should still be able to monitor; it will just emit
        # the funded action again on the next poll.
        pass


def send_discord(message: str) -> bool:
    """Send a message to the user-supplied Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not configured.", file=sys.stderr, flush=True)
        return False
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": message, "allowed_mentions": {"parse": []}},
        timeout=20,
    )
    response.raise_for_status()
    return True


def notify_discord(status: FaucetStatus) -> bool:
    """Send one refill alert to a user-supplied Discord webhook."""
    message = (
        "🚰 **Robinhood Chain faucet refilled**\n"
        f"Balance: **{eth(status.balance_wei)}** | Payout: **{eth(status.payout_wei)}**\n"
        f"Claims remaining: **{status.claims_remaining}**\n"
        f"[🔗 Open faucet and claim]({FAUCET}/#claim)\n"
        "Complete the faucet's human tile check manually."
    )
    if send_discord(message):
        print("Discord refill alert sent.", flush=True)
        return True
    return False


def send_test_discord(simulate_funded: bool = False) -> int:
    if simulate_funded:
        message = (
            "🧪 **Simulated Robinhood Chain refill alert**\n"
            "This is a test only — no faucet request was submitted.\n"
            "Balance: **0.000500 ETH** | Payout: **0.000500 ETH**\n"
            "Claims remaining: **1**\n"
            f"[🔗 Open faucet and claim]({FAUCET}/#claim)"
        )
    else:
        message = (
            "✅ **Robinhood faucet monitor test alert**\n"
            "Discord notifications are connected.\n"
            f"[🔗 Open faucet and claim]({FAUCET}/#claim)"
        )
    if not send_discord(message):
        return 2
    print("Discord test alert sent.", flush=True)
    return 0


def report(status: FaucetStatus, eligibility: dict[str, Any] | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    line = {
        "time": now,
        "faucet_balance": eth(status.balance_wei),
        "payout": eth(status.payout_wei),
        "claims_remaining": status.claims_remaining,
        "funded": status.funded,
    }
    LATEST_HEALTH.update({"ok": True, "last_check": now, **line})
    if eligibility is not None:
        line["wallet_eligible"] = eligibility["eligible"]
    print(json.dumps(line), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor rhfaucet.fun without bypassing its human check")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--test-discord", action="store_true", help="send one test message and exit")
    parser.add_argument(
        "--test-funded-discord",
        action="store_true",
        help="send a simulated funded/refill alert and exit",
    )
    args = parser.parse_args()

    if args.test_discord or args.test_funded_discord:
        return send_test_discord(simulate_funded=args.test_funded_discord)

    if WALLET_ADDRESS and not re.fullmatch(r"0x[0-9a-fA-F]{40}", WALLET_ADDRESS):
        print("WALLET_ADDRESS must be a 0x-prefixed 40-hex-character EVM address", file=sys.stderr)
        return 2

    start_health_server()
    session = requests.Session()
    state = load_state()
    print(f"Watching {FAUCET}; poll interval={POLL_SECONDS}s; wallet={'configured' if WALLET_ADDRESS else 'not configured'}", flush=True)

    while True:
        try:
            status = get_status(session)
            # Eligibility is deliberately left to the faucet page and the
            # user. The site may require a human puzzle and server-side checks.
            report(status, None)

            if status.funded and not state.get("funded_alerted"):
                print(
                    "ACTION_REQUIRED: faucet is funded. Open " + FAUCET + "/#claim, paste WALLET_ADDRESS, solve the site's tile check, and submit.",
                    flush=True,
                )
                sent = notify_discord(status) if DISCORD_WEBHOOK_URL else False
                # Without a webhook, log the event once. With a webhook, keep
                # retrying after a delivery failure until one succeeds.
                if sent or not DISCORD_WEBHOOK_URL:
                    state["funded_alerted"] = True
                    save_state(state)
            elif not status.funded:
                state["funded_alerted"] = False
                save_state(state)

            if args.once:
                return 0
            time.sleep(max(10, POLL_SECONDS))
        except KeyboardInterrupt:
            print("Stopped.", flush=True)
            return 0
        except Exception as exc:
            LATEST_HEALTH.update({"ok": False, "last_error": str(exc)})
            print(f"check failed: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(max(30, POLL_SECONDS))


if __name__ == "__main__":
    raise SystemExit(main())
