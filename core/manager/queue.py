'''
Module for interacting with an Azure Storage Queue.

Provides functionality to authenticate against an Azure Storage Queue,
send messages, receive and peek messages, delete messages, and encode
or decode queue message payloads.

Messages are encoded as Base64-encoded JSON strings to comply with
Azure Queue Storage message requirements.
'''

from azure.storage.queue import QueueClient, QueueMessage
from azure.core.exceptions import AzureError
from typing import Optional, List
from datetime import datetime
import logging as log
import base64
import json
import uuid

log = log.getLogger(__name__)

class QueueManager:
    def __init__(self, credential, queue_url, queue_name):
        '''
        Initialize the QueueManager.

        Args:
            credential: Azure credential used to authenticate with
                Azure Queue Storage.
            queue_url (str): Base URL of the storage account queue endpoint.
            queue_name (str): Name of the queue to interact with.
        '''

        self.credential = credential
        self.queue_url = queue_url
        self.queue_name = queue_name
        self.queue_client: Optional[QueueClient] = None

    def authenticate(self) -> bool:
        '''
        Authenticate and initialize the Azure Queue client.

        Creates a QueueClient, ensures the queue exists, and validates
        the connection by retrieving queue properties.

        Returns:
            bool: True if authentication succeeds, otherwise False.
        '''
        try:

            queue_endpoint = f'{self.queue_url}/{self.queue_name}'
            log.info(f'Authenticating with storage queue: {queue_endpoint}')
            self.queue_client = QueueClient(self.queue_url, self.queue_name, self.credential)

            # Test permissions
            permissions_ok = self.check_permissions()

            if permissions_ok:
                # create the queue
                self.create_queue_if_not_exists(self.queue_name)

                log.info(f'Successfully authenticated.')
                
                return True
        except AzureError as e:
            log.info('Authentication failed.')
            log.debug(f'Error: {e}')
            return False

    def check_permissions(self) -> bool:
        '''
        Validate permissions by performing a full message lifecycle test:
        creates a test queue, sends a message, reads it, deletes it,
        and removes the test queue.
        Returns:
            bool: True if all operations succeed, otherwise False.
        '''

        test_queue_name = f'permission-test-{uuid.uuid4().hex[:8]}'
        test_message = 'permission-check'

        try:
            log.info(f'Running permissions check with test queue: {test_queue_name}')
            test_client = QueueClient(self.queue_url, test_queue_name, self.credential)

            # 1. Create queue — requires "Storage Queue Data Contributor"
            test_client.create_queue()
            log.debug('Create queue')

            # 2. Send message — requires "Storage Queue Data Sender" or higher
            test_client.send_message(test_message)
            log.debug('Send message')

            # 3. Receive message — requires "Storage Queue Data Reader" or higher
            messages = test_client.receive_messages(messages_per_page=1)
            message = next(messages)
            assert message.content == test_message, 'Message content mismatch'
            log.debug('Receive message')

            # 4. Delete message — requires "Storage Queue Data Contributor"
            test_client.delete_message(message)
            log.debug('Delete message')

            # 5. Delete queue — requires "Storage Queue Data Contributor"
            test_client.delete_queue()
            log.debug('Delete queue')

            log.info(f'Permissions check for {self.queue_name} passed.')
            return True

        except Exception as e:
            log.error(
                'Permission check failed. '
                'Did you add "Storage Queue Data Contributor" permissions '
                f'to {self.queue_url} ?'
                )

        finally:
            # Best-effort cleanup in case of mid-test failure
            try:
                test_client.delete_queue()
            except Exception:
                pass

    def create_queue_if_not_exists(self, queue_name: str):
        '''
        Create the queue if it does not already exist.

        Args:
            queue_name (str): Name of the queue to create.
        '''

        try:
            self.queue_client.create_queue()
            log.info(f'Queue \'{queue_name}\' created.')

        except:
            log.info(f'Queue \'{queue_name}\' already exists.')

        return

    def peek_messages(self, max_messages: int = 1):
        '''
        Peek at messages in the queue without dequeuing them.

        Args:
            max_messages (int): Maximum number of messages to peek.

        Returns:
            Iterable[QueueMessage]: Peeked queue messages.

        Raises:
            ValueError: If the queue client is not authenticated.
        '''

        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')
        
        messages = self.queue_client.peek_messages(max_messages=max_messages)
        return messages

    def receive_messages(self, max_messages: int = 1) -> List[QueueMessage]:
        '''
        Retrieve messages from the queue.

        Messages are dequeued and become invisible for the queue's
        visibility timeout period.

        Args:
            max_messages (int): Maximum number of messages to retrieve.

        Returns:
            list[QueueMessage]: Retrieved queue messages.

        Raises:
            ValueError: If the queue client is not authenticated.
        '''
        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        messages = self.queue_client.receive_messages(messages_per_page=max_messages)

        undecoded_messages = []
        for message in messages:

            undecoded_messages.append(message)

        return undecoded_messages

    def delete_message(self, message):
        '''
        Delete a message from the queue.

        Args:
            message (QueueMessage): Message to delete.

        Raises:
            ValueError: If the queue client is not authenticated.
        '''

        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        self.queue_client.delete_message(message)

    def send_message(self, zipfile: str, source_name: str) -> bool:
        '''
        Send a message to the queue. Message content (must be <= 64KB)

        The message payload is encoded as a Base64-encoded JSON string
        containing the zipfile name and source name.

        Args:
            zipfile (str): Name of the zipfile to include in the message.
            source_name (str): Source identifier to include in the message.

        Returns:
            bool: True if the message is sent successfully, otherwise False.

        Raises:
            ValueError: If the queue client is not authenticated.
        '''

        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        try:
            base64_message, content = encode_messsage(zipfile, source_name)
            self.queue_client.send_message(base64_message)
            log.info(f'Sent to queue: {self.queue_name}.')

            log.debug(f'Sent message to queue: {content}')
            return True
        except AzureError as e:
            log.info(f'Failed to send message to queue: {e}')
            return False

def decode_message(message):
    '''
    Decode a Base64-encoded queue message.

    Args:
        message (dict): Queue message containing a Base64-encoded
            'content' field.

    Returns:
        str: Decoded message content as an ASCII string.
    '''

    message_content = message.get('content')
    message_content_decoded = base64.b64decode(message_content)
    message_content_decoded_ascii = message_content_decoded.decode('ascii')

    return message_content_decoded_ascii

def encode_messsage(zipfile: str, source_name: str):
    '''
    Encode a message for sending to Azure Queue Storage.

    The message content is serialized as JSON and Base64-encoded
    to comply with Azure Queue Storage requirements.

    Args:
        zipfile (str): Zipfile name to include in the message.
        source_name (str): Source name to include in the message.

    Returns:
        tuple[str, str]: Tuple containing:
            - Base64-encoded message string.
            - Original JSON message content.
    '''

    content = json.dumps({'triagepackage': zipfile, 'source_name' : source_name})
    message_bytes = content.encode('utf-8')
    base64_bytes = base64.b64encode(message_bytes)
    message_output = base64_bytes.decode('utf-8')

    return message_output, content