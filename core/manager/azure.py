'''
Module for interacting with Azure resources required for Azure Data Explorer.

This module uses Azure management SDK clients and performs interactive
selection via stdin for subscription, cluster, and database selection.
'''

from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import SubscriptionClient
from azure.mgmt.kusto import KustoManagementClient
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.core.exceptions import ClientAuthenticationError
import logging as log
import sys

log = log.getLogger(__name__)

class AzureManager:
    def __init__(self):
        '''
        Initialize the AzureManager.

        Sets internal client references to None until authentication and
        discovery methods are called.
        '''

        self.kusto_client = None
        self.credential = None
        self.sub_client = None

    def authenticate(self):
        '''
        Authenticate to Azure and initialize the subscription client.

        Uses DefaultAzureCredential with interactive browser credential enabled.
        On success, stores the credential and initializes SubscriptionClient.

        Returns:
            Any or None: The Azure credential object if authentication succeeds,
            otherwise None.
        '''

        log.info('Attempting to authenticate to Azure.')
        try:
            self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            self.sub_client = SubscriptionClient(self.credential)
            self.credential.get_token('https://management.azure.com/.default')
            log.info('Successfully authenticated with Azure.')
            return self.credential
        except ClientAuthenticationError:
            log.error('Azure authentication failed. Try running `az login` on your terminal. Exiting script.')
            sys.exit(1)
        except Exception as e:
            log.error(f'Authentication failed: {e}')
            sys.exit(1)

    def find_subscriptions(self):
        '''
        List Azure subscriptions available to the authenticated identity.

        Returns:
            list[tuple[str, str]]: List of (subscription_id, display_name) tuples.

        Notes:
            - Assumes `authenticate()` has been called and `self.sub_client` is set.
        '''

        log.info('Attempting to find Azure subscriptions.')
        subscriptions = []
        for sub in self.sub_client.subscriptions.list():
            subscriptions.append((sub.subscription_id, sub.display_name))
            log.info(f'Found subscription: {sub.subscription_id} {sub.display_name}')
        return subscriptions
    
    def select_subscription(self):
        '''
        Interactively select a subscription from those available.

        Prints available subscriptions and prompts the user to select one by
        number using stdin.

        Returns:
            str or None: Selected subscription ID if a selection is made,
            otherwise None.

        '''

        subs = self.find_subscriptions()
        if not subs:
            log.error('No subscriptions found.')
            return None

        print('\nAvailable Subscription(s):')
        for idx, (sid, name) in enumerate(subs, start=1):
            print(f"{idx}. {name} ({sid})")

        while True:
            try:
                choice = int(input('\nSelect a subscription by number: '))
                if 1 <= choice <= len(subs):
                    selected = subs[choice - 1]
                    log.info(f'Selected: {selected[1]} ({selected[0]})')
                    return selected[0]
                else:
                    print(f'Please enter a number between 1 and {len(subs)}.')
            except ValueError:
                print('Invalid input. Please enter a number.')

    def get_resource_group_from_cluster(self, cluster):
        '''
        Extract the resource group name from a cluster resource ID string.

        Args:
            cluster (str): Azure resource ID for a Kusto cluster.

        Returns:
            str or None: Resource group name if it can be parsed, otherwise None.
        '''

        try:
            parts = cluster.split('/')
            rg_index = parts.index('resourceGroups')

            return parts[rg_index + 1]

        except (ValueError, IndexError, AttributeError):
            return None

    def find_clusters(self, selected_sub):
        '''
        List Azure Data Explorer (Kusto) clusters in a subscription.

        Creates a KustoManagementClient for the provided subscription and
        enumerates available clusters, returning a simplified dictionary per
        cluster with key properties.

        Args:
            selected_sub (str): Azure subscription ID.

        Returns:
            list[dict]: List of cluster dictionaries including name, location,
            uri, ingestion uri, state (if present), id, and resource_group.
        '''

        self.kusto_client = KustoManagementClient(self.credential, selected_sub)

        clusters = []
        for cluster in self.kusto_client.clusters.list():
            clusters.append({
                'name': cluster.name,
                'location': cluster.location,
                'uri': cluster.uri,
                'data_ingestion_uri': cluster.data_ingestion_uri,
                'state': getattr(cluster, 'state', None),
                'id' : cluster.id,
                'resource_group': self.get_resource_group_from_cluster(cluster.id)
            })
            log.info(f'Found cluster: {cluster.name}')
        return clusters
    
    def select_cluster(self, selected_sub):
        '''
        Interactively select a Kusto cluster from the selected subscription.

        Lists clusters and prompts the user to select one by number using stdin.

        Args:
            selected_sub (str): Azure subscription ID.

        Returns:
            dict or None: Selected cluster dictionary if chosen, otherwise None.
        '''

        clusters = self.find_clusters(selected_sub)

        if not clusters:
            log.error('No Azure Data Explorer clusters found.')
            return None
        
        print('\nAvailable Cluster(s):')
        for idx, cluster in enumerate(clusters, start=1):
            print(f"{idx}. {cluster['name']} | {cluster['location']} | {cluster['uri']}")       

        while True:
            try:
                choice = int(input('\nSelect a cluster by number: '))
                if 1 <= choice <= len(clusters):
                    selected = clusters[choice - 1]
                    log.info(f"Selected: {selected['name']} ({selected['uri']})")
                    return selected
                else:
                    print(f'Please enter a number between 1 and {len(clusters)}.')
            except ValueError:
                print('Invalid input. Please enter a number.')

    def find_databases(self, selected_cluster):
        '''
        List databases for a selected Kusto cluster.

        Uses the KustoManagementClient to list databases in the cluster.
        Attempts to normalize the database name by splitting on '/' when
        possible.

        Args:
            selected_cluster (dict): Cluster dictionary as returned by
                `find_clusters()` or `select_cluster()`. Must include 'name'
                and 'resource_group'.

        Returns:
            list[dict] or None: List of database dictionaries on success,
            an empty list if cluster identifiers are missing, or None if an
            exception occurs during retrieval.
        '''

        databases = []
        cluster_name = selected_cluster.get('name')
        resource_group = selected_cluster.get('resource_group')

        if not resource_group:
            log.error('Could not determine resource group for the cluster.')
            return []
        
        if not cluster_name:
            log.error('Could not determine name for the cluster.')
            return []
        
        try:
            for db in self.kusto_client.databases.list_by_cluster(resource_group, cluster_name):

                try:
                    db_name = db.name.split('/')[1]
                except (AttributeError, IndexError):
                    db_name = ''

                databases.append({
                    'name': db_name,
                    'fullname': db.name,
                    'location': db.location,
                    'type': db.type,
                    'kind': db.kind
                })

            return databases
        
        except Exception as e:
            log.error(f'Could not retrieve database. Error: {e}')
            return None

    def select_database(self, selected_cluster):
        '''
        Interactively select a database from a selected Kusto cluster.

        Lists databases and prompts the user to select one by number using stdin.

        Args:
            selected_cluster (dict): Cluster dictionary that identifies the
                cluster to list databases for.

        Returns:
            dict or None: Selected database dictionary if chosen, otherwise None.
        '''

        databases = self.find_databases(selected_cluster)
        if not databases:
            log.error('No databases found in the selected cluster.')
            return None

        print('\nAvailable Database(s):')
        for idx, db in enumerate(databases, start=1):
            print(f"{idx}. {db['name']} ({db['location']})")

        while True:
            try:
                choice = int(input('\nSelect a database by number: '))
                if 1 <= choice <= len(databases):
                    selected = databases[choice - 1]
                    print(f"Selected: {selected['name']}")
                    log.info(f"Selected database: {selected['name']}")
                    return selected
                else:
                    print(f'Please enter a number between 1 and {len(databases)}.')
            except ValueError:
                print('Invalid input. Please enter a number.')