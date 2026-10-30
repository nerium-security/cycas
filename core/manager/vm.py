'''
Module for provisioning the Velociraptor server VM.

Provides VmManager for creating a static public IP with a DNS name, a network
security group, a virtual network, a network interface and an Ubuntu VM, and
for running a script on the VM through the Custom Script Extension.

Uses the ARM REST API directly, like WebappManager, so no compute or network
SDK is needed.
'''

import logging as log
import re
import time

import requests

log = log.getLogger(__name__)

_NETWORK_API   = '2024-05-01'
_COMPUTE_API   = '2024-07-01'
_POLL_INTERVAL = 10

DEFAULT_IMAGE = 'Canonical:ubuntu-26_04-lts:server:latest'


def parse_image_urn(urn):
    '''Turn an image URN (publisher:offer:sku:version) into an imageReference.'''
    parts = urn.split(':')
    if len(parts) != 4 or not all(parts):
        raise ValueError(f"Invalid image '{urn}', expected publisher:offer:sku:version.")
    return dict(zip(('publisher', 'offer', 'sku', 'version'), parts))


class ScriptFailed(RuntimeError):
    '''The Custom Script Extension script exited with an error.

    str() is the last line the script printed, which for bootstrap.py names
    the step that failed; stdout and stderr hold what the extension kept of
    the script output (its last few KB).
    '''

    def __init__(self, message, stdout='', stderr=''):
        self.stdout = stdout
        self.stderr = stderr
        last_line = next((l for l in reversed(stdout.splitlines()) if l.strip()), '')
        super().__init__(last_line or message)


class VmManager:
    def __init__(self, credential, subscription_id):
        self.credential      = credential
        self.subscription_id = subscription_id

    def _token(self):
        return self.credential.get_token('https://management.azure.com/.default').token

    def _rg_path(self, resource_group):
        return f'/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}'

    def _error_message(self, resp):
        try:
            err = resp.json().get('error', resp.json())
            return err.get('message', str(err))
        except Exception:
            return resp.text

    def _arm(self, method, path, api_version, **kwargs):
        '''
        Make an authenticated ARM request and wait for any async operation.

        Network and Compute report long-running PUTs through an
        Azure-AsyncOperation header on a 200/201 response, not only on 202,
        so poll whenever that header is present. For PUT requests the final
        resource is fetched once the operation succeeds.
        '''
        url = f'https://management.azure.com{path}?api-version={api_version}'
        headers = {
            'Authorization': f'Bearer {self._token()}',
            'Content-Type':  'application/json',
        }
        resp = requests.request(method, url, headers=headers, timeout=120, **kwargs)
        if not resp.ok:
            raise requests.exceptions.HTTPError(
                f'{resp.status_code} {resp.reason} — {self._error_message(resp)}', response=resp
            )

        poll_url = resp.headers.get('Azure-AsyncOperation')
        if not poll_url and resp.status_code == 202:
            poll_url = resp.headers.get('Location')
        if poll_url:
            self._wait(poll_url)
            if method.upper() == 'PUT':
                return self._arm_get(path, api_version) or {}

        return resp.json() if resp.content else {}

    def _arm_get(self, path, api_version, params=None):
        url  = f'https://management.azure.com{path}'
        resp = requests.get(
            url, params={'api-version': api_version, **(params or {})},
            headers={'Authorization': f'Bearer {self._token()}'}, timeout=30,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def _wait(self, poll_url):
        while True:
            time.sleep(_POLL_INTERVAL)
            poll = requests.get(poll_url, headers={'Authorization': f'Bearer {self._token()}'}, timeout=30)
            poll.raise_for_status()
            # A Location URL answers 202 while the operation is still running.
            if poll.status_code == 202:
                continue
            data   = poll.json() if poll.content else {}
            status = data.get('status', 'Succeeded')
            if status == 'Succeeded':
                return data
            if status in ('Failed', 'Canceled'):
                error = data.get('error', {})
                raise RuntimeError(f'ARM operation {status}: {error.get("message", error)}')

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def dns_label_available(self, location, label):
        '''Whether <label>.<location>.cloudapp.azure.com is still free.'''
        result = self._arm_get(
            f'/subscriptions/{self.subscription_id}/providers/Microsoft.Network'
            f'/locations/{location}/CheckDnsNameAvailability',
            _NETWORK_API, params={'domainNameLabel': label},
        )
        return bool(result and result.get('available'))

    def provision_public_ip(self, resource_group, name, location, dns_label):
        '''
        Create a static Standard SKU public IP with a DNS name.

        Returns:
            dict: 'id', 'ip_address' and 'fqdn' (<dns_label>.<location>.cloudapp.azure.com).
        '''
        path   = f'{self._rg_path(resource_group)}/providers/Microsoft.Network/publicIPAddresses/{name}'
        result = self._arm(
            'PUT', path, _NETWORK_API,
            json={
                'location': location,
                'sku':      {'name': 'Standard', 'tier': 'Regional'},
                'properties': {
                    'publicIPAllocationMethod': 'Static',
                    'publicIPAddressVersion':   'IPv4',
                    'dnsSettings':              {'domainNameLabel': dns_label},
                },
            },
        )
        props = result.get('properties', {})
        info  = {
            'id':         result['id'],
            'ip_address': props.get('ipAddress'),
            'fqdn':       props.get('dnsSettings', {}).get('fqdn'),
        }
        log.info(f"Public IP '{name}' ready: {info['ip_address']} ({info['fqdn']}).")
        return info

    def provision_nsg(self, resource_group, name, location, ssh_allowed_ips):
        '''
        Create or update the network security group.

        Port 443 is open to the internet: the Velociraptor frontend and GUI
        share it, clients must reach the frontend, and the GUI is limited to
        GUI.allowed_cidr by Velociraptor itself. Port 80 is open for the
        Let's Encrypt challenge (Velociraptor autocert); Velociraptor only
        answers ACME challenges and redirects to HTTPS there. SSH is only open
        to ssh_allowed_ips (IPs or CIDRs); with none given there is no SSH
        rule. Everything else inbound is denied by the default rules.
        '''
        rules = [{
            'name': 'allow-velociraptor-https',
            'properties': {
                'priority': 100, 'direction': 'Inbound', 'access': 'Allow', 'protocol': 'Tcp',
                'sourceAddressPrefix': 'Internet', 'sourcePortRange': '*',
                'destinationAddressPrefix': '*', 'destinationPortRange': '443',
            },
        }, {
            'name': 'allow-letsencrypt-http',
            'properties': {
                'priority': 105, 'direction': 'Inbound', 'access': 'Allow', 'protocol': 'Tcp',
                'sourceAddressPrefix': 'Internet', 'sourcePortRange': '*',
                'destinationAddressPrefix': '*', 'destinationPortRange': '80',
            },
        }]
        if ssh_allowed_ips:
            rules.append({
                'name': 'allow-ssh-admin',
                'properties': {
                    'priority': 110, 'direction': 'Inbound', 'access': 'Allow', 'protocol': 'Tcp',
                    'sourceAddressPrefixes': [ip if '/' in ip else f'{ip}/32' for ip in ssh_allowed_ips],
                    'sourcePortRange': '*',
                    'destinationAddressPrefix': '*', 'destinationPortRange': '22',
                },
            })
        path   = f'{self._rg_path(resource_group)}/providers/Microsoft.Network/networkSecurityGroups/{name}'
        result = self._arm('PUT', path, _NETWORK_API,
                           json={'location': location, 'properties': {'securityRules': rules}})
        log.info(f"Network security group '{name}' ready ({len(rules)} rule(s)).")
        return result['id']

    def provision_network(self, resource_group, vnet_name, location, address_prefix='10.42.0.0/24'):
        '''Create a virtual network with a single subnet. Returns the subnet ID.'''
        path     = f'{self._rg_path(resource_group)}/providers/Microsoft.Network/virtualNetworks/{vnet_name}'
        existing = self._arm_get(path, _NETWORK_API)
        if existing:
            log.info(f"Virtual network '{vnet_name}' already exists, skipping creation.")
            return existing['properties']['subnets'][0]['id']
        result = self._arm(
            'PUT', path, _NETWORK_API,
            json={
                'location': location,
                'properties': {
                    'addressSpace': {'addressPrefixes': [address_prefix]},
                    'subnets': [{'name': 'default', 'properties': {'addressPrefix': address_prefix}}],
                },
            },
        )
        log.info(f"Virtual network '{vnet_name}' ready.")
        return result['properties']['subnets'][0]['id']

    def provision_nic(self, resource_group, name, location, subnet_id, public_ip_id, nsg_id):
        '''Create a network interface with the public IP and NSG attached. Returns its ID.'''
        path   = f'{self._rg_path(resource_group)}/providers/Microsoft.Network/networkInterfaces/{name}'
        result = self._arm(
            'PUT', path, _NETWORK_API,
            json={
                'location': location,
                'properties': {
                    'networkSecurityGroup': {'id': nsg_id},
                    'ipConfigurations': [{
                        'name': 'ipconfig1',
                        'properties': {
                            'privateIPAllocationMethod': 'Dynamic',
                            'subnet':          {'id': subnet_id},
                            'publicIPAddress': {'id': public_ip_id},
                        },
                    }],
                },
            },
        )
        log.info(f"Network interface '{name}' ready.")
        return result['id']

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def provision_vm(self, resource_group, name, location, nic_id, vm_size,
                     admin_username, ssh_public_key, os_disk_gb=1024, image=DEFAULT_IMAGE,
                     os_disk_name=None):
        '''
        Create a Linux VM with SSH key login only and Trusted Launch.

        image is a URN (publisher:offer:sku:version) of a Gen2 image that
        supports Trusted Launch, by default Ubuntu 26.04 LTS.

        The datastore lives on the OS disk; Ubuntu grows the root partition to
        the full disk size on first boot. The disk and network interface are
        deleted together with the VM. os_disk_name defaults to <name>-osdisk,
        instead of Azure's long generated name.

        An existing VM is returned unchanged: its OS profile cannot be updated.
        '''
        path     = f'{self._rg_path(resource_group)}/providers/Microsoft.Compute/virtualMachines/{name}'
        existing = self._arm_get(path, _COMPUTE_API)
        if existing:
            log.info(f"VM '{name}' already exists, skipping creation.")
            return existing
        result = self._arm(
            'PUT', path, _COMPUTE_API,
            json={
                'location': location,
                'properties': {
                    'hardwareProfile': {'vmSize': vm_size},
                    'storageProfile': {
                        'imageReference': parse_image_urn(image),
                        'osDisk': {
                            'name':         os_disk_name or f'{name}-osdisk',
                            'createOption': 'FromImage',
                            'diskSizeGB':   os_disk_gb,
                            'managedDisk':  {'storageAccountType': 'StandardSSD_LRS'},
                            'deleteOption': 'Delete',
                        },
                    },
                    'osProfile': {
                        'computerName':  name[:64],
                        'adminUsername': admin_username,
                        'linuxConfiguration': {
                            'disablePasswordAuthentication': True,
                            'ssh': {'publicKeys': [{
                                'path':    f'/home/{admin_username}/.ssh/authorized_keys',
                                'keyData': ssh_public_key,
                            }]},
                        },
                    },
                    'networkProfile': {
                        'networkInterfaces': [{'id': nic_id, 'properties': {'deleteOption': 'Delete'}}],
                    },
                    'securityProfile': {
                        'securityType': 'TrustedLaunch',
                        'uefiSettings': {'secureBootEnabled': True, 'vTpmEnabled': True},
                    },
                },
            },
        )
        log.info(f"VM '{name}' ready.")
        return result

    def run_script(self, resource_group, vm_name, location, file_uris, command,
                   extension_name='cycas-velociraptor'):
        '''
        Run a command on the VM with the Custom Script Extension and wait for it.

        file_uris are downloaded into the working directory first; command runs
        there as root. Both go in protectedSettings, which Azure stores
        encrypted and never returns, so they may contain SAS tokens and
        secrets. A new forceUpdateTag makes the extension run again on every
        call, also when nothing else changed.

        Returns:
            str: The script output (the extension keeps its last few KB).

        Raises:
            ScriptFailed: The command exited non-zero.
        '''
        path = (f'{self._rg_path(resource_group)}/providers/Microsoft.Compute'
                f'/virtualMachines/{vm_name}/extensions/{extension_name}')
        try:
            self._arm(
                'PUT', path, _COMPUTE_API,
                json={
                    'location': location,
                    'properties': {
                        'publisher':               'Microsoft.Azure.Extensions',
                        'type':                    'CustomScript',
                        'typeHandlerVersion':      '2.1',
                        'autoUpgradeMinorVersion': True,
                        'forceUpdateTag':          str(int(time.time())),
                        'settings':                {},
                        'protectedSettings':       {'fileUris': file_uris, 'commandToExecute': command},
                    },
                },
            )
        except RuntimeError as e:
            # The extension reports a failed command as a failed operation,
            # with the tail of the output embedded in the message.
            stdout, stderr = _split_script_output(str(e))
            raise ScriptFailed(str(e), stdout, stderr) from None

        view = self._arm_get(path, _COMPUTE_API, params={'$expand': 'instanceView'}) or {}
        for status in view.get('properties', {}).get('instanceView', {}).get('substatuses', []):
            if '/StdOut/' in status.get('code', ''):
                return status.get('message', '')
        return ''


def _split_script_output(message):
    '''Pull the [stdout] and [stderr] sections out of an extension error message.'''
    match = re.search(r'\[stdout\]\n?(.*?)\n?\[stderr\]\n?(.*)', message, re.DOTALL)
    return (match.group(1), match.group(2)) if match else ('', '')
