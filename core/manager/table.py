'''
Module for updating an Azure Storage Table used as a processing log.

This module defines the `TablestorageManager` class, which wraps common
operations for writing and reading pipeline status information from an
Azure Table Storage table.
'''

from azure.data.tables import EntityProperty, EdmType
from azure.data.tables import TableServiceClient
from datetime import datetime
import uuid
import pandas as pd
import logging as log
import socket
import hashlib
import os
import sys

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

    def authenticate(self, verify_enabled):
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

        self.create_table_if_not_exists()
        if verify_enabled:
            self.has_write_access()
        else:
            log.info('Skipping write access check for Azure Storage Account Table as configured in .env with VAR_VERIFY_ENABLED.')

    def create_table_if_not_exists(self) -> bool:
        '''
        Create the configured table in Azure Table Storage if it does not
        already exist.

        Attempts to create the table and initializes self.table_client for
        subsequent read/write operations. If the table already exists, the
        existing table is used. If creation fails due to permissions or
        connectivity issues, the process exits.

        Returns:
            bool: True if the table exists or was successfully created.
        '''

        try:
            log.info(f"Creating table '{self.table_name}' if it does not already exist.")
            self.table_client = self.table_service.create_table_if_not_exists(
                table_name=self.table_name
            )
            log.info(f"Table '{self.table_name}' is ready.")
            return True
        except Exception as e:
            if e.status_code == 403:
                log.error(
                    f"Permission denied: could not create table '{self.table_name}'. "
                    f"Ensure 'Storage Table Data Contributor' is assigned to the "
                    f"identity on the storage account."
                )
                log.debug(f'Error: {e}')
            else:
                log.error(
                    f"HTTP error while creating table '{self.table_name}'. "
                    f"Error: {e}"
                )
            sys.exit(1)
        except Exception as e:
            log.error(
                f"Unexpected error while creating table '{self.table_name}'. "
                f"Error: {e}"
            )
            sys.exit(1)

    def has_write_access(self) -> bool:
        '''
        Test whether write access is enabled for the configured table.

        This method attempts to insert and then delete a temporary entity.
        If both operations succeed, write access is confirmed.

        Returns:
            bool: True if write access is available, False otherwise.
        '''
        test_entity = {
            'PartitionKey': 'write_test',
            'RowKey': str(uuid.uuid4()),
            'timestamp': datetime.utcnow().isoformat()
        }

        try:
            # Try to insert
            self.table_client.create_entity(entity=test_entity)

            # Cleanup (delete test entity)
            self.table_client.delete_entity(
                partition_key=test_entity['PartitionKey'],
                row_key=test_entity['RowKey']
            )

            log.info('Write access to table confirmed.')
            return True

        except Exception as e:

            log.error(
                f"Write access to table '{self.table_name}' failed. "
                f"Ensure 'Storage Table Data Contributor' permissions are "
                f"provided to the storage account."
                )
            log.debu(f'Error: {e}')
            sys.exit(1)

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

        # Convert size to int64 as otherwise a limit might be reached for entry in table
        size_int32 = status_data.get('zipfile_size', 0)
        size = EntityProperty(value=int(size_int32), edm_type=EdmType.INT64)

        return {
            'PartitionKey': self.partitionkey,
            'RowKey': self.hash_filename(status_data.get('zipfile_basename', '')),
            'ZipfileBasename': status_data.get('zipfile_basename', ''),
            'UploadId': status_data.get('uploadid', ''),
            'Status': status,
            'Sessionid': status_data.get('sessionid', ''),
            'ScriptLocation': self.computername,
            'Source': status_data.get('source_name', ''),
            'Duration': duration,
            'LastUpdatedTime': f'{datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}',
            'Extracted_Hostname': status_data.get('hostname', ''),
            'Size': size
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
        
        status_data = status_data['summary'][0]
        zipfile = status_data.get('zipfile_basename')
        existing_entry = self.retrieve_log_entry(zipfile)
        entity = self.build_log_entity(processing_status, None, status_data)

        if not existing_entry:
            log.info(f'No log entry found for {zipfile}. Writing new one.')
            entity['StartTime'] = f'{datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}'
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

        status_data = status_data['summary'][0]
        zipfile = status_data.get('zipfile_fullpath')
        
        zipfile = os.path.basename(zipfile)

        duration = self.calculate_duration(starttime)

        entity = self.build_log_entity(status, duration, status_data)

        self.update_log_entry(entity)

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