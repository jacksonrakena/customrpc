import json
import logging
import sys


class MemoryCache:
    def __init__(self, name):
        self.cache = {}
        self.name = name
        self.logger = logging.getLogger('customrpc')
        self.filename = f'data/.mcache_{name}.json'
        try:
            self.load()
        except:
            pass

    def load(self):
        with open(self.filename, 'r') as f:
            self.cache = json.load(f)
            self.logger.info('Loaded cache ' + self.name + ' ('+self.filename+')')

    def put(self, key, value):
        self.cache[key] = value

    def get(self, key):
        try:
            return self.cache[key]
        except:
            return None

    def save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.cache, f)
            logging.info('Saved cache ' + self.name + ' ('+self.filename+')')
