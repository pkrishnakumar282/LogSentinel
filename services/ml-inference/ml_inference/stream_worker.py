"""Ordered durable inference consumer, separate from exploratory HTTP contexts."""
import time

from sqlalchemy.exc import SQLAlchemyError

from ml.features import FeatureExtractor, FeatureStateError
from shared.common.publishing import fingerprint
from shared.pipeline.contracts import Deferred, Rejected, Retryable, StaleCheckpoint
from shared.schemas import InvalidRecord, NormalizedLog
from shared.schemas.logs import utc_time

from .engine import Context, InferenceCapacity, InferenceConflict, InferenceUnavailable, prepare_event


class InferenceHandler:
    def __init__(self, engine, store, broker):
        self.engine, self.store, self.broker = engine, store, broker
        self.revision = None
        self.generation = None
        self.current_event = None

    def restore(self, checkpoint):
        extractor = FeatureExtractor(self.engine.loaded.bundle.feature_config)
        state = checkpoint['state']
        if state:
            extractor.started_at = utc_time(state['started_at'])
            records = self.store.history(self.broker.stage, checkpoint, extractor.config.window_seconds)
            try:
                extractor.transform(records)
            except FeatureStateError:
                raise Deferred('checkpoint_restore_unavailable') from None
            extractor.current_time = utc_time(state['last_timestamp'])
        with self.engine.lock:
            self.engine.contexts['stream'] = Context(extractor, {}, time.monotonic())
        self.revision = checkpoint['revision']
        self.generation = self.engine.generation

    def __call__(self, delivery):
        self.current_event = None
        try:
            event = prepare_event(NormalizedLog(**delivery.payload()))
        except (InvalidRecord, ValueError, TypeError, AttributeError):
            raise Rejected('invalid_normalized_event') from None
        self.current_event = event
        previous = self.store.receipt(self.broker.stage, event.event_id)
        if previous:
            if previous['fingerprint'] != fingerprint(event):
                raise Rejected('event_id_conflict')
            self.revision = None
            return 'duplicate'
        if self.engine.loaded is None:
            raise Deferred('approved_model_unavailable')
        checkpoint = self.store.checkpoint(self.broker.stage)
        if checkpoint['revision'] != self.revision or self.engine.generation != self.generation:
            self.restore(checkpoint)
        try:
            prediction = self.engine.predict([event], 'stream')[0]
            context = self.engine.contexts['stream']
            state = {'started_at': context.features.started_at.isoformat(),
                     'last_timestamp': context.features.current_time.isoformat(),
                     'feature_version': self.engine.loaded.bundle.feature_version,
                     'model_version': prediction['model_version']}
            if not self.broker.lease():
                raise Deferred('consumer_leadership_lost')
            result = self.store.save_prediction(self.broker.stage, event, prediction,
                self.engine.loaded, state, checkpoint['revision'])
            if result['status'] == 'duplicate':
                self.revision = None
            else:
                self.revision = result['revision']
            return result['status']
        except InferenceConflict as exc:
            self.revision = None
            raise Rejected(str(exc)) from None
        except InferenceCapacity:
            self.revision = None
            raise Deferred('feature_window_capacity_reached') from None
        except InferenceUnavailable as exc:
            self.revision = None
            if self.engine.loaded is None:
                raise Deferred('approved_model_unavailable') from None
            raise Retryable('model_prediction_failed') from None
        except BaseException:
            # On any failed transaction, next delivery restores the last committed feature context.
            self.revision = None
            self.engine.reset_context('stream')
            raise
