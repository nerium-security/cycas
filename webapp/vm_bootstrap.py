#!/usr/bin/env python3
'''
Set up the Cycas web app on an Ubuntu VM.

Runs on the VM as root, started by the Azure Custom Script Extension that
easy_install.py configures. Uses the standard library only.

The web app runs with gunicorn as a systemd service on 127.0.0.1:4040, as an
unprivileged user. Caddy in front of it serves HTTPS on the VM's DNS name with
a Let's Encrypt certificate (HTTP-01 challenge on port 80). The app reaches
Azure with the VM's managed identity; its login password comes from Key Vault.

Parameters are JSON, passed base64-encoded in CYCAS_WEBAPP_PARAMS or read from
a file with --params: fqdn (the VM's DNS name), app_zip (the web app package
in the working directory, default app.zip), env (the .env values) and steps
(the steps to run, default all). easy_install.py runs it twice: the slow steps
that do not need the .env (packages to venv) while the rest of Azure is still
being created, and the remaining steps once the .env is final.

The last line of output is CYCAS_WEBAPP_RESULT <json> on success, or
CYCAS_WEBAPP_FAILED step=<name>: <reason>. The full log is in
/opt/cycas-webapp/bootstrap.log.
'''

import argparse
import base64
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

WORK         = Path('/opt/cycas-webapp')
APP          = WORK / 'app'
VENV         = WORK / 'venv'
SERVICE_USER = 'cycas-webapp'
SERVICE      = 'cycas-webapp'
APP_ADDRESS  = ('127.0.0.1', 4040)

UNIT = f'''[Unit]
Description=Cycas web app
After=network-online.target
Wants=network-online.target

[Service]
User={SERVICE_USER}
Group={SERVICE_USER}
WorkingDirectory={APP}
Environment=HOME={WORK}
ExecStart={VENV}/bin/gunicorn --bind={APP_ADDRESS[0]}:{APP_ADDRESS[1]} --timeout 600 webapp.app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
'''


class StepFailed(Exception):
    pass


class Bootstrap:
    def __init__(self, params):
        self.p        = params
        WORK.mkdir(parents=True, exist_ok=True)
        self.log_file = WORK / 'bootstrap.log'
        self.result   = {'url': f'https://{params["fqdn"]}/'}

    def log(self, msg, console=True):
        line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
        if console:
            print(line, flush=True)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def run(self, cmd, **kwargs):
        self.log('$ ' + ' '.join(str(c) for c in cmd))
        result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kwargs)
        for line in (result.stdout + result.stderr).splitlines():
            self.log('  ' + line, console=False)
        if result.returncode != 0:
            lines = result.stderr.strip().splitlines() or result.stdout.strip().splitlines() or ['no output']
            raise StepFailed(f'{Path(str(cmd[0])).name} exited with {result.returncode}: {lines[-1]}')
        return result

    def wait_for(self, check, what, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return check()
            except (OSError, ssl.SSLError) as e:
                if time.monotonic() > deadline:
                    raise StepFailed(f'{what} within {timeout}s ({e}).')
                time.sleep(5)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def step_packages(self):
        # On a fresh VM cloud-init and unattended upgrades often still hold the
        # apt lock, so wait for it instead of failing.
        env = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}
        apt = ['apt-get', '-o', 'DPkg::Lock::Timeout=900', '-y', '-q']
        self.run(apt + ['update'], env=env)
        self.run(apt + ['install', 'python3-venv', 'caddy'], env=env)

    def step_user(self):
        if subprocess.run(['id', SERVICE_USER], capture_output=True).returncode != 0:
            self.run(['useradd', '--system', '--home-dir', WORK, '--no-create-home',
                      '--shell', '/usr/sbin/nologin', SERVICE_USER])

    def step_app(self):
        # Unpack next to the current version and swap, so a failed unpack
        # leaves the running app untouched.
        staging = WORK / 'app.new'
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(self.p.get('app_zip', 'app.zip')) as zf:
            zf.extractall(staging)
        # Keep the current settings until the env step writes the new ones: on
        # a re-run that comes much later, and the running app may restart before.
        if (APP / '.env').exists():
            shutil.copy2(APP / '.env', staging / '.env')
            shutil.chown(staging / '.env', 'root', SERVICE_USER)
        shutil.rmtree(APP, ignore_errors=True)
        staging.rename(APP)
        self.run(['chmod', '-R', 'u=rwX,go=rX', APP])
        if (APP / '.env').exists():
            (APP / '.env').chmod(0o640)

    def step_venv(self):
        if not (VENV / 'bin' / 'python').exists():
            self.run([sys.executable, '-m', 'venv', VENV])
        self.run([VENV / 'bin' / 'pip', 'install', '--quiet', '--upgrade', 'pip'])
        self.run([VENV / 'bin' / 'pip', 'install', '--quiet', '-r', APP / 'requirements.txt'])

    def step_env(self):
        # Read by core.utils.config through find_dotenv(). Only the service
        # user may read it: it can contain SAS URLs.
        # A JSON string is a valid double-quoted dotenv value (\" and \\ escapes);
        # ensure_ascii=False because python-dotenv does not decode \u escapes.
        lines = [f'{key}={json.dumps(str(value), ensure_ascii=False)}' for key, value in sorted(self.p['env'].items())]
        env_file = APP / '.env'
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        env_file.chmod(0o640)   # os.open only applies the mode when it creates the file
        shutil.chown(env_file, 'root', SERVICE_USER)

    def step_service(self):
        Path(f'/etc/systemd/system/{SERVICE}.service').write_text(UNIT)
        self.run(['systemctl', 'daemon-reload'])
        self.run(['systemctl', 'enable', SERVICE])
        self.run(['systemctl', 'restart', SERVICE])
        try:
            self.wait_for(lambda: socket.create_connection(APP_ADDRESS, timeout=5).close(),
                          f'The web app did not come up on {APP_ADDRESS[0]}:{APP_ADDRESS[1]}', 120)
        except StepFailed:
            journal = self.run(['journalctl', '-u', SERVICE, '-n', '25', '--no-pager'])
            for line in journal.stdout.strip().splitlines():
                self.log('  | ' + line)
            raise

    def step_caddy(self):
        Path('/etc/caddy/Caddyfile').write_text(
            f'{self.p["fqdn"]} {{\n\treverse_proxy {APP_ADDRESS[0]}:{APP_ADDRESS[1]}\n}}\n')
        self.run(['systemctl', 'enable', 'caddy'])
        self.run(['systemctl', 'restart', 'caddy'])

    def step_certificate(self):
        '''Check that Caddy serves a publicly trusted certificate for the DNS name.'''
        context = ssl.create_default_context()

        def check():
            with socket.create_connection(('127.0.0.1', 443), timeout=30) as sock:
                with context.wrap_socket(sock, server_hostname=self.p['fqdn']) as tls:
                    return tls.getpeercert()

        cert   = self.wait_for(check, f"No trusted certificate for {self.p['fqdn']}; "
                                      'see journalctl -u caddy for the Let\'s Encrypt errors', 300)
        issuer = dict(item[0] for item in cert.get('issuer', ()))
        self.log(f'Certificate issued by {issuer.get("organizationName", "?")}, valid until {cert.get("notAfter")}.')

    STEPS = [
        ('packages',    step_packages),
        ('user',        step_user),
        ('app',         step_app),
        ('venv',        step_venv),
        ('env',         step_env),
        ('service',     step_service),
        ('caddy',       step_caddy),
        ('certificate', step_certificate),
    ]


def main():
    parser = argparse.ArgumentParser(description='Set up the Cycas web app.')
    parser.add_argument('--params', help='JSON parameter file (default: base64 JSON in CYCAS_WEBAPP_PARAMS)')
    args = parser.parse_args()

    try:
        if args.params:
            params = json.loads(Path(args.params).read_text())
        else:
            params = json.loads(base64.b64decode(os.environ['CYCAS_WEBAPP_PARAMS']))
    except (KeyError, ValueError) as e:
        print(f'CYCAS_WEBAPP_FAILED step=init: no valid parameters ({e})', flush=True)
        return 1

    # Check the input before Bootstrap() creates anything on the system.
    selected = params.get('steps') or [name for name, _ in Bootstrap.STEPS]
    unknown  = [s for s in selected if s not in dict(Bootstrap.STEPS)]
    if unknown:
        print(f'CYCAS_WEBAPP_FAILED step=init: unknown step(s) {", ".join(unknown)}', flush=True)
        return 1
    bootstrap = Bootstrap(params)

    for name, fn in Bootstrap.STEPS:
        if name not in selected:
            continue
        bootstrap.log(f'== {name}')
        try:
            fn(bootstrap)
        except Exception as e:
            bootstrap.log(traceback.format_exc(), console=False)
            bootstrap.log(f'CYCAS_WEBAPP_FAILED step={name}: {e}')
            return 1

    bootstrap.log('CYCAS_WEBAPP_RESULT ' + json.dumps(bootstrap.result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
