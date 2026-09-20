# Nintendo Zelda Edition Stock Monitor

This project checks Nintendo's French-Belgian product page for SKU `P00211`. On meaningful transitions, it creates a GitHub Issue assigned to the repository owner. GitHub Mobile can deliver that assignment as an iPhone push notification.

The monitor does not bypass Nintendo's Queue-it waiting room. A queue redirect produces a separate "check manually" notification and is never reported as confirmed stock.

## Signals

| Observation | Result | Notification |
| --- | --- | --- |
| JSON-LD `InStock`, `PreOrder`, or an enabled purchase control | `AVAILABLE` | Assigned GitHub Issue, once per restock |
| JSON-LD `OutOfStock`, disabled purchase control, sold-out text, `404`, or `410` | `UNAVAILABLE` | No alert; two consecutive observations rearm the monitor |
| New Queue-it waiting room or changed queue event | `QUEUE` | Assigned GitHub Issue, once per queue event |
| Ambiguous page, timeout, rate limit, server error, or unrelated redirect | `UNKNOWN` | No alert and no confirmed-state change |

A plain HTTP `200` response is not enough to claim availability.

## Local Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Perform a non-mutating product check without any credentials:

```bash
python -m nintendo_stock_monitor check --dry-run --state-file /tmp/nintendo-state.json
```

Issue notifications are intended to run in GitHub Actions, where GitHub supplies a temporary token automatically. To create a test Issue locally, authenticate GitHub CLI and export the repository context:

```bash
export GITHUB_TOKEN="$(gh auth token)"
export GITHUB_REPOSITORY='BerkantKs/Track-website-change---notification'
export GITHUB_ASSIGNEE='BerkantKs'
python -m nintendo_stock_monitor test-notification
```

## Enable GitHub Mobile Push

1. Install GitHub Mobile on the iPhone and sign in as the repository owner.
2. In GitHub Mobile, open **Profile > Settings > Notifications** and enable push notifications for **Assignments**.
3. In iOS **Settings > Apps > GitHub > Notifications**, enable Lock Screen, Notification Centre, Banners, and Sounds.
4. Ensure GitHub is not silenced by Focus or Scheduled Summary while testing.

On Mac, GitHub notifications appear in the web notification inbox and can also be delivered by email according to your GitHub notification settings.

## GitHub Deployment

1. Create a **public** GitHub repository and push this project. A private repository running every five minutes can exceed the included GitHub Actions minutes.
2. No repository secrets are required. Delete any old `NTFY_TOPIC`, `NTFY_TOKEN`, or `NTFY_SERVER_URL` entries.
3. In **Settings > Actions > General > Workflow permissions**, choose **Read and write permissions**.
4. Open **Actions > Nintendo stock monitor > Run workflow** and choose `test-notification`.
5. Confirm GitHub creates a test Issue assigned to the repository owner and that GitHub Mobile shows the assignment notification. Close the test Issue afterward.
6. Run `check`. Later scheduled checks use the same transition and deduplication rules.

The workflow uses GitHub's short-lived built-in token with only `contents: write` and `issues: write`. It does not need a personal access token or notification secret.

The schedule is declared as `*/5 * * * *`, but GitHub scheduled jobs are best effort and can be delayed during busy periods. GitHub may disable scheduled workflows in inactive public repositories. The state file writes one monthly heartbeat so there is normally a small repository update even when availability does not change.

The job has a four-minute ceiling for installation, bounded redirects, one retry, and network jitter. Each individual Nintendo request times out after ten seconds by default.

## Persistent State

`.monitor/state.json` contains only public status metadata. It tracks:

- the last confirmed availability state;
- the consecutive unavailable count;
- the active flag and last seen Queue-it event identity;
- the last successful alert type and time;
- the monthly heartbeat.

The workflow commits this file only when those values change. Alert state is saved after GitHub accepts the new Issue. If Issue creation fails, the workflow fails and the next run retries the alert.

Delivery is intentionally at least once. A runner failure after GitHub creates an Issue but before state is pushed can produce one duplicate Issue; this is preferable to silently missing a restock.

On the first check, confirmed availability alerts immediately. Initial unavailability establishes a silent baseline, while an initial waiting room sends one normal-priority manual-check warning.

## Development

```bash
python -m ruff check .
python -m pytest
```

CLI exit codes are `0` for success, `2` for invalid configuration, `3` for state errors, `4` for notification failures, and `5` for an unexpected error.

Product metadata and request settings live in `config.toml`. GitHub Actions supplies its notification credentials automatically.

## Limitations

- Queue-it hides the underlying product page, so actual stock cannot be confirmed while the waiting room is active.
- The project does not solve CAPTCHAs, reuse browser sessions, automate checkout, or bypass traffic controls.
- Nintendo can change its page markup. Fixture tests protect the currently supported JSON-LD and purchase-control signals, but a markup change may produce `UNKNOWN` until the parser is updated.
- Monitoring covers only the French-Belgian product URL configured in `config.toml`.
- Notification Issues and their status details are public because the repository is public.