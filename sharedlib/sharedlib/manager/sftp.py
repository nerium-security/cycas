'''
Module for handling data in an SFTP server.
'''

import logging as log
import paramiko
import io
import stat
import os

log = log.getLogger(__name__)

class SftpManager:
    def __init__(self, host: str, username: str, port: int, ssh_key_str: str, ):
        self.host = host
        self.port = port
        self.username = username
        self.ssh_key_str = ssh_key_str

    def authenticate(self) -> bool:
        '''Authenticate and open an SFTP connection.'''
        try:

            log.info(f'Authenticating with SFTP server: {self.host}.')
            key_file = io.StringIO(self.ssh_key_str)
            private_key = paramiko.RSAKey.from_private_key(key_file)

            self.transport = paramiko.Transport((self.host, self.port))
            self.transport.connect(username=self.username, pkey=private_key)

            self.sftp = paramiko.SFTPClient.from_transport(self.transport)

            log.info(f'Successfully authenticated.')
            return True
        except Exception as e:
            log.error(f'SFTP authentication failed: {e}')
            return False

    def list_files(self, remote_path: str = '.') -> list:
        '''List all files in a remote SFTP directory.'''
        if not self.sftp:
            raise Exception('SFTP connection not established. Call authenticate() first.')

        try:
            files = self.sftp.listdir(path=remote_path)
            log.info(f'Found {len(files)} file(s) in {remote_path}')
            return files
        except Exception as e:
            log.error(f'Failed to list files in {remote_path}: {e}')
            return []

    def list_files_recursive(self, remote_path: str = '.') -> list:
        '''Recursively list all files in the given remote directory.'''

        if not self.sftp:
            raise Exception('SFTP connection not established. Call authenticate() first.')

        file_list = []
        
        details = self.username + '@' + self.host + ':' + self.port
        log.info(f'Listing files in: {details}')

        def walk(path):
            try:
                entries = self.sftp.listdir_attr(path)
                for entry in entries:
                    remote_file = f'{path.rstrip("/")}/{entry.filename}'
                    if stat.S_ISDIR(entry.st_mode):
                        walk(remote_file)
                    else:
                        file_list.append(remote_file)
            except Exception as e:
                log.error(f'Failed to walk {path}: {e}')

        walk(remote_path)
        log.info(f'Found {len(file_list)} file(s) recursively in {remote_path}')
        return file_list

    def download(self, local_path: str, remote_path: str) -> bool:
        '''Download a single file from the SFTP server to the local system.'''
        if not self.sftp:
            raise Exception('SFTP connection not established. Call authenticate() first.')

        basename = os.path.basename(remote_path)
        fullpath = os.path.join(local_path, basename)
        try:
            # Ensure the local folder exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            self.sftp.get(remote_path, fullpath)
            log.info(f'Downloaded: {remote_path} -> {local_path}')
            return fullpath
        except Exception as e:
            log.error(f'Failed to download {remote_path}: {e}')
            return False
        
    def delete(self, remote_path: str) -> bool:
        '''Delete a file on the remote SFTP server.'''
        if not self.sftp:
            raise Exception('SFTP connection not established. Call authenticate() first.')

        try:
            self.sftp.remove(remote_path)
            log.info(f'Deleted file: {remote_path}')
            return True
        except FileNotFoundError:
            log.warning(f'Path not found: {remote_path}')
            return False
        except Exception as e:
            log.error(f'Failed to delete {remote_path}: {e}')
            return False