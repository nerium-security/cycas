from azure.kusto.data import KustoClient, KustoConnectionStringBuilder, DataFormat, ClientRequestProperties
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties, ReportLevel, ReportMethod
from azure.kusto.ingest.status import KustoIngestStatusQueues
from datetime import timedelta
import pandas as pd
import logging as log
import re
import json
import time
import os

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

    def convert_to_dataframe(self, f, nrows):
        '''Convert a file or in-memory data to a Pandas DataFrame.'''

        try:

            df = pd.read_json(f, lines=True, nrows=nrows)
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

        if not self.kusto_client:
            raise ValueError('Not authenticated with ADX. Call authenticate() first.')

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
        tablename = re.sub('%2F', '_', filename.replace('.json', '').replace('.csv', '').replace('.CSV', '').replace('.JSON', ''))
        tablename = re.sub('[^0-9a-zA-Z_-]', '_', tablename)
        log.debug(f'Extracted the following name which will be used to create the table: {tablename}.')
        return tablename

    def get_table_createcommand(self, columnames, tablename, dyn_columns, int_columns):
        if not any([dyn_columns, int_columns]):
            dyn_columns = []
            int_columns = []

        time_columns = ['time', '0x30', '0x10', 'date']
        stringbuilder = ''

        for columname in columnames:
            columname = columname.replace('>', '').replace('<', '')
            stringbuilder += f"[\'{columname}\']:"
            
            if any(item in columname.lower() for item in time_columns):
                stringbuilder += 'date, '
            elif columname in dyn_columns:
                stringbuilder += 'dynamic, '
            elif columname in int_columns:
                stringbuilder += 'int, '
            else:
                stringbuilder += 'string, '
        
        create = f'.create-merge table {tablename} ({stringbuilder[:-2]})'
        return create

    def launch_upload_data(self, tablename: str, fullpath: str) -> bool:
        '''Uploads a file to adx'''

        if not self.kusto_client or not self.kusto_queued:
            log.error('Clients are not authenticated. Please run the authenticate method first.')
            return False

        ingestion_properties = IngestionProperties(
            database=self.adx_database_name,
            table=tablename,
            data_format=DataFormat.JSON,
            report_level=ReportLevel.FailuresAndSuccesses
        )
        basename = os.path.basename(fullpath)
        try:
            self.kusto_queued.ingest_from_file(fullpath, ingestion_properties=ingestion_properties)
            log.info(f'Successfully initiated the data upload request of file {basename}')

            return True
        except Exception as e:
            log.error(f'Failed to initiate the data upload request of file {basename}. Error: {e}' )
            return False

    def check_if_table_exists(self, tablename):
        query = f'{tablename} | limit 0'
        try:
            response_query = self.kusto_client.execute_query(self.adx_database_name, query)
            raw_columns = response_query.primary_results[0].raw_columns
            if raw_columns:
                return True, raw_columns
        except Exception as e:
            log.info('Table %s does not exist.' %tablename)
            return False, None

    def launch_createmerge_table(self, tablename, cmd_createmergetable):
        table_exists, columns_current = self.check_if_table_exists(tablename)

        new_columns = []
        if table_exists:
            columns_current = ', '.join([f"['{column['ColumnName']}']:{column['ColumnType']}" for column in columns_current])
            cmd_createmergetable_columns = re.search(r'\((.*?)\)', cmd_createmergetable).group(1)
            cmd_createmergetable_fields = [field.strip() for field in cmd_createmergetable_columns.split(',')]

            if columns_current != cmd_createmergetable_columns:
                for cmd_createmergetable_field in cmd_createmergetable_fields:
                    column_name, column_type = cmd_createmergetable_field.split(':')
                    if column_name not in columns_current:
                        new_columns.append(cmd_createmergetable_field)
                
            if new_columns:
                cmd_createmergetable = cmd_createmergetable.replace(')', '') + ', ' + ', '.join(new_columns) + ')'

        if new_columns or not table_exists:
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