#!/usr/bin/env python3
'''
Deploy the Cycas webapp to Azure App Service.

Reads connection details from .install_state.json (written by easy_install.py)
and pushes the current webapp/ and core/ code via Kudu ZIP deploy.

Usage:
    python deploy_webapp.py              # deploy code only
    python deploy_webapp.py --settings   # also push app settings from .env
'''

import argparse
import json
import sys
from pathlib import Path

from core.manager.azure import AzureManager
from core.manager.webapp import WebappManager

ROOT       = Path(__file__).parent
STATE_FILE = ROOT / '.install_state.json'
ENV_FILE   = ROOT / '.env'

_STARTUP_COMMAND = 'gunicorn --bind=0.0.0.0:8000 --timeout 600 webapp.app:app'


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_env(env_file):
    defaults = {}
    try:
        for line in Path(env_file).read_text().splitlines():
            line = line.split('#')[0].strip()
            if '=' in line:
                key, _, value = line.partition('=')
                k = key.strip().lower()
                if k not in defaults:
                    defaults[k] = value.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return defaults


def main():
    parser = argparse.ArgumentParser(description='Deploy Cycas webapp to Azure App Service.')
    parser.add_argument('--settings', action='store_true',
                        help='Also push app settings from .env before deploying.')
    args = parser.parse_args()

    state = load_state()
    if not state:
        print('[ERROR] No install state found. Run easy_install.py first.', file=sys.stderr)
        sys.exit(1)

    webapp_app = state.get('webapp_app')
    if not webapp_app:
        print('[ERROR] No webapp was provisioned in this installation.', file=sys.stderr)
        sys.exit(1)

    resource_group  = state['resource_group']
    subscription_id = state['subscription_id']

    print(f'  Webapp : {webapp_app}')
    print(f'  RG     : {resource_group}')
    print()

    azure      = AzureManager()
    credential = azure.authenticate()
    webapp     = WebappManager(credential, subscription_id)

    print('  >> Configuring startup command...')
    webapp.configure_startup(resource_group, webapp_app, _STARTUP_COMMAND)
    print('  [OK] Startup command configured.')

    if args.settings:
        print('  >> Applying app settings from .env...')
        env_settings = {k.upper(): v for k, v in read_env(ENV_FILE).items() if v}
        webapp.set_app_settings(resource_group, webapp_app, env_settings)
        print('  [OK] App settings applied.')

    print(f'  >> Deploying to {webapp_app}...')
    webapp.deploy(resource_group, webapp_app, ROOT / 'webapp', ROOT / 'core')
    print(f'  [OK] Deployed: https://{webapp_app}.azurewebsites.net')


if __name__ == '__main__':
    main()
