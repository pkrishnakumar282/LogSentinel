"""Serializable fitted pipeline and stateful scoring contract, shared with future inference."""
from dataclasses import dataclass
import importlib.metadata
import math
import platform
import threading

from ml.features import FeatureConfig, FeatureExtractor, FEATURE_VERSION

MODEL_NAME = "logsentinel-model"


def runtime_versions() -> dict:
    return {"python": platform.python_version(), **{
        name: importlib.metadata.version(name)
        for name in ("scikit-learn", "numpy", "pandas", "scipy", "joblib", "threadpoolctl")}}


@dataclass
class ModelBundle:
    pipeline: object
    threshold: float
    variant: str
    feature_config: FeatureConfig
    dataset_version: str
    candidate_id: str
    dependencies: dict
    feature_version: str = FEATURE_VERSION
    model_name: str = MODEL_NAME

    def score_rows(self, rows: list[dict]) -> list[float]:
        import numpy as np
        import pandas as pd
        from ml.features.contract import FEATURE_COLUMNS
        if self.feature_version != FEATURE_VERSION:
            raise ValueError("unsupported_feature_version")
        if not rows:
            return []
        frame = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
        # Higher = more unusual, explicitly not a calibrated probability.
        scores = -self.pipeline.score_samples(frame)
        if not np.isfinite(scores).all():
            raise ValueError("nonfinite_model_scores")
        return [float(value) for value in scores]

    def new_session(self, warmup=()):
        session = ScoringSession(self)
        for event in warmup:
            session.features.transform_one(event)
        return session


class ScoringSession:
    """One ordered stream per session; single/batch calls share exactly the same state."""
    def __init__(self, bundle: ModelBundle):
        if not math.isfinite(bundle.threshold):
            raise ValueError("invalid_model_threshold")
        self.bundle = bundle
        self.features = FeatureExtractor(bundle.feature_config)
        self.lock = threading.Lock()

    def predict(self, events) -> list[dict]:
        events = list(events)
        with self.lock:
            rows = self.features.transform(events)
            scores = self.bundle.score_rows(rows)
            return [{"event_id": event.event_id, "prediction": "anomaly" if score > self.bundle.threshold else "normal",
                     "is_anomaly": score > self.bundle.threshold, "anomaly_score": score,
                     "model_name": self.bundle.model_name, "model_version": self.bundle.candidate_id}
                    for event, score in zip(events, scores, strict=True)]
