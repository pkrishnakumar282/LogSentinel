"""ACK-after-commit worker orchestration and transactional outbox relay."""
from sqlalchemy.exc import SQLAlchemyError

from .contracts import Deferred, Rejected, Retryable, reason_code


class OutboxRelay:
    def __init__(self, store, publisher):
        self.store, self.publisher = store, publisher

    def flush(self, limit=100):
        count = 0
        for row in self.store.claim_outbox(limit):
            try:
                self.publisher.publish(row['id'], row['payload'])
            except Exception:
                self.store.finish_outbox(row['id'], row['lease_token'], False)
                raise Deferred('outbox_publish_unavailable') from None
            # If the process dies here, lease expiry + publisher/consumer dedup make replay safe.
            self.store.finish_outbox(row['id'], row['lease_token'], True)
            count += 1
        return count


class StreamWorker:
    def __init__(self, broker, store, handler):
        self.broker, self.store, self.handler = broker, store, handler
        self.last_reason = None

    def _quarantine(self, delivery, reason, attempts):
        reason = reason_code(reason)
        event = getattr(self.handler, 'current_event', None)
        identity = self.store.record_failure(self.broker.stage, self.broker.stream, delivery,
                                              reason, attempts, event)
        self.broker.dead_letter(delivery, reason, identity, attempts)
        self.broker.trim()
        return 'failed'

    def tick(self):
        self.last_reason = None
        if not self.broker.lease():
            return 'standby'
        delivery = self.broker.next()
        if delivery is None:
            return 'idle'
        try:
            previous_failure = self.store.failure(self.broker.stage, delivery.entry_id)
            if previous_failure:
                self.broker.dead_letter(delivery, previous_failure['reason'], previous_failure['id'],
                                        previous_failure['attempts'])
                return 'failed'
            result = self.handler(delivery)
        except (Deferred, SQLAlchemyError) as exc:
            self.last_reason = reason_code(str(exc)) if isinstance(exc, Deferred) else 'database_unavailable'
            self.broker.defer(delivery, self.last_reason, counted=False)
            return 'deferred'
        except Rejected as exc:
            self.last_reason = reason_code(str(exc))
            return self._quarantine(delivery, self.last_reason, 1)
        except Exception as exc:
            self.last_reason = reason_code(str(exc)) if isinstance(exc, Retryable) else 'processing_failed'
            attempts = self.broker.defer(delivery, self.last_reason, counted=True)
            if attempts >= self.broker.settings.max_retries:
                return self._quarantine(delivery, self.last_reason, attempts)
            return 'retrying'
        # Renew leadership before acknowledging. A lost lease leaves a committed receipt for recovery.
        if not self.broker.lease():
            return 'standby'
        self.broker.ack(delivery)
        self.broker.trim()
        return result
