#!/usr/bin/env python3
#Glorion_Outsingoula_Oumona
"""Weurkico CI client - stdlib only (no third-party deps).

Triggers a verification through API v1, polls until terminal, maps the
verdict to a CI outcome. A technical error is NEVER a pass.

Exit codes: 0 = PASS (or waived per fail-on policy), 1 = FAIL, 2 = ERROR.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API = 'https://www.weurkico.com'
TERMINAL = {'completed', 'failed', 'cancelled', 'awaiting_review'}

# verdict -> CI outcome. 'needs_review'/manual = automation could not
# settle every control: not a pass by default (configurable via fail-on).
VERDICT_OUTCOME = {
    'ready_to_ship': 'pass',
    'ready_with_conditions': 'conditions',
    'not_ready': 'fail',
}


class CIError(Exception):
    """Technical failure - exit 2, never a pass."""


def _req(base: str, key: str, method: str, path: str, payload=None) -> dict:
    url = base.rstrip('/') + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {key}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'weurkico-ci/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode() or '{}')
            msg = (detail.get('error') or {}).get('message') or e.reason
        except Exception:
            msg = e.reason
        raise CIError(f'HTTP {e.code} {path}: {msg}') from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise CIError(f'network error {path}: {e}') from e


def ensure_project(base, key, name, repo_url, branch, repo_token) -> str:
    """Create the application if it does not exist, reuse by public_id."""
    try:
        for p in _req(base, key, 'GET', '/api/v1/projects').get('projects', []):
            if p.get('name') == name or repo_url and p.get('repo_url') == repo_url:
                return p['id']
    except CIError:
        raise
    payload = {'name': name, 'repo_url': repo_url}
    if branch:
        payload['branch'] = branch
    if repo_token:
        payload['repo_token'] = repo_token
        payload['repo_username'] = 'x-access-token'
        payload['repo_provider'] = 'github'
    return _req(base, key, 'POST', '/api/v1/projects', payload)['id']


def start_run(base, key, project_id, scope) -> str:
    out = _req(base, key, 'POST', '/api/v1/verifications',
               {'project_id': project_id, 'scope': scope})
    return out['id']  # WK-<n>-<hash>


def poll_run(base, key, run_id, timeout_s, interval) -> dict:
    deadline = time.monotonic() + timeout_s
    wait = interval
    while True:
        data = _req(base, key, 'GET', f'/api/v1/verifications/{run_id}')
        if data.get('status') in TERMINAL:
            return data
        if time.monotonic() >= deadline:
            raise CIError(f'timeout after {timeout_s}s - run still '
                          f"{data.get('status')}")
        time.sleep(wait)
        wait = min(wait * 1.5, 30)   # bounded exponential backoff


def classify(data: dict, fail_on: str) -> tuple[str, str]:
    """(outcome, reason) - pass|fail|error, never guess."""
    status = data.get('status')
    if status in ('failed', 'cancelled'):
        return 'error', f"run {status}: {data.get('error_message') or 'no detail'}"
    if status == 'awaiting_review':
        return ('fail' if fail_on == 'strict' else 'conditions',
                'verdict requires human review')
    verdict = data.get('verdict')
    out = VERDICT_OUTCOME.get(verdict)
    if out is None:
        return 'error', f'unexpected verdict {verdict!r}'
    if out == 'conditions' and fail_on in ('conditions', 'strict'):
        return 'fail', 'ready with conditions (policy blocks)'
    return out, verdict


SARIF_LEVEL = {'critical': 'error', 'high': 'error',
               'medium': 'warning', 'low': 'note', 'info': 'note'}


def write_sarif(base, key, run_id, path) -> int:
    """Export findings as SARIF 2.1.0 - uploadable to GitHub code scanning
    via actions/upload-sarif. Returns the finding count."""
    data = _req(base, key, 'GET',
                f'/api/v1/verifications/{run_id}/findings')
    findings = data.get('findings', [])
    rules, seen, results = {}, set(), []
    for f in findings:
        rid = f.get('control_key') or f.get('fingerprint') or 'weurkico'
        if rid not in seen:
            seen.add(rid)
            rules[rid] = {'id': rid,
                          'name': (f.get('title') or rid)[:120],
                          'properties': {'category': f.get('category')}}
        res = {'ruleId': rid,
               'level': SARIF_LEVEL.get(f.get('severity'), 'warning'),
               'message': {'text': f.get('title') or rid}}
        if f.get('file_path'):
            res['locations'] = [{'physicalLocation': {
                'artifactLocation': {
                    'uri': f['file_path'].replace('\\', '/')},
                'region': {'startLine': f.get('line_start') or 1}}}]
        results.append(res)
    doc = {'version': '2.1.0',
           '$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
           'runs': [{'tool': {'driver': {
               'name': 'Weurkico', 'version': '1.0',
               'informationUri': 'https://www.weurkico.com',
               'rules': list(rules.values())}},
               'results': results}]}
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1)
    return len(results)


def emit_github(summary: str, outputs: dict):
    if p := os.environ.get('GITHUB_OUTPUT'):
        with open(p, 'a', encoding='utf-8') as f:
            for k, v in outputs.items():
                f.write(f'{k}={v}\n')
    if p := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(p, 'a', encoding='utf-8') as f:
            f.write(summary + '\n')
    print(summary)


def main() -> int:
    base = os.environ.get('WEURKICO_API_URL', DEFAULT_API)
    key = os.environ.get('WEURKICO_API_KEY', '').strip()
    if not key:
        print('::error::WEURKICO_API_KEY is not set')
        return 2
    name = os.environ.get('INPUT_PROJECT_NAME') or \
        os.environ.get('GITHUB_REPOSITORY', 'app').rsplit('/', 1)[-1]
    repo_url = os.environ.get('INPUT_REPO_URL') or \
        f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
    branch = os.environ.get('INPUT_BRANCH') or \
        os.environ.get('GITHUB_REF_NAME', '')
    scope = os.environ.get('INPUT_SCOPE', 'production')
    fail_on = os.environ.get('INPUT_FAIL_ON', 'not-ready')
    timeout_s = int(os.environ.get('INPUT_TIMEOUT', '900'))
    interval = float(os.environ.get('INPUT_POLL_INTERVAL', '10'))
    repo_token = os.environ.get('INPUT_REPO_TOKEN', '')
    project_id = os.environ.get('INPUT_PROJECT_ID', '')
    sha = os.environ.get('GITHUB_SHA', '')[:12]

    try:
        if not project_id:
            project_id = ensure_project(base, key, name, repo_url,
                                        branch, repo_token)
        run_id = start_run(base, key, project_id, scope)
        print(f'::notice::Weurkico run {run_id} queued for {repo_url}@{sha}')
        data = poll_run(base, key, run_id, timeout_s, interval)
        if os.environ.get('INPUT_SARIF', '') == 'true':
            n = write_sarif(base, key, run_id, 'weurkico.sarif')
            print(f'::notice::weurkico.sarif written - {n} findings')
    except CIError as e:
        emit_github(f'### Weurkico CI - ERROR\n{e}', {'verdict': 'error'})
        return 2

    outcome, reason = classify(data, fail_on)
    verdict = data.get('verdict', 'unknown')
    verify_url = base.rstrip('/') + (data.get('verify_url') or '')
    summary = (f'### Weurkico - {verdict.replace("_", " ").upper()}\n\n'
               f'- Run: `{run_id}` ({data.get("status")})\n'
               f'- Scope: `{data.get("scope")}` - commit `{sha}`\n'
               f'- Reason: {reason}\n'
               f'- Report: {verify_url}\n')
    emit_github(summary, {'verdict': verdict, 'verification_id': run_id,
                          'verify_url': verify_url})
    return 0 if outcome == 'pass' else 1 if outcome == 'fail' else 2


if __name__ == '__main__':
    sys.exit(main())
