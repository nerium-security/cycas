'''
Module for ingesting data into ADx.
'''

import re
import json
import time
import os
import logging as log
import pandas as pd
import fileinput
from pathlib import Path
from datetime import timedelta
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder, DataFormat, ClientRequestProperties
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties, ReportLevel
from azure.kusto.ingest.status import KustoIngestStatusQueues

log = log.getLogger(__name__)

class AdxManager:
    def __init__(self, credential, adx_cluster_uri, adx_cluster_ingestion_uri, adx_database_name):
        self.credential = credential
        self.adx_database_name = adx_database_name
        self.adx_cluster_uri = adx_cluster_uri
        self.adx_cluster_ingestion_uri = adx_cluster_ingestion_uri
        self.kusto_client = None
        self.kusto_queued = None

    def authenticate(self):
        log.info(f'Authenticating with Azure Data Explorer cluster: {self.adx_cluster_uri}.')

        try:

            token_scope = 'https://kusto.kusto.windows.net/.default'
            token_provider = lambda: self.credential.get_token(token_scope).token

            kcsb_client = KustoConnectionStringBuilder.with_token_provider(self.adx_cluster_uri, token_provider)
            kcsb_queued = KustoConnectionStringBuilder.with_token_provider(self.adx_cluster_ingestion_uri, token_provider)

            self.kusto_client = KustoClient(kcsb_client)
            self.kusto_queued = QueuedIngestClient(kcsb_queued)

            log.info('Successfully authenticated.')
            return True

        except Exception as e:
            log.error(f'Authentication failed. Error: {e}')
            return False

    def query_db_test(self):

        log.info(f'Running a test query against the database: {self.adx_database_name}')
        try:

            query_test = '.show operations | limit 1'
            properties = ClientRequestProperties()
            properties.set_option(properties.request_timeout_option_name, timedelta(seconds=5))
            self.kusto_client.execute(self.adx_database_name, query_test, properties=properties)
            log.info(f'Successfully launched query: {query_test}')
            return True
        except Exception as e:
            log.error(f'Running test-query failed. Error: {e}')
            return False

    def convert_to_dataframe(self, f, nrows, chunksize):
        '''Convert a file or in-memory data to a Pandas DataFrame.'''

        try:

            df = pd.read_json(f, lines=True, nrows=nrows, chunksize=chunksize)
            log.debug(f'Loaded chunk of file into Pandas dataframe: {f}')

            if isinstance(df, pd.DataFrame):
                if df.empty:
                    log.info('Dataframe is empty')
                    return pd.DataFrame()

            return df
        except Exception as e:
            log.error(f'Error converting file to dataframe: {e}')
            return pd.DataFrame()

    def find_dynamic_int_columns(self, df, var_sample_size):
        
        log.debug('Entered function that finds dynamic and integer columns.')
        dict_columns = []
        int_columns = []

        if isinstance(df, pd.DataFrame):
            for column in df.columns:
                for item in df[column].head(var_sample_size):
                    if isinstance(item, dict):
                        if len(item.keys()) >= 1 and column not in dict_columns:
                            dict_columns.append(column)
                    if isinstance(item, int) and column not in int_columns:
                        int_columns.append(column)

        return dict_columns, int_columns

    def convert_dict_to_json(self, df, dyn_columns):
        log.debug('Entered function to convert dictionaries in the dataframe to json.')

        if dyn_columns:
            for dyn_column in dyn_columns:
                try:
                    df[dyn_column] = df[dyn_column].apply(lambda x: json.dumps(x) if isinstance(x, dict) else x)
                except Exception as e:
                    log.error(f'Could not convert column {dyn_column} of dataframe to json. Error: {e}')

    def get_tablename(self, filename):
        
        tablename = Path(filename).stem
        tablename = re.sub('%2F', '_', tablename)
        tablename = re.sub('[^0-9a-zA-Z_-]', '_', tablename)
        log.debug(f'Extracted the following name which will be used to create the table: {tablename}.')
        return tablename

    def remove_columnames_with_special_characters(self, colums):
        
        return colums[~colums.str.contains(r'\(.*\)')]

    def prepare_string_with_columnames(self, columns, dyn_columns, int_columns):
        ''' 
        Prepares the string with columnames and determines columntypes.
        Expected output: ['CreationTime']:date, ['PhysicalProcessorCount']:string, etc
        '''

        if not any([dyn_columns, int_columns]):
            dyn_columns = []
            int_columns = []

        time_columns = ['time', '0x30', '0x10', 'date', 'LastSeen', 'LastAccess']

        parts = []
        for columname in dict.fromkeys(columns):
            columname = columname.replace('>', '').replace('<', '')

            if any(item in columname.lower() for item in time_columns):
                dtype = 'date'
            elif columname in dyn_columns:
                dtype = 'dynamic'
            elif columname in int_columns:
                dtype = 'int'
            else:
                dtype = 'string'

            parts.append(f"['{columname}']:{dtype}")

        # Adding columns Hostname and Sourcefile
        #parts.append("['Hostname']:string")
        #parts.append("['Sourcefile']:string")

        return ', '.join(parts)

    def create_new_table_if_required(self, Config, file):
        ''' Creates a new table when there is isn't one or when a new column needs to be added to the table '''

        tablename = self.get_tablename(os.path.basename(file))

        table_exists, existing_columns = self.check_if_table_exists(tablename)

        cmd_createmergetable, new_columns = self.get_table_createcommand(file, Config, tablename)
               
        new_columns_exists = self.checking_if_new_columns_exists(existing_columns, new_columns)

        if not table_exists or new_columns_exists:

            self.launch_createmerge_table(cmd_createmergetable)

        return tablename
        

    def checking_if_new_columns_exists(self, existing_columns, new_columns):
        ''' Returns True if a new column is observed that needs to be added to the table in ADX. '''

        existing_columns_clean = [col.get('ColumnName') for col in existing_columns]

        for new_column in new_columns:
            if not new_column in existing_columns_clean:
                log.debug(f'Column does not exist yet: {new_column}')
                return True

        return False

    def get_table_createcommand(self, file, Config, tablename):
        ''' Prepares the command for creating a table in ADX'''

        df = self.convert_to_dataframe(file, Config.var_sample_size, chunksize=None)

        columnames = self.remove_columnames_with_special_characters(df.columns)

        dyn_columns, int_columns = self.find_dynamic_int_columns(df, Config.var_sample_size)

        columnstring = self.prepare_string_with_columnames(columnames, dyn_columns, int_columns)

        return f'.create-merge table {tablename} ({columnstring})', columnames


    def read_ingestion_properties(self, tablename):

        return IngestionProperties(
            database=self.adx_database_name,
            table=tablename,
            data_format=DataFormat.JSON,
            report_level=ReportLevel.FailuresAndSuccesses)

    def launch_upload_file(self, tablename: str, fullpath: str) -> bool:
        '''Uploads a file to adx'''

        ingestion_props = self.read_ingestion_properties(tablename)

        try:
            basename = os.path.basename(fullpath)

            self.kusto_queued.ingest_from_file(fullpath, ingestion_properties=ingestion_props)

            log.info(f'Successfully initiated upload request of {basename} to table {tablename}')

            return True
        except Exception as e:
            log.error(f'Failed to initiate the data upload request of {basename} to table {tablename}. Error: {e}' )
            return False

    def add_hostname_to_file(self, fullpath, hostname, zipfile):
        ''' Adds hostname and sourcefilename inline to file'''

        columns = f',"Sourcefilename":"{zipfile}","Hostname":"{hostname}"'
        replacement = columns + '}'

        for line in fileinput.input(fullpath, inplace=True):
            line = line.rstrip("\n")
            if line.endswith("}"):
                line = line[:-1] + replacement
            print(line)

    def check_if_table_exists(self, tablename):

        query = f'{tablename} | limit 0'
        try:
            response_query = self.kusto_client.execute_query(self.adx_database_name, query)
            raw_columns = response_query.primary_results[0].raw_columns
            if raw_columns:
                return True, raw_columns
        except:
            log.info(f'Table {tablename} does not exist.')
            return False, ''

    def launch_createmerge_table(self, cmd_createmergetable):
        ''' Creates the table in ADX. '''

        try:
            self.kusto_client.execute_mgmt(self.adx_database_name, cmd_createmergetable)
            log.info(f'Successfully launched command: {cmd_createmergetable}')
            return True
        except Exception as e:
            log.error(f'Failed to launch command {cmd_createmergetable}. Error: {e}')
            return False

    def check_ingestion_status(self, max_wait_seconds=180) -> bool:
        '''
        Monitors the ingestion status queue to verify whether ingestion succeeded.
        Requires ReportLevel.FailuresAndSuccesses and ReportMethod.Queue.

        Returns:
            True if success message is received
            False if failure message is received or timeout
        '''

        if not self.kusto_queued:
            log.error('No Kusto queued ingest client available.')
            return False

        try:
            qs = KustoIngestStatusQueues(self.kusto_queued)

            backoff = 1
            total_wait = 0

            while total_wait < max_wait_seconds:
                if not qs.success.is_empty():
                    success_messages = qs.success.pop(32)
                    for msg in success_messages:
                        log.info(f'Ingested: {msg.IngestionSourcePath}')
                    return True

                if not qs.failure.is_empty():
                    failure_messages = qs.failure.pop(32)
                    for msg in failure_messages:
                        log.error(f'Ingestion failed: {msg.IngestionSourcePath} - {msg.Details}')
                    return False

                log.debug(f'Waiting for ingestion status... ({backoff}s)')
                time.sleep(backoff)
                total_wait += backoff
                backoff = min(backoff * 2, 30)  # Exponential backoff capped at 30s

            log.warning('Ingestion status check timed out.')
            return False

        except Exception as e:
            log.error(f'Error while checking ingestion status: {e}')
            return False
