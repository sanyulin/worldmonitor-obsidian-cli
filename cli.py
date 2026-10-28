"""Headless World Monitor -> DeepSeek -> Obsidian. Default action: background start."""
import argparse
import base64
import contextlib
import ctypes
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import engine

HOME = engine.ROOT
TASK = 'WorldMonitorObsidianCLI'
SCRIPT = Path(__file__).resolve()
KEYS = ('WORLDMONITOR_API_KEY', 'DEEPSEEK_API_KEY')


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def config():
    if not (HOME / 'config.json').is_file():
        raise RuntimeError('Run python cli.py init --vault PATH before starting')
    cfg = engine.settings(HOME)
    dt.datetime.strptime(cfg.get('hour', '08:00'), '%H:%M')
    if not (Path(cfg['vault']) / '.obsidian').is_dir():
        raise RuntimeError('The configured directory must be an existing Obsidian vault')
    return cfg


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('DPAPI requires Windows')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Windows DPAPI operation failed; credentials belong to a specific Windows user')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(output.data, ctypes.c_void_p))


def protect(value):
    return base64.b64encode(dpapi(value.encode('utf-8'))).decode('ascii')


def unprotect(value):
    return dpapi(base64.b64decode(value, validate=True), decrypt=True).decode('utf-8')


def credentials(keys=KEYS):
    saved = {}
    if os.name == 'nt' and (HOME / 'credentials.dpapi.json').is_file():
        saved = json.loads((HOME / 'credentials.dpapi.json').read_text(encoding='utf-8'))
    result = {}
    for key in keys:
        value = os.environ.get(key, '').strip()
        if not value and saved.get(key):
            value = unprotect(saved[key])
        if not value:
            raise RuntimeError(key + ' is missing; use python cli.py secrets or a process environment variable')
        result[key] = value
    return result


@contextlib.contextmanager
def key_environment(keys=KEYS):
    old = {k: os.environ.get(k) for k in KEYS}
    try:
        os.environ.update(credentials(keys))
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def init(args):
    HOME.mkdir(parents=True, exist_ok=True)
    path = HOME / 'config.json'
    if path.exists():
        raise RuntimeError('Configuration already exists; edit your local config instead of overwriting it')
    vault = Path(args.vault).expanduser().resolve()
    if not (vault / '.obsidian').is_dir():
        raise RuntimeError('--vault must point to an existing Obsidian vault')
    dt.datetime.strptime(args.hour, '%H:%M')
    cfg = dict(vault=str(vault), folder='World Monitor日报', base_url=args.base_url,
               model='deepseek-flash', variants=['full', 'tech', 'finance'], hour=args.hour,
               timezone='Asia/Shanghai', max_articles=60, per_publisher=5, minimum_articles=6,
               worldmonitor_dir=str(Path(args.worldmonitor_dir).expanduser().resolve()) if args.worldmonitor_dir else None)
    engine.save_json(path, cfg)
    try:
        config()
    except Exception:
        # Retain invalid configuration for inspection; no implicit deletion.
        raise RuntimeError('Configuration saved but validation failed; check your endpoint and vault') from None
    emit(dict(result='configured', next='python cli.py secrets, then python cli.py'))


def service(cfg):
    project = cfg.get('worldmonitor_dir')
    if not project:
        return
    project = Path(project)
    if not project.is_dir() or not any((project / n).is_file() for n in ('compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml')):
        raise RuntimeError('worldmonitor_dir must contain an already configured Compose deployment')
    docker = shutil.which('docker')
    if not docker:
        raise RuntimeError('Docker CLI is unavailable; start World Monitor separately or fix worldmonitor_dir')
    try:
        result = subprocess.run([docker, 'compose', 'up', '-d', '--no-build'], cwd=project,
                                capture_output=True, timeout=180,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError('World Monitor service startup exceeded 180 seconds') from None
    if result.returncode:
        raise RuntimeError('World Monitor Compose startup failed; inspect that deployment locally')


def external_failure(cfg, exc, scheduled):
    now = dt.datetime.now(engine.TZ)
    run_id = now.strftime('%Y-%m-%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    status = dict(run_id=run_id, started_at=now.isoformat(), finished_at=now.isoformat(), elapsed_seconds=0,
                  edition='', scheduled=scheduled, result='failed', stage='service-or-credentials', error=str(exc))
    engine.save_json(HOME / 'runs' / run_id / 'manifest.json', status)
    engine.save_json(HOME / 'latest-run.json', status)
    engine.dashboard(HOME, cfg, status, now)


def run(cfg, probe=False, scheduled=False):
    HOME.mkdir(parents=True, exist_ok=True)
    with engine.run_lock(HOME):
        # Complete notes remain usable even if a proxy, deployment or key is currently unavailable.
        today = Path(cfg['vault']) / cfg['folder'] / (dt.datetime.now(engine.TZ).date().isoformat() + '.md')
        if not probe and today.exists():
            return engine.execute(cfg, HOME, scheduled=scheduled)
        entered_engine = False
        try:
            service(cfg)
            with key_environment(('WORLDMONITOR_API_KEY',) if probe else KEYS):
                entered_engine = True
                return engine.execute(cfg, HOME, probe=probe, scheduled=scheduled)
        except Exception as exc:
            # execute() records its own stage; startup errors need a separate record.
            if not entered_engine:
                external_failure(cfg, exc, scheduled)
            raise


@contextlib.contextmanager
def daemon_lock():
    lock_home = HOME / 'daemon'
    lock_home.mkdir(parents=True, exist_ok=True)
    with engine.run_lock(lock_home):
        yield


def running():
    try:
        with daemon_lock():
            return False
    except RuntimeError:
        return True


def load_state():
    path = HOME / 'scheduler.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def due(now, cfg, state):
    hour, minute = map(int, cfg['hour'].split(':'))
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return False
    if state.get('day') != now.date().isoformat():
        return True
    if state.get('success') or state.get('attempts', 0) >= 3:
        return False
    return now.timestamp() >= state.get('retry_at', 0)


def serve():
    cfg = config()
    with daemon_lock():
        session = uuid.uuid4().hex
        engine.save_json(HOME / 'daemon.json', dict(pid=os.getpid(), session=session, started_at=dt.datetime.now(engine.TZ).isoformat()))
        state = load_state()
        while True:
            stop = HOME / 'stop-request.json'
            if stop.exists() and json.loads(stop.read_text(encoding='utf-8')).get('session') == session:
                break
            now = dt.datetime.now(engine.TZ)
            if due(now, cfg, state):
                if state.get('day') != now.date().isoformat():
                    state = dict(day=now.date().isoformat(), attempts=0, success=False)
                state['attempts'] += 1
                # Record an attempt before work, so a crash cannot create an infinite paid retry loop.
                state['retry_at'] = now.timestamp() + 900
                engine.save_json(HOME / 'scheduler.json', state)
                try:
                    result = run(cfg, scheduled=True)
                    state['success'] = result['result'] in ('published', 'skipped-valid')
                except Exception as exc:
                    state['error'] = str(exc)
                    state['retry_at'] = dt.datetime.now(engine.TZ).timestamp() + 900
                    print('Daily attempt failed: ' + str(exc), flush=True)
                engine.save_json(HOME / 'scheduler.json', state)
            engine.save_json(HOME / 'heartbeat.json', dict(session=session, checked_at=now.isoformat()))
            time.sleep(5)


def start():
    config()
    if running():
        emit(dict(result='already-running'))
        return
    credentials()  # Fail visibly before detaching if setup is incomplete.
    log = HOME / 'background.log'
    with log.open('ab') as output:
        options = dict(stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                       cwd=SCRIPT.parent, close_fds=True)
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        else:
            options['start_new_session'] = True
        env = dict(os.environ, WM_OBSIDIAN_HOME=str(HOME), PYTHONIOENCODING='utf-8')
        child = subprocess.Popen([sys.executable, str(SCRIPT), 'serve'], env=env, **options)
    for _ in range(40):
        if running():
            emit(dict(result='started-in-background', pid=child.pid, schedule=config()['hour'] + ' Asia/Shanghai'))
            return
        if child.poll() is not None:
            raise RuntimeError('Background CLI stopped during startup; inspect its local background.log')
        time.sleep(.1)
    raise RuntimeError('Background start could not be confirmed; inspect local status before retrying')


def stop():
    if not running():
        emit(dict(result='not-running'))
        return
    path = HOME / 'daemon.json'
    if not path.exists():
        raise RuntimeError('Daemon is initializing; retry stop in a moment')
    data = json.loads(path.read_text(encoding='utf-8'))
    engine.save_json(HOME / 'stop-request.json', dict(session=data['session']))
    # Cooperative stop never kills an unrelated process or interrupts an in-progress note write.
    emit(dict(result='stop-requested', note='The CLI exits after any active report finishes'))


def startup_path():
    appdata = os.environ.get('APPDATA')
    if not appdata:
        raise RuntimeError('Windows APPDATA is unavailable')
    return Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup' / (TASK + '.vbs')


def startup_fallback():
    if not shutil.which('wscript.exe'):
        raise RuntimeError('Task registration is unavailable and Windows Script Host is not installed')
    path = startup_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        (HOME / ('startup-backup-' + uuid.uuid4().hex[:8] + '.vbs')).write_bytes(path.read_bytes())
    quote = lambda value: '"' + str(value).replace('"', '""') + '"'
    command = subprocess.list2cmdline([sys.executable, str(SCRIPT), 'start'])
    script = ('Set shell = CreateObject("WScript.Shell")\r\n'
              'shell.Environment("PROCESS")("WM_OBSIDIAN_HOME") = ' + quote(HOME) + '\r\n'
              'WScript.Sleep 45000\r\n'
              'shell.Run ' + quote(command) + ', 0, False\r\n')
    path.write_text(script, encoding='utf-16')
    engine.save_json(HOME / 'autostart.json', dict(method='startup-folder', script=str(path)))
    emit(dict(result='login-autostart-installed', method='startup-folder', starts='45 seconds after login, hidden CLI'))


def autostart(uninstall=False):
    if os.name != 'nt':
        raise RuntimeError('Automatic login setup supports Windows; use your OS service manager elsewhere')
    config()
    if uninstall:
        record = HOME / 'autostart.json'
        if record.exists() and json.loads(record.read_text(encoding='utf-8')).get('method') == 'startup-folder':
            path = startup_path()
            if path.is_file():
                path.unlink()  # One exact application startup file only; never delete a directory.
            emit(dict(result='login-autostart-removed', method='startup-folder'))
            return
        subprocess.run(['schtasks.exe', '/Delete', '/TN', TASK, '/F'], check=True, capture_output=True)
        emit(dict(result='login-autostart-removed'))
        return
    # Task XML has no tokens. Runtime data and secrets remain in the user's local state directory.
    import xml.etree.ElementTree as ET
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    tag = lambda s: '{' + ns + '}' + s
    task = ET.Element(tag('Task'), version='1.2')
    triggers = ET.SubElement(task, tag('Triggers'))
    logon = ET.SubElement(triggers, tag('LogonTrigger'))
    ET.SubElement(logon, tag('Enabled')).text = 'true'
    ET.SubElement(logon, tag('Delay')).text = 'PT45S'
    principals = ET.SubElement(task, tag('Principals'))
    principal = ET.SubElement(principals, tag('Principal'), id='CurrentUser')
    import getpass as gp
    user = os.environ.get('USERDOMAIN', '') + '\\' + gp.getuser()
    ET.SubElement(principal, tag('UserId')).text = user.lstrip('\\')
    ET.SubElement(principal, tag('LogonType')).text = 'InteractiveToken'
    ET.SubElement(principal, tag('RunLevel')).text = 'LeastPrivilege'
    settings = ET.SubElement(task, tag('Settings'))
    for key, value in [('MultipleInstancesPolicy', 'IgnoreNew'), ('DisallowStartIfOnBatteries', 'false'),
                       ('StopIfGoingOnBatteries', 'false'), ('StartWhenAvailable', 'true'),
                       ('ExecutionTimeLimit', 'PT5M'), ('Enabled', 'true'), ('Hidden', 'true')]:
        ET.SubElement(settings, tag(key)).text = value
    actions = ET.SubElement(task, tag('Actions'), Context='CurrentUser')
    action = ET.SubElement(actions, tag('Exec'))
    ET.SubElement(action, tag('Command')).text = shutil.which('powershell.exe')
    # PowerShell launches a hidden CLI bootstrap; start() then detaches the long-lived worker.
    payload = "& '" + sys.executable.replace("'", "''") + "' '" + str(SCRIPT).replace("'", "''") + "' start"
    # A dedicated per-task state path is passed as an environment variable, not shell interpolation.
    payload = "$env:WM_OBSIDIAN_HOME='" + str(HOME).replace("'", "''") + "'; " + payload + '; exit $LASTEXITCODE'
    encoded = base64.b64encode(payload.encode('utf-16-le')).decode('ascii')
    ET.SubElement(action, tag('Arguments')).text = '-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + encoded
    ET.SubElement(action, tag('WorkingDirectory')).text = str(SCRIPT.parent)
    path = HOME / 'login-task.xml'
    path.write_bytes(ET.tostring(task, encoding='utf-16', xml_declaration=True))
    # Preserve an existing definition before replacing this application's task only.
    old = subprocess.run(['schtasks.exe', '/Query', '/TN', TASK, '/XML'], capture_output=True)
    if old.returncode == 0:
        (HOME / ('login-task-backup-' + uuid.uuid4().hex[:8] + '.xml')).write_bytes(old.stdout)
    result = subprocess.run(['schtasks.exe', '/Create', '/TN', TASK, '/XML', str(path), '/F'], capture_output=True)
    if result.returncode:
        startup_fallback()
        return
    engine.save_json(HOME / 'autostart.json', dict(method='task-scheduler'))
    emit(dict(result='login-autostart-installed', method='task-scheduler', starts='45 seconds after login, hidden CLI'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command')
    setup = commands.add_parser('init', help='Configure an existing vault; no secrets in config')
    setup.add_argument('--vault', required=True)
    setup.add_argument('--base-url', default='http://127.0.0.1:3000')
    setup.add_argument('--hour', default='08:00', help='Beijing time HH:MM')
    setup.add_argument('--worldmonitor-dir')
    for name in ('start', 'serve', 'stop', 'status', 'run', 'probe', 'secrets', 'install-autostart', 'uninstall-autostart'):
        commands.add_parser(name)
    args = parser.parse_args()
    command = args.command or 'start'
    if command == 'init':
        init(args)
    elif command == 'secrets':
        if os.name != 'nt':
            raise RuntimeError('On this OS set WORLDMONITOR_API_KEY and DEEPSEEK_API_KEY in the service environment')
        HOME.mkdir(parents=True, exist_ok=True)
        protected = {}
        for key in KEYS:
            value = getpass.getpass(key + ' (hidden): ').strip()
            if not value:
                raise RuntimeError('Empty credential; nothing saved')
            protected[key] = protect(value)
        engine.save_json(HOME / 'credentials.dpapi.json', protected)
        emit(dict(result='credentials-encrypted-for-current-Windows-user'))
    elif command == 'start': start()
    elif command == 'serve': serve()
    elif command == 'stop': stop()
    elif command == 'status':
        state = load_state()
        latest = HOME / 'latest-run.json'
        record = json.loads(latest.read_text(encoding='utf-8')) if latest.exists() else {}
        emit(dict(background_running=running(), schedule=state,
                  latest={k: record.get(k) for k in ('result', 'stage', 'finished_at', 'error')}))
    elif command in ('run', 'probe'): run(config(), probe=command == 'probe')
    elif command == 'install-autostart': autostart()
    elif command == 'uninstall-autostart': autostart(uninstall=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
