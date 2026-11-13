"""Standalone Redis 7.4 stream delivery. No in-memory substitute is used in production."""
import hashlib
import json
import time
from uuid import uuid4

from shared.common.publishing import PublicationUnavailable, QueueFull
from .contracts import Deferred, Delivery, reason_code

LEASE = """
local value=redis.call('GET',KEYS[1])
if value==ARGV[1] then redis.call('PEXPIRE',KEYS[1],ARGV[2]); return 1 end
if not value then
 redis.call('SET',KEYS[1],ARGV[1],'PX',ARGV[2],'NX'); return 1
end
return 0
"""
PUBLISH = """
local previous=redis.call('GET',KEYS[2])
if previous then if previous==ARGV[1] then return 'duplicate' else return 'conflict' end end
if redis.call('XLEN',KEYS[1])>=tonumber(ARGV[4]) then return 'full' end
redis.call('XADD',KEYS[1],'*','message_id',ARGV[2],'payload',ARGV[3])
redis.call('SET',KEYS[2],ARGV[1],'EX',ARGV[5])
return 'published'
"""
ACK = """
local count=redis.call('XACK',KEYS[1],ARGV[1],ARGV[2])
redis.call('DEL',KEYS[2]); return count
"""
DEAD = """
if not redis.call('GET',KEYS[3]) then
 if redis.call('XLEN',KEYS[2])>=tonumber(ARGV[4]) then return 'full' end
 redis.call('XADD',KEYS[2],'*','payload',ARGV[3])
 redis.call('SET',KEYS[3],'1','EX',ARGV[5])
end
redis.call('XACK',KEYS[1],ARGV[1],ARGV[2]); redis.call('DEL',KEYS[4]); return 'failed'
"""
# This entire snapshot + trim runs atomically; a new group cannot appear in the middle.
TRIM = """
local function less(a,b)
 local am,as=string.match(a,'^(%d+)%-(%d+)$'); local bm,bs=string.match(b,'^(%d+)%-(%d+)$')
 if #am~=#bm then return #am<#bm end
 if am~=bm then return am<bm end
 if #as~=#bs then return #as<#bs end
 return as<bs
end
local groups=redis.call('XINFO','GROUPS',KEYS[1]); if #groups==0 then return 0 end
local boundary=nil
for _,g in ipairs(groups) do
 local name=nil; local delivered=nil
 for i=1,#g,2 do if g[i]=='name' then name=g[i+1] end; if g[i]=='last-delivered-id' then delivered=g[i+1] end end
 local pending=redis.call('XPENDING',KEYS[1],name,'-','+',1)
 local cutoff=delivered; if #pending>0 then cutoff=pending[1][1] end
 if not boundary or less(cutoff,boundary) then boundary=cutoff end
end
return redis.call('XTRIM',KEYS[1],'MINID','=',boundary)
"""


def connect(settings):
    from redis import Redis
    if not settings.password:
        raise ValueError('redis_password_required')
    return Redis(host=settings.host, port=settings.port, password=settings.password,
                 decode_responses=True, socket_connect_timeout=3, socket_timeout=5)


class RedisJSONPublisher:
    def __init__(self, client, stream, settings):
        self.client, self.stream, self.settings = client, stream, settings

    def publish(self, identity, payload):
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        result = self.client.eval(PUBLISH, 2, self.stream, f'{self.stream}:dedup:{identity}',
            digest, identity, encoded, self.settings.capacity, self.settings.dedup_ttl)
        if result == 'full':
            raise QueueFull('stream_capacity_reached')
        if result == 'conflict':
            raise PublicationUnavailable('outbox_identity_conflict')
        return result


class RedisGroup:
    """One active ordered handler per group, pending-first recovery and bounded poison retries."""
    def __init__(self, client, stream, group, settings, consumer=None):
        self.client, self.stream, self.group, self.settings = client, stream, group, settings
        self.consumer = consumer or 'worker-' + uuid4().hex[:16]
        self.token = uuid4().hex
        self.key = 'logsentinel:group:' + hashlib.sha256(f'{stream}:{group}'.encode()).hexdigest()
        self.stage = self.key
        self.retry_prefix = self.key + ':retry:'

    def initialize(self):
        from redis.exceptions import ResponseError
        try:
            self.client.xgroup_create(self.stream, self.group, id='0-0', mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith('BUSYGROUP'):
                raise

    def lease(self):
        try:
            return bool(self.client.eval(LEASE, 1, self.key + ':leader', self.token, self.settings.lease_ms))
        except Exception:
            raise Deferred('broker_unavailable') from None

    def next(self):
        pending = self.client.xpending_range(self.stream, self.group, '-', '+', 1)
        if pending:
            first = pending[0]
            identity = first['message_id']
            # Never skip an older pending entry to process newer event-time context.
            if first['consumer'] != self.consumer and first['time_since_delivered'] < self.settings.claim_idle_ms:
                return None
            retry = self.retry(identity)
            if retry['next_at'] > time.time():
                return None
            minimum = 0 if first['consumer'] == self.consumer else self.settings.claim_idle_ms
            entries = self.client.xclaim(self.stream, self.group, self.consumer, minimum, [identity])
            return Delivery(entries[0][0], entries[0][1]) if entries else None
        result = self.client.xreadgroup(self.group, self.consumer, {self.stream: '>'}, count=1)
        return Delivery(result[0][1][0][0], result[0][1][0][1]) if result else None

    def retry(self, identity):
        value = self.client.hgetall(self.retry_prefix + identity)
        return {'attempts': int(value.get('attempts', 0)), 'next_at': float(value.get('next_at', 0))}

    def defer(self, delivery, reason, counted=False):
        key = self.retry_prefix + delivery.entry_id
        attempts = self.client.hincrby(key, 'attempts', 1) if counted else self.retry(delivery.entry_id)['attempts']
        delay = min(self.settings.retry_seconds * 2 ** max(0, attempts - 1), self.settings.max_retry_seconds)
        self.client.hset(key, mapping={'next_at': time.time() + delay, 'reason': reason_code(reason)})
        return attempts

    def ack(self, delivery):
        return self.client.eval(ACK, 2, self.stream, self.retry_prefix + delivery.entry_id, self.group, delivery.entry_id)

    def dead_letter(self, delivery, reason, failure_id, attempts):
        payload = json.dumps({'schema_version': 1, 'stream': self.stream, 'group': self.group,
            'entry_id': delivery.entry_id, 'reason': reason_code(reason), 'failure_id': failure_id,
            'attempts': attempts}, sort_keys=True)
        result = self.client.eval(DEAD, 4, self.stream, self.settings.failed_stream,
            self.key + ':failed:' + delivery.entry_id, self.retry_prefix + delivery.entry_id,
            self.group, delivery.entry_id, payload, self.settings.capacity, self.settings.dedup_ttl)
        if result == 'full':
            raise QueueFull('failed_stream_capacity_reached')

    def trim(self):
        return self.client.eval(TRIM, 1, self.stream)
