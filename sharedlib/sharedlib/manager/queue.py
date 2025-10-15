'''
Module for dealing with messages in the
message queue of an storage account
'''

from azure.storage.queue import QueueClient, QueueMessage
from azure.core.exceptions import AzureError
from typing import Optional, List
from datetime import datetime
import logging as log
import base64
import json

log = log.getLogger(__name__)

class QueueManager:
    def __init__(self, credential, queue_url, queue_name):
        self.credential = credential
        self.queue_url = queue_url
        self.queue_name = queue_name
        self.queue_client: Optional[QueueClient] = None

    def authenticate(self) -> bool:
        ''' Authenticate and create the QueueClient.'''
        try:
            queue_endpoint = f'{self.queue_url}/{self.queue_name}'
            log.info(f'Authenticating with storage queue: {queue_endpoint}')
            self.queue_client = QueueClient(self.queue_url, self.queue_name, self.credential)
            # Test the connection
            self.queue_client.get_queue_properties()
            log.info(f'Successfully authenticated.')
            return True
        except AzureError as e:
            log.info(f'Authentication failed: {e}')
            return False

    def peek_messages(self, max_messages: int = 1):
        '''Peek at messages without dequeuing.'''
        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')
        
        messages = self.queue_client.peek_messages(max_messages=max_messages)
        return messages

    def receive_messages(self, max_messages: int = 1) -> List[QueueMessage]:
        '''Retrieve messages from the queue.'''
        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        messages = self.queue_client.receive_messages(messages_per_page=max_messages)

        decoded_messages = []
        for message in messages:

            message_content = message.get('content')
            message_content_decoded = base64.b64decode(message_content)
            message_content_decoded_ascii = message_content_decoded.decode('ascii')
            decoded_messages.append(message_content_decoded_ascii)      
        return decoded_messages

    def delete_message(self, message: QueueMessage):
        '''
        Delete a message from the queue.
        '''
        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        self.queue_client.delete_message(message)

    def send_message(self, zipfile: str, source_name) -> bool:
        '''Send a message to the queue. Message content (must be <= 64KB)'''
        if not self.queue_client:
            raise ValueError('Queue client not authenticated. Call authenticate() first.')

        try:
            content = json.dumps({'triagepackage': zipfile, 'source_name' : source_name})
            message_bytes = content.encode('utf-8')
            base64_bytes = base64.b64encode(message_bytes)
            base64_message = base64_bytes.decode('utf-8')
            self.queue_client.send_message(base64_message)
            log.info(f'Sent to queue: {self.queue_name}.')

            log.debug(f'Sent message to queue: {content}')
            return True
        except AzureError as e:
            log.info(f'Failed to send message to queue: {e}')
            return False