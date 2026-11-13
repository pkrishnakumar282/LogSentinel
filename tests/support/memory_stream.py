"""Explicit test-only broker for failure injection. Not a Redis implementation or production proof."""
from collections import OrderedDict
import json
from types import SimpleNamespace

from shared.pipeline.contracts import Delivery


class MemoryGroup:
    def __init__(self, stream='test-stream', stage='test-stage', max_retries=3):
        self.stream, self.stage = stream, stage
        self.settings = SimpleNamespace(max_retries=max_retries)
        self.entries = OrderedDict()
        self.next_index = 0
        self.acked, self.dead = [], []
        self.failures = {}
        self.leader = True
        self.fail_ack = False

    def add(self, payload):
        self.next_index += 1
        delivery = Delivery(f'{self.next_index}-0', {'payload': json.dumps(payload)})
        self.entries[delivery.entry_id] = delivery
        return delivery

    def lease(self):
        return self.leader

    def next(self):
        return next(iter(self.entries.values()), None)

    def retry(self, identity):
        return {'attempts': self.failures.get(identity, 0), 'next_at': 0}

    def defer(self, delivery, reason, counted=False):
        if counted:
            self.failures[delivery.entry_id] = self.failures.get(delivery.entry_id, 0) + 1
        return self.failures.get(delivery.entry_id, 0)

    def ack(self, delivery):
        if self.fail_ack:
            self.fail_ack = False
            raise ConnectionError('injected acknowledgement loss')
        self.entries.pop(delivery.entry_id, None)
        self.acked.append(delivery.entry_id)

    def dead_letter(self, delivery, reason, failure_id, attempts):
        self.dead.append({'entry_id': delivery.entry_id, 'reason': reason, 'failure_id': failure_id,
                          'attempts': attempts})
        self.ack(delivery)

    def trim(self):
        return 0


class MemoryPublisher:
    def __init__(self, group):
        self.group, self.sent = group, {}
        self.fail = False

    def publish(self, identity, payload):
        if self.fail:
            raise ConnectionError('injected publisher outage')
        if identity in self.sent:
            assert self.sent[identity] == payload
            return 'duplicate'
        self.sent[identity] = payload
        self.group.add(payload)
        return 'published'
