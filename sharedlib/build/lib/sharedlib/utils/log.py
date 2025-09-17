from datetime import timezone, datetime
import logging as log
import os
import sys

class LogManager:
    def __init__(self, log_directory, loglevel):
        self.logger = None
        self.log_directory = log_directory
        self.loglevel = loglevel

    def setup_logging(self):
        # Setup filenames
        root_folder = os.path.abspath(os.path.join(os.path.dirname(__name__), '..'))
        log_filename = f'{datetime.now():%Y-%m-%d_%H%M%S}' + '_log.txt'
        log_file_path = os.path.join(root_folder, self.log_directory, log_filename)

        # Create a root logger
        self.logger = log.getLogger()

        self.logger.setLevel(self.loglevel)

        # Create a formatter
        formatter = log.Formatter(fmt='%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %I:%M:%S')

        # Create stdout handler
        stdout_handler = log.StreamHandler()
        stdout_handler.setLevel(log.DEBUG)
        stdout_handler.setFormatter(formatter)

        # Create file handler
        file_handler = log.FileHandler(log_file_path)
        file_handler.setLevel(log.DEBUG)
        file_handler.setFormatter(formatter)

        # Add handlers to the logger
        if not self.logger.hasHandlers():
            self.logger.addHandler(stdout_handler)
            self.logger.addHandler(file_handler)

        # Supress warnings
        self.suppress_warning()

    def suppress_warning(self):
        for logger_name in [
            'azure.core.pipeline.policies.http_logging_policy',
            'azure.identity',
            'paramiko'
        ]:
            log.getLogger(logger_name).setLevel(log.WARNING)        

    def get_logger(self, name=None):
        if self.logger is None:
            self.setup_logging()
        return log.getLogger(name) if name else self.logger
    
    def add_file_handler(self, log_file_path):
        # Create a formatter
        formatter = log.Formatter(fmt='%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %I:%M:%S')

        # Create file handler
        file_handler = log.FileHandler(log_file_path)
        file_handler.setLevel(log.DEBUG)
        file_handler.setFormatter(formatter)

        # Add file handler to the root logger
        self.logger.addHandler(file_handler)
        return file_handler

    def remove_file_handler(self, file_handler):
        # Remove the file handler from the root logger
        self.logger.removeHandler(file_handler)
        file_handler.close()
