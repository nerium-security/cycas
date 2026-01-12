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
import numpy as np
from pathlib import Path
from datetime import timedelta
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder, DataFormat, ClientRequestProperties
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties, ReportLevel
from azure.kusto.ingest.status import KustoIngestStatusQueues
from datetime import datetime
from collections import Counter

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

            # --- In-memory JSON cases ---
            if isinstance(f, dict):
                df = pd.DataFrame([f], dtype=object)

            elif isinstance(f, list):
                df = pd.DataFrame(f, dtype=object)

            # --- Filepath case ---
            elif isinstance(f, (str, Path)):
                df = pd.read_json(f, lines=True, nrows=nrows, chunksize=chunksize, dtype=object)
                log.debug(f'Loaded chunk of file into Pandas dataframe: {f}')

            if isinstance(df, pd.DataFrame):
                if df.empty:
                    log.info('Dataframe is empty')
                    return pd.DataFrame()

            return df
        except Exception as e:

            log.error(f'Error converting {f} to dataframe: {e}')
            return pd.DataFrame()

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

    def prepare_string_with_columnames(self, schema):
        ''' 
        Prepares the string with columnames and determines columntypes.
        Expected output: ['CreationTime']:date, ['PhysicalProcessorCount']:string, etc
        '''

        parts = []
        
        for columnname in dict.fromkeys(schema):  # preserves order, removes duplicates

            clean_name = self.sanitize_adx_column_name(columnname)
            
            # default safely to string if missing from schema
            adx_type = schema.get(clean_name, 'string')

            parts.append(f"['{clean_name}']:{adx_type}")

        return ', '.join(parts)


    def _classify_value(self, value: str):
        '''
        Classify value into an ADX type.

        Returns one of:
        - 'long'
        - 'datetime'
        - 'dynamic'
        - 'string'
        '''

        if value is None:
            return None

        # If dropna() was used:
        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None

        # Real structured python objects
        if isinstance(value, (dict, list)):
            return 'dynamic'

        # Bool before int
        if isinstance(value, (bool)):
            return 'bool'

        # Real datetime objects
        if isinstance(value, (datetime)):
            return 'datetime'

        # Numeric objects
        if isinstance(value, (int)):
            return 'long'

        # Epoch detection for float 
        if isinstance(value, (float)):
            num = float(value)
            if 1_000_000_000 <= num <= 20_000_000_000_000:
                return 'datetime'
            else:
                return 'real'

        if isinstance(value, str):
            s = value.strip()

            # ISO-8601 datetime
            try:
                datetime.fromisoformat(s.replace('Z', '+00:00'))
                return 'datetime'
            except ValueError:
                pass

        # return string for rest (which includes unclear and vague values)
        return 'string'


    def infer_adx_type_majority(self, df, sample_size=100):
        '''
        Infer ADX type by majority vote.
        '''

        schema = {}
        
        for col in df.columns:

            values = df[col].dropna().head(sample_size).tolist()
            
            counts = Counter(self._classify_value(v) for v in values if self._classify_value(v) is not None)

            if not counts:
                schema[col] = 'string'
                continue

            schema[col] = counts.most_common(1)[0][0]

        return schema

    def create_new_table_if_required(self, Config, file, forcetablename):
        ''' Creates a new table when there is isn't one or when a new column needs to be added to the table '''
        
        if forcetablename:
            tablename = forcetablename
        else:
            tablename = self.get_tablename(os.path.basename(file))
            if os.path.getsize(file) == 0:
                return tablename

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

    def sanitize_adx_column_name(self, name: str) -> str:
        '''
        Sanitize a string so it can be safely used as an Azure Data Explorer (ADX) column name.
        '''

        name = re.sub(r"[<>\[\]{}\"'`\\]", "_", name)
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)

        if name and name[0].isdigit():
            name = f'{name}'

        return name

    def get_table_createcommand(self, file, Config, tablename):
        ''' Prepares the command for creating a table in ADX'''
        
        df = self.convert_to_dataframe(file, Config.var_sample_size, chunksize=None)

        schema = self.infer_adx_type_majority(df, Config.var_sample_size)
        
        columnstring = self.prepare_string_with_columnames(schema)

        clean_columnames = list(schema.keys())

        return f'.create-merge table {tablename} ({columnstring})', clean_columnames

    def upload_detailed_status(self, results, Config, tablename):

        table = self.create_new_table_if_required(Config, results, tablename)
        results_df = pd.DataFrame(results)

        self.launch_upload_df(results_df, table)

    def read_ingestion_properties(self, tablename):

        return IngestionProperties(
            database=self.adx_database_name,
            table=tablename,
            data_format=DataFormat.JSON,
            report_level=ReportLevel.FailuresAndSuccesses)

    def launch_upload_file(self, tablename: str, fullpath: str) -> bool:
        '''Uploads a file to adx'''
        result = {}
        start = time.time()
        ingestion_props = self.read_ingestion_properties(tablename)

        size = os.path.getsize(fullpath)
        basename = os.path.basename(fullpath)
        result['basename'] = basename
        result['size'] = size      
        result['ignored_upload'] = False 

        if size == 0:

            return {
                
                'upload_initiated': False,
                'upload_error': 'Filesize is 0'
            }

        try:
            
            self.kusto_queued.ingest_from_file(fullpath, ingestion_properties=ingestion_props)
            
            duration = time.time() - start
            result['upload_initiated_timestamp'] = start
            
            log.info(f'Successfully initiated upload request of {basename} to table {tablename}')
            
            result['upload_initiated'] = True
            result['upload_duration_in_sec'] = duration

        except Exception as e:
            log.error(f'Failed to initiate the data upload request of {basename} to table {tablename}. Error: {e}' )

            duration = time.time() - start
            result['upload_initiated'] = False
            result['upload_duration_in_sec'] = duration
            result['upload_error'] = str(e)

        return result

    def launch_upload_df(self, df, tablename: str) -> bool:
        '''Uploads a dataframe to adx'''

        ingestion_props = self.read_ingestion_properties(tablename)

        try:
            self.kusto_queued.ingest_from_dataframe(df, ingestion_properties=ingestion_props)
            log.info(f'Initiated the upload of detailed status to ADX table: {tablename}')

        except Exception as e:
            log.error(f'Failed to initiate the data upload request to table {tablename}. Error: {e}' )

    def add_hostname_to_file(self, fullpath, hostname, zipfile):
        ''' Adds hostname and sourcefilename inline to file'''

        start = time.time()
        basename = os.path.basename(fullpath)
        columns = f',"Sourcefilename":"{zipfile}","Hostname":"{hostname}"'
        replacement = columns + '}'

        try:
            for line in fileinput.input(fullpath, inplace=True):
                line = line.rstrip("\n")
                if line.endswith("}"):
                    line = line[:-1] + replacement
                print(line)
        except Exception as e:
            log.error(f'Could not add hostname as column to file {basename}. Error: {e}')
            return {
                'added_hostname_error': e 
            }
        
        duration = time.time() - start

        return {      
            'added_hostname': True,
            'added_hostname_duration': duration
        }

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