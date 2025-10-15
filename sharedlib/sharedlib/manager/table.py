'''
Module for updating the storage account table.
'''

from azure.data.tables import TableServiceClient
from datetime import datetime
import pandas as pd
import logging as log
import socket
import hashlib
import os

log = log.getLogger(__name__)

class TablestorageManager:
    def __init__(self, credential, table_endpoint, table_name):
        #table storage setup
        self.credential = credential
        self.table_name = table_name
        self.table_endpoint = table_endpoint
        self.computername = self.get_computername()
        self.partitionkey = 'pipeline-uploads'

    def authenticate(self):
        log.info(f'Attempting to authenticate with blob storage table: {self.table_endpoint}')
        try:
            self.table_service = TableServiceClient(endpoint=self.table_endpoint, credential=self.credential)
            log.info('Successfully authenticated.')
        except Exception as e:
            log.error(f'Could not authenticate. Error: {e}')

        self.table_client = self.table_service.create_table_if_not_exists(table_name=self.table_name)

    def hash_filename(self, filename):
        return hashlib.md5(filename.encode()).hexdigest()[:8]          

    def build_log_entity(self, status, zipfile, source, sessionid, duration) -> dict:

        return {
            'PartitionKey': self.partitionkey,
            'RowKey': self.hash_filename(zipfile),
            'Fullpath': zipfile,
            'Status': status,
            'Sessionid': sessionid,
            'ScriptLocation': self.computername,
            'Source': source,
            'Duration': duration,
            'StartTime': f'{datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}',
        }

    def calculate_duration(self, starttime):

        end = datetime.now()
        duration = end - starttime

        log.debug('Outputting human-friendly way of runtime duration of script.')
        seconds = int(duration.total_seconds())
        mins, secs = divmod(seconds, 60)
        hrs, mins = divmod(mins, 60)

        return f'{hrs}h {mins}m {secs}s'

    def failed_files(self, failed):

        failed_files = []

        for entry in self.table_client.query_entities(f"Status eq '{failed}'"):
            fullpath = entry.get('Fullpath')
            failed_files.append(fullpath)

        return failed_files

    def get_status_zipfile(self, zip):

        status = self.retrieve_log_entry(zip)

        return status

    def retrieve_log_entry(self, zip):

        try:
            results = self.table_client.query_entities(f"Fullpath eq '{zip}'")
            for result in results:
                return result
            
        except Exception as e:
            return False

    def determine_if_already_retrieved_from_queue(self, zipfile: str, source: str, status, Config):
        '''Determines if a triagepackage is already retrieved from queue and update log status'''

    def writes_log_entry_if_not_exists(self, zipfile: str, source: str, sessionid: str, status: str):

        zipfile = os.path.basename(zipfile)
        existing_entry = self.retrieve_log_entry(zipfile)
        entity = self.build_log_entity(status, zipfile, source, sessionid, None)

        if not existing_entry:
            log.info(f'No log entry found for {zipfile}. Writing new one.')
            self.table_client.create_entity(entity)
            return True

    def determine_if_need_for_processing(self, zipfile: str, source: str, sessionid: str, status: str, Config):
        '''Determines if a triagepackage should be processed by this script instance and writes log entries'''

        retry = Config.var_retryfailed

        zipfile = os.path.basename(zipfile)
        existing_entry = self.retrieve_log_entry(zipfile)
        entity = self.build_log_entity(status, zipfile, source, sessionid, None)

        if not existing_entry:
            log.info(f'No log entry found for {zipfile}. Writing new one.')

            self.table_client.create_entity(entity)
            return True

        '''
        if self.check_if_failed(status, existing_entry):
            log.info(f'{zipfile} previously failed.')
            
            if retry:
                self.update_log_entry(entity)
                log.info(f'Retrying {zipfile} (retry enabled).')
                return True
            if retry:
                log.info(f'Skipping {zipfile} (retry disabled).')
                return False
        '''

        if self.check_if_processing_by_this_instance(zipfile, sessionid):
            log.info(f'{zipfile} is already being processed by this instance.')
            return True

        log.info(f'Skipping {zipfile}. Already processed or processing.')
        return False

    def update_status_in_log(self, status, zipfile, source, sessionid, starttime):

        zipfile = os.path.basename(zipfile)

        duration = self.calculate_duration(starttime)

        entity = self.build_log_entity(status, zipfile, source, sessionid, duration)

        self.update_log_entry(entity)

    def check_if_processing_by_this_instance(self, zip, sessionid):
        '''Check if this script instance processes the zip file'''

        entry = self.retrieve_log_entry(zip)

        entry_sessionid = entry.get('Sessionid')
        if entry_sessionid == sessionid:
            log.info('Zip file is processed by this script instance.')
            return True
        
        return False
                
    def check_if_failed(self, status, log_entry):
        '''Check in log if entry is failed'''

        log_entry_status = log_entry.get('Status')
        if log_entry_status in (status.FAILED, status.DOWNLOADFAILED, status.EXTRACTIONFAILED, status.UPLOADFAILED):
            log.info('Zip file failed to process.')
            return True
        return False

    def update_log_entry(self, entity):
        '''Creates an entry to the storage blob log table.'''

        try:
            self.table_client.update_entity(entity)
            return True
        except Exception as e:
            log.error(f'Could not update table. Error: {e}')

    def output_logtable(self):
        results = list(self.table_client.query_entities(f"PartitionKey eq '{self.partitionkey}'"))
        df = pd.DataFrame(results)
        print('\n')
        print(df.to_string(index=False))
        print('\n')

    def get_computername(self):
        '''Returns the computername of the device running the script.'''

        log.info('Attempting to retrieve hostname.')
        try:
            hostname = socket.gethostname()
            log.info(f'Found hostname: {hostname}')
            return hostname
        except Exception as e:
            log.error('Failed to retrieve hostname.')
            return 'CouldNotRetrieveHostname'