# Nintendo Zelda Edition Stock Monitor

This project checks Nintendo's French-Belgian product page for SKU `P00211` and sends transition-only notifications through [ntfy](https://ntfy.sh/). It is designed to run every five minutes in a public GitHub repository.

The monitor does not bypass Nintendo's Queue-it waiting room. A queue redirect produces a separate "check manually" notification and is never reported as confirmed stock.

## Signals

| Observation | Result | Notification |
| --- | --- | --- |
| JSON-LD `InStock`, `PreOrder`, or an enabled purchase control | `AVAILABLE` | High priority, once per restock |
| JSON-LD `OutOfStock`, disabled purchase control, sold-out text, `404`, or `410` | `UNAVAILABLE` | No alert; two consecutive observations rearm the monitor |
| New Queue-it waiting room or changed queue event | `QUEUE` | Normal priority, once per queue event |
| Ambiguous page, timeout, rate limit, server error, or unrelated redirect | `UNKNOWN` | No alert and no confirmed-state change |

A plain HTTP `200` response is not enough to claim availability.

## Local Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Choose a long random ntfy topic. Treat it like a password because an unreserved `ntfy.sh` topic can be read or written by anyone who guesses its name.

An unreserved topic is not suitable for sensitive messages: someone who discovers it can read alerts or send fake ones. For stronger protection, reserve the topic and use `NTFY_TOKEN`, or use an authenticated self-hosted ntfy server.

```bash
export NTFY_TOPIC='your-long-random-topic'
export NTFY_TOKEN='' # Optional for a reserved/protected topic or self-hosted ntfy
export NTFY_SERVER_URL='https://ntfy.sh'
```

Test delivery, then perform a non-mutating product check:

```bash
python -m nintendo_stock_monitor test-notification
python -m nintendo_stock_monitor check --dry-run --state-file /tmp/nintendo-state.json
```

Run a real local check after setting `NTFY_TOPIC` with:

```bash
python -m nintendo_stock_monitor check
```

## Subscribe on Apple Devices

### iPhone

1. Install the ntfy app from the App Store.
2. Subscribe to the exact value used for `NTFY_TOPIC` on the configured server.
3. Allow notifications and send `test-notification` before relying on the monitor.

### Mac

1. Open `https://ntfy.sh/YOUR_TOPIC` in a browser, or use the matching URL for a self-hosted server.
2. Allow browser notifications and keep the subscription available as a pinned tab or installed web app.
3. Confirm the same test message arrives on both devices.

For a protected topic, sign in with the account that can read it. `NTFY_TOKEN` is sent only by the monitor and is never written to logs or state.

## GitHub Deployment

1. Create a **public** GitHub repository and push this project. A private repository running every five minutes can exceed the included GitHub Actions minutes.
2. In **Settings > Secrets and variables > Actions**, add `NTFY_TOPIC` as a repository secret.
3. Add `NTFY_TOKEN` as a repository secret when the topic requires authentication.
4. For a self-hosted server, add `NTFY_SERVER_URL` as a repository variable. Omit it to use `https://ntfy.sh`.
5. In **Settings > Actions > General > Workflow permissions**, choose **Read and write permissions** so the workflow can persist `.monitor/state.json`.
6. Open **Actions > Nintendo stock monitor > Run workflow** and choose `test-notification`.
7. Run `check` twice. The first run may send one Queue-it warning; the second should not duplicate it when the queue event is unchanged.

The schedule is declared as `*/5 * * * *`, but GitHub scheduled jobs are best effort and can be delayed during busy periods. GitHub may disable scheduled workflows in inactive public repositories. The state file writes one monthly heartbeat so there is normally a small repository update even when availability does not change.

The job has a four-minute ceiling for installation, bounded redirects, one retry, and network jitter. Each individual Nintendo request times out after ten seconds by default.

## Persistent State

`.monitor/state.json` contains only public status metadata. It tracks:

- the last confirmed availability state;
- the consecutive unavailable count;
- the active flag and last seen Queue-it event identity;
- the last successful alert type and time;
- the monthly heartbeat.

The workflow commits this file only when those values change. Alert state is saved after ntfy accepts a message. If ntfy fails, the workflow fails and the next run retries the alert.

Delivery is intentionally at least once. A runner failure after ntfy accepts a message but before Git pushes state can produce one duplicate notification; this is preferable to silently missing a restock.

On the first check, confirmed availability alerts immediately. Initial unavailability establishes a silent baseline, while an initial waiting room sends one normal-priority manual-check warning.

## Development

```bash
python -m ruff check .
python -m pytest
```

CLI exit codes are `0` for success, `2` for invalid configuration, `3` for state errors, `4` for notification failures, and `5` for an unexpected error.

Product metadata and request settings live in `config.toml`. Notification credentials belong only in local environment variables or GitHub Actions secrets.

## Limitations

- Queue-it hides the underlying product page, so actual stock cannot be confirmed while the waiting room is active.
- The project does not solve CAPTCHAs, reuse browser sessions, automate checkout, or bypass traffic controls.
- Nintendo can change its page markup. Fixture tests protect the currently supported JSON-LD and purchase-control signals, but a markup change may produce `UNKNOWN` until the parser is updated.
- Monitoring covers only the French-Belgian product URL configured in `config.toml`.