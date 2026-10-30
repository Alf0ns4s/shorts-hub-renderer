"""Public orchestration only. Production data and diagnostics stay private."""
import base64
import datetime
import io
import json
import os
import re
from zoneinfo import ZoneInfo
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

SOURCE = 'https://github.com/Alf0ns4s/shorts-hub.git'
ALLOWED = ('inbox/', 'state/', 'data/', 'art/')
MAX_SECRET_ZIP = 2_000_000


def verify_groq(key):
    """Authenticate without generating text, exposing a key, or consuming model tokens."""
    request = urllib.request.Request('https://api.groq.com/openai/v1/models', headers={
        'Authorization': 'Bearer ' + key, 'User-Agent': 'shorts-hub-renderer'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if not isinstance(data.get('data'), list) or not data['data']:
            raise ValueError('empty model response')
    except Exception:
        print('Groq connection failed; check the encrypted key or provider availability.', flush=True)
        return False
    print('Groq connection verified.', flush=True)
    return True


def git_env(token):
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    config = {
        'credential.helper': '',
        'core.askPass': '',
        'http.https://github.com/.extraheader': 'Authorization: Basic ' +
        base64.b64encode(('x-access-token:' + token).encode()).decode(),
    }
    env['GIT_CONFIG_COUNT'] = str(len(config))
    for i, (key, value) in enumerate(config.items()):
        env[f'GIT_CONFIG_KEY_{i}'] = key
        env[f'GIT_CONFIG_VALUE_{i}'] = value
    return env


def unpack_credentials(encoded, destination):
    """Only expected YouTube credential JSON files; no extractall or symlinks."""
    import re
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_SECRET_ZIP:
        raise ValueError('credential archive exceeds limit')
    files = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.infolist()) > 30:
            raise ValueError('too many credential files')
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not re.fullmatch(r'(client_secret|youtube_[a-z0-9-]+(?:\.info)?)\.json', name):
                raise ValueError('unexpected credential filename')
            if info.file_size > 250_000 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('invalid credential entry')
            if name in files:
                raise ValueError('duplicate credential filename')
            value = archive.read(info)
            json.loads(value)
            files[name] = value
    if not files:
        raise ValueError('empty credential archive')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, value in files.items():
        target = destination / name
        target.write_bytes(value)
        target.chmod(0o600)
    return files.keys()


def run(args, cwd, log, env=None, check=True, timeout=7200):
    result = subprocess.run(args, cwd=cwd, env=env, stdout=log,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if check and result.returncode:
        # Never echo private subprocess output or exception arguments publicly.
        raise RuntimeError('private subprocess failed')
    return result.returncode


def pending_channels(repo):
    return sorted({p.name.split('_', 2)[1] for p in (repo / 'inbox').glob('????-??-??_*.json')})


def save_private(repo, log, env):
    """Persist normal workflow-owned output; never credentials, videos or logs."""
    run(['git', 'config', 'user.name', 'shorts-hub-worker'], repo, log, env)
    run(['git', 'config', 'user.email', 'shorts-hub-worker@users.noreply.github.com'], repo, log, env)
    for folder in ALLOWED:
        if (repo / folder).exists():
            run(['git', 'add', '-A', '--', folder], repo, log, env)
    # Extra protection even if the private .gitignore changes.
    run(['git', 'reset', '-q', '--', 'secrets', 'logs', '*.mp4', '*.wav', '*.zip'], repo, log, env)
    changed = run(['git', 'diff', '--cached', '--quiet'], repo, log, env, check=False)
    if changed == 0:
        return
    if changed != 1:
        raise RuntimeError('cannot inspect private changes')
    run(['git', 'commit', '-q', '-m', 'Production worker: save rendered results'], repo, log, env)
    # Never resolve competing production state by choosing one side automatically.
    run(['git', 'pull', '--rebase', 'origin', 'main'], repo, log, env)
    run(['git', 'push', 'origin', 'HEAD:main'], repo, log, env)


def save_diagnostics(repo, log, failures, stage):
    """Keep useful failure output only in the private production repository."""
    log.flush()
    text = Path(log.name).read_text(encoding='utf-8', errors='replace')[-120000:]
    secret_values = [os.environ.get(k, '') for k in ('SOURCE_TOKEN','YT_SECRETS','GROQ_API_KEY','PAGES_TOKEN')]
    for path in (repo / 'secrets').glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                secret_values.extend(str(v) for k,v in value.items() if k in ('token','refresh_token','client_secret'))
        except (ValueError, OSError):
            pass
    for value in secret_values:
        if value:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(?:gsk_|github_pat_|gh[pousr]_)[A-Za-z0-9_]+', '[REDACTED]', text)
    target = repo / 'state' / 'worker'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'latest.log').write_text(text, encoding='utf-8')
    (target / 'latest.json').write_text(json.dumps({'updated':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                                 'stage':stage,'failures':failures},indent=2),encoding='utf-8')


def main():
    if sys.argv[1:] == ['--check-groq']:
        key = os.environ.get('GROQ_API_KEY', '')
        if not key:
            print('GROQ_API_KEY is not configured.')
            return 2
        return 0 if verify_groq(key) else 2
    token = os.environ.get('SOURCE_TOKEN', '')
    credentials = os.environ.get('YT_SECRETS', '')
    if not token or not credentials:
        print('Configuration incomplete: SOURCE_TOKEN and YT_SECRETS are required.')
        return 2
    if os.environ.get('GROQ_API_KEY') and not verify_groq(os.environ['GROQ_API_KEY']):
        return 2
    failures = []
    with tempfile.TemporaryDirectory(prefix='production-', dir=os.environ.get('RUNNER_TEMP')) as temp:
        root = Path(temp)
        repo = root / 'source'
        env = git_env(token)
        with (root / 'private.log').open('wb') as log:
            run(['git', 'clone', '--depth', '1', '--branch', 'main', SOURCE, str(repo)], root, log, env)
            unpack_credentials(credentials, repo / 'secrets')
            # Do not give the render/writer subprocess the repository access token.
            child = os.environ.copy()
            for name in ('SOURCE_TOKEN', 'YT_SECRETS', 'GITHUB_TOKEN', 'GH_TOKEN',
                         'GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_PATH'):
                child.pop(name, None)
            child['SHORTS_OUTPUT_DIR'] = 'state'
            child['PYTHONDONTWRITEBYTECODE'] = '1'
            stage = 'dependencies'
            try:
                print('Preparing production dependencies.', flush=True)
                run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'], repo, log, child)
                stage = 'source checks'
                run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_*.py', '-q'], repo, log, child)
                stage = 'writing'
                if child.get('GROQ_API_KEY'):
                    print('Writing and validating missing scripts.', flush=True)
                    if run([sys.executable, 'tools/local_writer.py', '--backend', 'groq'], repo, log, child, check=False):
                        failures.append('writing')
                # Save valid scripts and writer diagnostics before a long render.
                save_diagnostics(repo, log, failures, stage)
                save_private(repo, log, env)
                # Refuse to render a pending channel without its existing upload login.
                for channel in pending_channels(repo):
                    if not (repo / 'secrets' / f'youtube_{channel}.json').is_file():
                        raise RuntimeError('missing channel upload credentials')
                stage = 'rendering'
                if pending_channels(repo):
                    print('Rendering and testing approved scripts.', flush=True)
                    run([sys.executable, 'hub.py', 'setup'], repo, log, child)
                    today = datetime.datetime.now(ZoneInfo('Europe/Vilnius')).date()
                    drawings = []
                    for day in (today, today - datetime.timedelta(days=1)):
                        drawings += sorted((repo / 'art/src/daily' / day.isoformat()).glob('draw_*.py'))
                    if drawings:
                        run([sys.executable, 'tools/render_art.py', *[str(p.relative_to(repo)) for p in drawings]], repo, log, child)
                    if run([sys.executable, 'hub.py', 'cloud'], repo, log, child, check=False):
                        failures.append('video processing')
                # Refresh the existing dashboard with a dedicated environment. The writer
                # and renderer never receive the repository credential.
                stage = 'dashboard'
                dashboard = child.copy()
                dashboard.pop('GROQ_API_KEY', None)
                dashboard['PAGES_TOKEN'] = os.environ.get('PAGES_TOKEN') or token
                dashboard['GH_TOKEN'] = token
                if run(['bash', 'tools/publish_dashboard.sh'], repo, log, dashboard, check=False):
                    failures.append('dashboard refresh')
            except Exception as error:
                failures.append(stage + ':' + type(error).__name__)
            finally:
                save_diagnostics(repo, log, failures, stage)
                print('Saving production results privately.', flush=True)
                save_private(repo, log, env)
    if failures:
        print('Production incomplete. Review private inbox/state; no content was printed here.')
        return 1
    print('Production pass completed. Check the private dashboard for upload and QA results.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('Worker stopped. Private content and credentials are omitted from this log.')
        raise SystemExit(1)
