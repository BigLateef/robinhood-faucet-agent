# cron-job.org keep-alive

Use cron-job.org only to keep the Render web service awake. The monitor itself
continues polling the faucet every 60 seconds inside the Render service.

## Job settings

- **Method:** GET
- **Target:** your Render service URL with `/healthz` appended
- **Schedule:** every 5 minutes
- **Timeout:** 30 seconds
- **Request body:** none
- **Notifications:** enable failure notifications

Do not point the job at the faucet claim route. The keep-alive only checks the
agent's health endpoint; it never submits a claim, solves the human puzzle, or
handles a private key.

The endpoint returns the latest monitor state, including the last check time
and whether the faucet is currently considered funded. Discord is notified by
the Render process itself when the state changes from dry to funded; the
cron-job.org request only keeps the service awake.
