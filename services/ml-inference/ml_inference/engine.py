"""Atomic model swaps and transactional, bounded context windows without training logic."""
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import math
import threading
import time
from uuid import UUID

from ml.features import FeatureExtractor, FeatureStateError
from ml.features.causal import event_digest
from shared.common.privacy import is_authentication_record
from shared.common.redaction import redact
from shared.schemas import InvalidRecord, NormalizedLog
from shared.schemas.logs import utc_time

from .config import InferenceSettings
from .loader import ModelLoadError, RegistryLoader


class InferenceUnavailable(RuntimeError):
    pass


class InferenceConflict(ValueError):
    pass


class InferenceCapacity(RuntimeError):
    pass


def prepare_event(event: NormalizedLog) -> NormalizedLog:
    if is_authentication_record(event.service, event.message):
        raise InvalidRecord('sensitive_auth_record')
    synthetic = event.metadata.get('synthetic', False)
    if type(synthetic) is not bool:
        raise InvalidRecord('invalid_synthetic_flag')
    return replace(event, event_id=str(UUID(event.event_id)), timestamp=utc_time(event.timestamp).isoformat(),
                   message=redact(event.message), template=redact(event.template),
                   metadata={'synthetic': synthetic, 'redaction_version': '1'})


@dataclass
class Context:
    features: FeatureExtractor
    responses: dict = field(default_factory=dict)
    touched: float = field(default_factory=time.monotonic)


class InferenceEngine:
    def __init__(self, settings: InferenceSettings, loader=None):
        self.settings = settings
        self.active_target = settings
        self.loader = loader or RegistryLoader()
        self.lock = threading.RLock()
        self.reload_lock = threading.Lock()
        self.loaded = None
        self.contexts = {}
        self.generation = 0
        self.last_load_error = None
        self.last_load_at = None
        self.loading = False
        self.stats = {'requests': 0, 'events_predicted': 0, 'anomalies_predicted': 0,
                      'duplicate_responses': 0, 'prediction_failures': 0, 'reload_failures': 0}
        self.latencies_ms = deque(maxlen=256)

    def reload(self, model_version: str | None = None, model_alias: str | None = None) -> dict:
        if model_version is not None and model_alias is not None:
            raise ValueError('choose_version_or_alias')
        target = self.active_target
        if model_version is not None:
            target = replace(target, model_version=model_version, model_alias='')
        elif model_alias is not None:
            target = replace(target, model_version='', model_alias=model_alias)
        if not self.reload_lock.acquire(blocking=False):
            raise InferenceConflict('model_reload_in_progress')
        with self.lock:
            self.loading = True
        try:
            candidate = self.loader.load(target)
            with self.lock:
                changed = (self.loaded is None or (candidate.name, candidate.version, candidate.package_sha256)
                           != (self.loaded.name, self.loaded.version, self.loaded.package_sha256))
                if changed:
                    self.loaded = candidate
                    self.contexts.clear()
                    self.generation += 1
                self.active_target = target
                self.last_load_error = None
                self.last_load_at = datetime.now(UTC).isoformat()
        except Exception as exc:
            with self.lock:
                if (isinstance(exc, ModelLoadError) and exc.definitive and self.loaded is not None
                        and exc.resolved_version == self.loaded.version):
                    # A revoked or corrupted current version must not keep serving as "last healthy".
                    self.loaded = None
                    self.contexts.clear()
                    self.generation += 1
                self.last_load_error = str(exc) if isinstance(exc, ModelLoadError) else 'model_loading_failed' 
                self.stats['reload_failures'] += 1
                self.last_load_at = datetime.now(UTC).isoformat()
        finally:
            with self.lock:
                self.loading = False
            self.reload_lock.release()
        return self.health()

    def health(self) -> dict:
        with self.lock:
            loaded = self.loaded
            return {'status': 'ready' if loaded else 'unavailable', 'loading': self.loading,
                    'model_name': loaded.name if loaded else self.settings.model_name,
                    'model_version': loaded.version if loaded else None,
                    'approval_scope': loaded.scope if loaded else None,
                    'serving_scope': self.settings.scope,
                    'feature_version': loaded.bundle.feature_version if loaded else None,
                    'threshold': self.threshold() if loaded else None,
                    'threshold_source': 'environment_override' if self.settings.threshold is not None else 'artifact',
                    'evaluation_applies_to_threshold': loaded is not None and self.settings.threshold is None,
                    'generation': self.generation, 'active_contexts': len(self.contexts),
                    'last_load_error': self.last_load_error, 'last_load_at': self.last_load_at,
                    'statistics': dict(self.stats),
                    'last_batch_ms': self.latencies_ms[-1] if self.latencies_ms else None}

    def threshold(self):
        return self.settings.threshold if self.settings.threshold is not None else self.loaded.bundle.threshold

    def reset_context(self, context_id: str) -> bool:
        with self.lock:
            return self.contexts.pop(context_id, None) is not None

    def predict(self, events, context_id: str = 'default') -> list[dict]:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', context_id):
            raise InvalidRecord('invalid_context_id')
        events = list(events)
        if not 1 <= len(events) <= self.settings.max_batch:
            raise InvalidRecord('invalid_batch_size')
        events = [prepare_event(event) for event in events]
        identities = {}
        for event in events:
            digest = event_digest(event)
            if event.event_id in identities and identities[event.event_id] != digest:
                raise InferenceConflict('event_id_conflict')
            identities[event.event_id] = digest
        started = time.perf_counter()
        with self.lock:
            self.stats['requests'] += 1
            if self.loaded is None:
                raise InferenceUnavailable('approved_model_unavailable')
            now = time.monotonic()
            for key in list(self.contexts):
                if now - self.contexts[key].touched > self.settings.context_ttl_seconds:
                    del self.contexts[key]
            existing = self.contexts.get(context_id)
            if existing is None:
                if len(self.contexts) >= self.settings.max_contexts:
                    raise InferenceCapacity('context_capacity_reached')
                existing = Context(FeatureExtractor(self.loaded.bundle.feature_config))
            # Clone mutable containers only. Cached rows and frozen event values are never mutated.
            transaction = existing.features.fork()
            try:
                rows = transaction.transform(events)
                responses = {key: value for key, value in existing.responses.items()
                             if key not in identities or (key in existing.features.cache
                                 and existing.features.cache[key][0] == identities[key])}
                new_rows, new_events = [], []
                pending_ids = set()
                for event, row in zip(events, rows, strict=True):
                    if event.event_id not in responses and event.event_id not in pending_ids:
                        pending_ids.add(event.event_id)
                        new_events.append(event)
                        new_rows.append(row)
                scores = self.loaded.bundle.score_rows(new_rows)
                if len(scores) != len(new_events) or any(not math.isfinite(value) for value in scores):
                    raise InferenceUnavailable('invalid_model_scores')
                threshold = self.threshold()
                for event, row, score in zip(new_events, new_rows, scores, strict=True):
                    responses[event.event_id] = {
                        'event_id': event.event_id, 'prediction': 'anomaly' if score > threshold else 'normal',
                        'is_anomaly': score > threshold, 'anomaly_score': score,
                        'model_name': self.loaded.name, 'model_version': self.loaded.version,
                        'threshold': threshold, 'context_warm': bool(row['window_is_warm']),
                        'feature_version': self.loaded.bundle.feature_version,
                        'threshold_source': 'environment_override' if self.settings.threshold is not None else 'artifact',
                    }
                output = [dict(responses[event.event_id]) for event in events]
            except FeatureStateError as exc:
                self.stats['prediction_failures'] += 1
                if str(exc) == 'feature_window_capacity_reached':
                    raise InferenceCapacity(str(exc)) from None
                raise InferenceConflict(str(exc)) from None
            except Exception:
                self.stats['prediction_failures'] += 1
                raise InferenceUnavailable('prediction_failed') from None
            # Commit only after every event scored successfully. Expired prediction cache entries go too.
            self.contexts[context_id] = Context(transaction, {key: value for key, value in responses.items()
                                                            if key in transaction.cache}, now)
            self.stats['events_predicted'] += len(new_events)
            self.stats['anomalies_predicted'] += sum(responses[event.event_id]['is_anomaly'] for event in new_events)
            self.stats['duplicate_responses'] += len(events) - len(new_events)
            self.latencies_ms.append((time.perf_counter() - started) * 1000)
            return output
