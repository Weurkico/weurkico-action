# Weurkico Verify - GitHub Action

Gate your pipeline on a Weurkico production-readiness verdict.

```yaml
- uses: Weurkico/weurkico-action@v1
  with:
    api-key: ${{ secrets.WEURKICO_API_KEY }}
    scope: production
    fail-on: not-ready
```

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `api-key` | — | **Required.** `wk_live_...` key stored as a repo secret |
| `api-url` | `https://www.weurkico.com` | API base (staging/self-host) |
| `project-id` | `''` | Reuse an existing application - skips auto-creation |
| `project-name` | repo name | Name for the auto-created application |
| `repo-url` | workflow repo | Repository to verify |
| `repo-token` | `''` | PAT for **private** repos (stored encrypted server-side) |
| `branch` | current ref | Branch to clone |
| `scope` | `production` | `core` · `production` · `ai_data` · `quality` |
| `fail-on` | `not-ready` | `not-ready` · `conditions` · `strict` · `never` |
| `timeout` | `900` | Max wait (s) before a technical **ERROR** |
| `poll-interval` | `10` | Initial poll delay, backs off to 30 s |

## Outputs

`verification_id`, `verdict`, `verify_url` - the public proof URL.

## Semantics - never silently pass

| Backend state | CI result |
|---|---|
| `ready_to_ship` | PASS |
| `ready_with_conditions` | PASS, or FAIL under `fail-on: conditions/strict` |
| `not_ready` | FAIL |
| `awaiting_review` | `conditions` under default, FAIL under `strict` |
| `failed`/`cancelled`/timeout/API error | **ERROR (exit 2)** - never a pass |

## Secrets

- `WEURKICO_API_KEY` - create under **Settings → Secrets → Actions**.
- The key is sent only in the `Authorization` header, never printed.
- `repo-token` is needed only for private repositories; it is stored
  encrypted (Fernet) in your Weurkico account, never in logs.

## Notes

- Duplicate submissions are safe: the API returns `409` if a run is
  already in progress on the application.
- The run binds to the commit cloned at queue time (`commit_sha` in the
  report); the workflow SHA is echoed in the step summary.

## GitLab CI

GitLab has no action marketplace - copy [`gitlab-ci.yml`](gitlab-ci.yml)
from this repo into your project's `.gitlab-ci.yml`. It downloads
`weurkico_ci.py` and runs the same verification gate in a job. Set
`WEURKICO_API_KEY` as a masked, protected CI/CD variable.
