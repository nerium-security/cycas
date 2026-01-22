'''
Module for updating an Azure Storage Table used as a processing log.

This module defines the `TablestorageManager` class, which wraps common
operations for writing and reading pipeline status information from an
Azure Table Storage table.
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
        '''
        Initialize the Azure Table Storage logger.

        Stores authentication details and configuration required to write
        processing status entries to an Azure Table Storage table.

        Args:
            credential: Azure credential used to authenticate with Table Storage.
            table_endpoint (str): Endpoint URL of the Table Storage account.
            table_name (str): Name of the table used for logging.
        '''

        #table storage setup
        self.credential = credential
        self.table_name = table_name
        self.table_endpoint = table_endpoint
        self.computername = self.get_computername()
        self.partitionkey = 'pipeline-uploads'

    def authenticate(self):
        '''
        Authenticate to Azure Table Storage and create the log table if it
        does not already exists.

        Initializes the TableServiceClient and ensures the configured table
        exists before performing any read or write operations.

        '''

        log.info(f'Attempting to authenticate with blob storage table: {self.table_endpoint}')
        try:
            self.table_service = TableServiceClient(endpoint=self.table_endpoint, credential=self.credential)
            log.info('Successfully authenticated.')
        except Exception as e:
            log.error(f'Could not authenticate. Error: {e}')

        self.table_client = self.table_service.create_table_if_not_exists(table_name=self.table_name)

    def hash_filename(self, filename):
        '''
        Generate a short hash of a filename.

        Uses an MD5 hash of the filename and truncates it to 8 characters.
        Intended for use as a RowKey in Table Storage.

        Args:
            filename (str): Filename to hash.

        Returns:
            str: 8-character hexadecimal hash string.
        '''

        return hashlib.md5(filename.encode()).hexdigest()[:8]          

    def build_log_entity(self, status, duration, status_data) -> dict:
        '''
        Build a log entity dictionary for Azure Table Storage.

        Constructs a dictionary containing processing metadata and statistics
        extracted from the provided status data.

        Args:
            status (str): Processing status value to store in the log.
            duration (str): Human-readable duration string or None.
            status_data (dict): Dictionary containing zipfile metadata
                and processing statistics.

        Returns:
            dict: Table Storage entity ready to be inserted or updated.
        '''

        return {
            'PartitionKey': self.partitionkey,
            'RowKey': self.hash_filename(status_data['summary'][0].get('zipfile_basename', '')),
            'ZipfileBasename': status_data['summary'][0].get('zipfile_basename', ''),
            'Status': status,
            'Sessionid': status_data['summary'][0].get('sessionid', ''),
            'ScriptLocation': self.computername,
            'Source': status_data.get('source_name', ''),
            'Duration': duration,
            'StartTime': f'{datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}',
            'Extracted_Hostname': status_data['summary'][0].get('hostname', ''),
            'Size': status_data['summary'][0].get('zipfile_size', '')
        }

    def calculate_duration(self, starttime):
        '''
        Calculate a human-readable runtime duration.

        Computes the elapsed time between the provided start time and the
        current time.

        Args:
            starttime (datetime): Script start time.

        Returns:
            str: Duration formatted as '<hours>h <minutes>m <seconds>s'.
        '''

        end = datetime.now()
        duration = end - starttime

        log.debug('Outputting human-friendly way of runtime duration of script.')
        seconds = int(duration.total_seconds())
        mins, secs = divmod(seconds, 60)
        hrs, mins = divmod(mins, 60)

        return f'{hrs}h {mins}m {secs}s'

    def failed_files(self, failed):
        '''
        Retrieve full paths of files with a given failure status.

        Queries the log table for entries matching the provided failure
        status and extracts their file paths.

        Args:
            failed: Status value representing a failure state.

        Returns:
            list[str]: List of full file paths for failed entries.
        '''

        failed_files = []

        for entry in self.table_client.query_entities(f"Status eq '{failed}'"):
            fullpath = entry.get('Fullpath')
            failed_files.append(fullpath)

        return failed_files

    def get_status_zipfile(self, zip):
        '''
        Retrieve the log status for a given zipfile.

        Args:
            zip (str): Zipfile basename to look up.

        Returns:
            dict or False: Log entry if found, otherwise False.
        '''

        status = self.retrieve_log_entry(zip)

        return status

    def retrieve_log_entry(self, zip):
        '''
        Retrieve a single log entry for a zipfile.

        Queries the log table for an entry matching the provided
        zipfile basename.

        Args:
            zip (str): Zipfile basename.

        Returns:
            dict or False: Log entry if found, otherwise False.
        '''

        try:
            results = self.table_client.query_entities(f"ZipfileBasename eq '{zip}'")
            for result in results:
                return result
            
        except:
            return False

    def writes_log_entry_if_not_exists(self, processing_status: str, status_data: dict):
        '''
        Write a new log entry if one does not already exist.

        Checks for an existing log entry for the given zipfile and creates
        a new one if none is found.

        Args:
            processing_status (str): Initial processing status.
            status_data (dict): Metadata used to construct the log entry.

        Returns:
            bool: True if a new entry was created, otherwise None.
        '''

        zipfile = status_data.get('zipfile_basename')
        existing_entry = self.retrieve_log_entry(zipfile)
        entity = self.build_log_entity(processing_status, None, status_data)

        if not existing_entry:
            log.info(f'No log entry found for {zipfile}. Writing new one.')
            self.table_client.create_entity(entity)
            return True

    def update_status_in_log(self, status, starttime, status_data):
        '''
        Update the processing status and duration for a zipfile log entry.

        Builds a new entity using the provided status and calculated
        duration, then updates the existing table entry.

        Args:
            status: New processing status value.
            starttime (datetime): Start time used to calculate duration.
            status_data (dict): Zipfile metadata and statistics.
        '''

        zipfile = status_data['summary'][0].get('zipfile_fullpath')

        zipfile = os.path.basename(zipfile)

        duration = self.calculate_duration(starttime)

        entity = self.build_log_entity(status, duration, status_data)

        self.update_log_entry(entity)

    def check_if_processing_by_this_instance(self, zip, sessionid):
        '''
        Check whether the current script instance is processing a zipfile.

        Compares the stored session ID in the log entry with the provided
        session ID.

        Args:
            zip (str): Zipfile basename.
            sessionid: Session identifier for the current script instance.

        Returns:
            bool: True if the zipfile is processed by this instance.
        '''

        entry = self.retrieve_log_entry(zip)

        entry_sessionid = entry.get('Sessionid')
        if entry_sessionid == sessionid:
            log.info('Zip file is processed by this script instance.')
            return True
        
        return False

    def check_if_failed(self, status, log_entry):
        '''
        Check whether a log entry represents a failure state.

        Args:
            status: Status enum or object containing failure states.
            log_entry (dict): Log entry retrieved from the table.

        Returns:
            bool: True if the entry indicates a failure, otherwise False.
        '''

        log_entry_status = log_entry.get('Status')
        if log_entry_status in (status.FAILED, status.DOWNLOADFAILED, status.EXTRACTIONFAILED, status.UPLOADFAILED):
            log.info('Zip file failed to process.')
            return True
        return False

    def update_log_entry(self, entity):
        '''
        Update an existing log entry in Azure Table Storage.

        Args:
            entity (dict): Table Storage entity to update.

        Returns:
            bool: True if the update succeeds, otherwise None.
        '''

        try:
            self.table_client.update_entity(entity)
            return True
        except Exception as e:
            log.error(f'Could not update table. Error: {e}')

    def output_logtable(self):
        '''
        Output the contents of the log table to stdout.

        Queries all log entries for the configured partition key and
        prints them as a formatted pandas DataFrame.
        '''

        results = list(self.table_client.query_entities(f"PartitionKey eq '{self.partitionkey}'"))
        df = pd.DataFrame(results)
        print('\n')
        print(df.to_string(index=False))
        print('\n')

    def get_computername(self):
        '''
        Retrieve the hostname of the machine running the script.

        Returns:
            str: Hostname if successfully retrieved, otherwise a fallback value.
        '''

        log.debug('Attempting to retrieve hostname.')
        try:
            hostname = socket.gethostname()
            log.info(f'Script is running on: {hostname}')
            return hostname
        except Exception as e:
            log.error('Failed to retrieve hostname.')
            return 'CouldNotRetrieveHostname'