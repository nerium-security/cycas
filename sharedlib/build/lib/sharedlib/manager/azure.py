from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import SubscriptionClient
from azure.mgmt.kusto import KustoManagementClient
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
import logging as log

log = log.getLogger(__name__)

class AzureManager:
    def __init__(self):
        self.kusto_client = None
        self.credential = None
        self.sub_client = None

    def authenticate(self):
        log.info('Attempting to authenticate to Azure.')
        try:
            self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
            self.sub_client = SubscriptionClient(self.credential)
            #self.token = self.credential.get_token('https://management.azure.com/.default', process_timeout=5)
            log.info('Successfully authenticated.')
            return self.credential
        except Exception as e:
            log.error(f'Authentication failed: {e}')
            return

    def find_subscriptions(self):
        log.info('Attempting to find Azure subscriptions.')
        subscriptions = []
        for sub in self.sub_client.subscriptions.list():
            subscriptions.append((sub.subscription_id, sub.display_name))
            log.info(f'Found subscription: {sub.subscription_id} {sub.display_name}')
        return subscriptions
    
    def select_subscription(self):
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
        try:
            parts = cluster.split('/')
            rg_index = parts.index('resourceGroups')

            return parts[rg_index + 1]

        except (ValueError, IndexError, AttributeError):
            return None

    def find_clusters(self, selected_sub):

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