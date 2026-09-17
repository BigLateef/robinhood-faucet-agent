# Robinhood Chain faucet monitor

A small always-on monitor for [rhfaucet.fun](https://rhfaucet.fun/#claim).
It checks the faucet's public status and tells you when the reservoir can cover
at least one `0.0005 ETH` drop.

## Important limitation

The faucet intentionally requires a human tile puzzle and enforces one claim
per person using network/device/wallet signals. This agent **does not solve or
bypass that check**, rotate identities, or submit claims behind the site's
rules. When the tap is funded, it prints the claim URL so you can complete the
human check yourself. No private key, wallet connection, signature, or
transaction approval is needed for this faucet.

## Run

```bash
cd robinhood-faucet-agent
python3 -m pip install -r requirements.txt
export WALLET_ADDRESS=0xYOUR_40_HEX_CHARACTER_EVM_ADDRESS
export POLL_SECONDS=60
python3 agent.py
```

Use `python3 agent.py --once` for a single check. To verify Discord after
setting `DISCORD_WEBHOOK_URL`, run:

```bash
python3 agent.py --test-discord
```

To preview the exact refill-style message without checking or claiming:

```bash
python3 agent.py --test-funded-discord
```

Each test sends one message and exits; neither checks the faucet nor claims
anything. Logs are JSON lines, and `faucet-state.json` prevents repeated funded
alerts while the tap remains on. The wallet is optional; if omitted, the
monitor only reports faucet funding.

For Render Free, the same simulated alert is available through a protected
endpoint. Set `TEST_ALERT_TOKEN` in Render, then send:

```bash
curl -X POST \
  -H "X-Test-Alert-Token: $TEST_ALERT_TOKEN" \
  https://robinhood-faucet-monitor.onrender.com/test-discord
```

The endpoint is POST-only, requires the secret header, and sends no faucet
request.

## What counts as funded

The agent requires the faucet's public status to report that it is configured,
its RPC is healthy, the live balance is at least the advertised payout, and
there is at least one estimated claim remaining. The faucet's final eligibility
and human check are intentionally left to the normal claim page.

## Render deployment

`render.yaml` and `Dockerfile` are included. In Render:

1. Put this folder in a GitHub repository.
2. Create a new **Blueprint** and select that repository.
3. Deploy the `robinhood-faucet-monitor` web service.
4. Confirm `WALLET_ADDRESS` is the intended public EVM address.
5. Add your Discord webhook as the `DISCORD_WEBHOOK_URL` environment variable.
6. Set a long random `TEST_ALERT_TOKEN` environment variable.
7. Use the service's `/healthz` path for health checks.

When the state changes from dry to funded, the service sends one Discord
message with the balance, payout, claims remaining, and claim page. It resets
its alert state after the faucet becomes dry again. Keep the webhook secret;
it is an alert credential, not a wallet key.

The service exposes `/healthz` so cron-job.org can keep the Render service
awake. Configure a cron-job.org HTTP GET request to your Render service's
`/healthz` path every 5 minutes. Render's Free web services can spin down when
idle, and a stopped or sleeping service cannot observe a newly funded faucet.
See `CRON-JOB-ORG.md` for the exact job settings.

This is also suitable for a small Linux host, Termux/Linux proot, or a
container. Keep the process private and use a normal outbound network
identity. Do not add private keys or attempt to defeat Cloudflare, rate
limits, device checks, or the human puzzle.
