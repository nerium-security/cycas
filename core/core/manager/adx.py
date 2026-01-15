'''
Module for ingesting data into Azure Data Explorer (ADX).

Provides the `AdxManager` class, which authenticates to an ADX cluster,
infers table schemas from JSONL data, creates or merges tables when
required, and initiates queued ingestion from files or pandas DataFrames.
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
        '''
        Initialize the ADX manager.

        Args:
            credential: Azure credential capable of acquiring an access token
                for Kusto (e.g. DefaultAzureCredential).
            adx_cluster_uri (str): Query endpoint URI of the ADX cluster.
            adx_cluster_ingestion_uri (str): Ingestion endpoint URI of the ADX cluster.
            adx_database_name (str): Target ADX database name.
        '''

        self.credential = credential
        self.adx_database_name = adx_database_name
        self.adx_cluster_uri = adx_cluster_uri
        self.adx_cluster_ingestion_uri = adx_cluster_ingestion_uri
        self.kusto_client = None
        self.kusto_queued = None

    def authenticate(self):
        '''
        Authenticate to Azure Data Explorer and initialize clients.

        Uses the provided Azure credential to request an access token for the
        Kusto scope and builds:
            - a query client (`KustoClient`)
            - a queued ingestion client (`QueuedIngestClient`)

        Returns:
            bool: True if authentication succeeds, otherwise False.
        '''

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
        '''
        Run a short test query against the configured ADX database.

        Executes a management query ('.show operations | limit 1') with a short
        timeout to validate connectivity to the cluster and database.

        Returns:
            bool: True if the query executes successfully, otherwise False.

        Notes:
            - Assumes `authenticate()` has been called and `self.kusto_client` is set.
        '''

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
        '''
        Convert a file or in-memory input data to a pandas DataFrame.

        Supports:
            - dict: converted into a single-row DataFrame
            - list: converted into a DataFrame of records
            - filepath (str or Path): read as JSON Lines via `pd.read_json(..., lines=True)`

        Args:
            f: Input data or file path. Supported types are dict, list,
                str, or Path.
            nrows (int): Number of rows to read when loading from a file.
            chunksize (int or None): Chunk size to use for `pd.read_json`.
                If provided, pandas may return an iterator instead of a DataFrame.

        Returns:
            pandas.DataFrame or Iterator[pandas.DataFrame]: DataFrame (or chunk iterator)
            if conversion succeeds. Returns an empty DataFrame on errors or if the
            resulting DataFrame is empty.

        Notes:
            The data is loaded as object so that the columntype can be determined manually
            _classify_value

        '''

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
        '''
        Convert Python dictionaries in selected DataFrame columns into JSON strings.

        For each column listed in `dyn_columns`, values that are dictionaries are
        serialized using `json.dumps()`.

        Args:
            df (pandas.DataFrame): DataFrame to modify in place.
            dyn_columns (list[str]): Column names that may contain dict values.
        '''

        log.debug('Entered function to convert dictionaries in the dataframe to json.')

        if dyn_columns:
            for dyn_column in dyn_columns:
                try:
                    df[dyn_column] = df[dyn_column].apply(lambda x: json.dumps(x) if isinstance(x, dict) else x)
                except Exception as e:
                    log.error(f'Could not convert column {dyn_column} of dataframe to json. Error: {e}')

    def prepare_string_with_columnames(self, schema):
        ''' 
        Prepares the string with columnames and determines columntypes.

        Args:
            schema (dict[str, str]): Mapping of column name to ADX type.

        Returns:
            str: Comma-separated string of ADX column definitions:
                ['CreationTime']:date, ['PhysicalProcessorCount']:string, etc
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
        Classify a single value into an ADX column type.

        Classification rules include:
            - dict/list -> 'dynamic'
            - bool -> 'bool'
            - datetime -> 'datetime'
            - int -> 'long'
            - float -> 'datetime' if it resembles epoch seconds/ms, else 'real'
            - str -> 'datetime' if ISO-8601 parseable, else 'string'
            - None/NaN -> None (ignored)

        Args:
            value: Value to classify. May be of any type observed in a DataFrame.

        Returns:
            str or None: One of 'long', 'datetime', 'dynamic', 'string', 'real', 'bool',
            or None for missing/invalid samples.
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
        Infer ADX column types using a majority vote over sampled values.

        For each DataFrame column, inspects up to `sample_size` non-null values,
        classifies each value using `_classify_value()`, and selects the most
        common type.

        Args:
            df (pandas.DataFrame): DataFrame used for inference.
            sample_size (int): Number of non-null values to sample per column.

        Returns:
            dict[str, str]: Mapping of column name to inferred ADX type.

        Notes:
            - If no valid samples are found for a column, defaults to 'string'.
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
        '''
        Create or merge an ADX table if needed based on sample-derived schema.

        Determines the target table name, checks whether the table exists, infers
        a schema from sample data, and issues a '.create-merge table' command if:
            - the table does not exist, or
            - new columns are detected compared to the existing table schema

        Args:
            Config: Configuration object expected to provide `var_sample_size`.
            file: File path used for table naming and schema inference. If
                `forcetablename` is provided, the name is not derived from the file.
            forcetablename (str or None): Optional explicit table name.

        Returns:
            str: Name of the table used or created.
        '''
        
        if forcetablename:
            tablename = forcetablename
        else:
            tablename = self.sanitize_adx_column_name(os.path.basename(file))
            if os.path.getsize(file) == 0:
                return tablename

        table_exists, existing_columns = self.check_if_table_exists(tablename)

        cmd_createmergetable, new_columns = self.get_table_createcommand(file, Config, tablename)
            
        new_columns_exists = self.checking_if_new_columns_exists(existing_columns, new_columns)

        if not table_exists or new_columns_exists:

            self.launch_createmerge_table(cmd_createmergetable)

        return tablename
        
    def checking_if_new_columns_exists(self, existing_columns, new_columns):
        '''
        Determine whether new columns are present compared to an existing ADX table.

        Args:
            existing_columns (list[dict]): Existing ADX column metadata, expected
                to contain 'ColumnName' keys.
            new_columns (list[str]): New column names derived from sample schema.

        Returns:
            bool: True if a column in `new_columns` is not present in the existing
            schema, otherwise False.
        '''

        existing_columns_clean = [col.get('ColumnName') for col in existing_columns]

        for new_column in new_columns:
            if not new_column in existing_columns_clean:
                log.debug(f'Column does not exist yet: {new_column}')
                return True

        return False

    def sanitize_adx_column_name(self, name: str) -> str:
        '''
        Sanitize a string so it can be used as an ADX column name.

        Replaces special characters, whitespace, and non-alphanumeric characters
        with underscores.

        Args:
            name (str): Candidate column name.

        Returns:
            str: Sanitized column name.
        '''

        name = re.sub(r"[<>\[\]{}\"'`\\]", "_", name)
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)

        if name and name[0].isdigit():
            name = f'{name}'

        return name

    def get_table_createcommand(self, file, Config, tablename):
        '''
        Generate an ADX '.create-merge table' command from sample data.

        Loads sample data into a DataFrame, infers a schema using majority vote,
        and formats the ADX create-merge command string.

        Args:
            file: JSONL file path or in-memory object supported by `convert_to_dataframe()`.
            Config: Configuration object expected to provide `var_sample_size`.
            tablename (str): Target table name to include in the command.

        Returns:
            tuple[str, list[str]]: Tuple containing:
                - ADX management command string for creating/merging the table
                - List of column names derived from the inferred schema
        '''
        
        df = self.convert_to_dataframe(file, Config.var_sample_size, chunksize=None)

        schema = self.infer_adx_type_majority(df, Config.var_sample_size)
        
        columnstring = self.prepare_string_with_columnames(schema)

        clean_columnames = list(schema.keys())

        return f'.create-merge table {tablename} ({columnstring})', clean_columnames

    def upload_detailed_status(self, results, Config, tablename):
        '''
        Upload a detailed status payload into ADX.

        Ensures the target table exists (creating/merging if required), converts
        the results payload into a DataFrame, and initiates ingestion from the
        DataFrame.

        Args:
            results: In-memory status payload (typically dict or list).
            Config: Configuration object used for schema inference sampling.
            tablename (str): Table name to use or force for detailed status.
        '''

        table = self.create_new_table_if_required(Config, results, tablename)
        results_df = pd.DataFrame(results)

        self.launch_upload_df(results_df, table)

    def read_ingestion_properties(self, tablename):
        '''
        Build ingestion properties for JSON ingestion into an ADX table.

        Args:
            tablename (str): Target table name.

        Returns:
            IngestionProperties: Ingestion properties configured for JSON format
            and failure/success reporting.
        '''

        return IngestionProperties(
            database=self.adx_database_name,
            table=tablename,
            data_format=DataFormat.JSON,
            report_level=ReportLevel.FailuresAndSuccesses)

    def launch_upload_file(self, tablename: str, fullpath: str) -> bool:
        '''
        Initiate queued ingestion of a local file into ADX.

        Builds ingestion properties and calls `ingest_from_file()` using the
        queued ingestion client. This initiates ingestion but does not wait
        for completion.

        Args:
            tablename (str): Target ADX table name.
            fullpath (str): Full path to the local file to ingest.

        Returns:
            dict: Dictionary containing upload initiation metadata, including:
                - basename, size, ignored_upload
                - upload_initiated (bool)
                - upload_duration_in_sec (float)
                - upload_initiated_timestamp (float) on success
                - upload_error (str) on failure
        '''

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
        '''
        Initiate queued ingestion of a pandas DataFrame into ADX.

        Args:
            df (pandas.DataFrame): DataFrame to ingest.
            tablename (str): Target ADX table name.

        Side Effects:
            - Initiates ingestion using the queued ingestion client.

        Notes:
            - The current implementation logs errors but does not return a value.
        '''

        ingestion_props = self.read_ingestion_properties(tablename)

        try:
            self.kusto_queued.ingest_from_dataframe(df, ingestion_properties=ingestion_props)
            log.info(f'Initiated the upload of detailed status to ADX table: {tablename}')

        except Exception as e:
            log.error(f'Failed to initiate the data upload request to table {tablename}. Error: {e}' )

    def add_hostname_to_file(self, fullpath, hostname, zipfile):
        '''
        Add Hostname and Sourcefilename fields to each JSON object line in a file.

        Performs an in-place modification of a JSON Lines file by appending
        '"Sourcefilename":"<zipfile>","Hostname":"<hostname>"' to each line that
        ends with '}'.

        Args:
            fullpath (str): Path to the JSONL file to modify in place.
            hostname (str): Hostname value to add to each JSON object.
            zipfile (str): Source filename value to add to each JSON object.

        Returns:
            dict: Result dictionary containing:
                - added_hostname (bool) and added_hostname_duration (float) on success
                - added_hostname_error on failure
        '''

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
        '''
        Check whether an ADX table exists by running a zero-row query.

        Executes '<tablename> | limit 0' and inspects the returned schema.

        Args:
            tablename (str): Table name to check.

        Returns:
            tuple[bool, Any]: Tuple containing:
                - True and raw column metadata if the table exists
                - False and an empty string if the table does not exist
        '''

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
        '''
        Execute an ADX management command to create or merge a table.

        Args:
            cmd_createmergetable (str): ADX management command string,
                typically a '.create-merge table ...' command.

        Returns:
            bool: True if the command executes successfully, otherwise False.
        '''

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