from dataclasses import asdict, dataclass, field
import math

from ml.features import FeatureConfig


@dataclass(frozen=True)
class TrainingConfig:
    random_seed: int = 42
    holdout_fraction: float = 0.2
    min_train: int = 32
    min_holdout: int = 8
    n_estimators: tuple[int, ...] = (100, 200)
    max_samples: int = 256
    max_text_features: int = 2048
    threshold_quantile: float = 0.99
    feature_config: FeatureConfig = field(default_factory=FeatureConfig)

    def __post_init__(self):
        if type(self.random_seed) is not int or not 0 <= self.random_seed < 2**32:
            raise ValueError("invalid_random_seed")
        if (type(self.holdout_fraction) not in (int, float)
                or not math.isfinite(self.holdout_fraction) or not 0 < self.holdout_fraction < 1
                or type(self.min_train) is not int or type(self.min_holdout) is not int
                or self.min_train < 2 or self.min_holdout < 1):
            raise ValueError("invalid_training_split")
        if not isinstance(self.n_estimators, tuple) or not self.n_estimators:
            raise ValueError("invalid_estimator_grid")
        if len(set(self.n_estimators)) != len(self.n_estimators):
            raise ValueError("invalid_estimator_grid")
        if any(type(count) is not int or not 1 <= count <= 2000 for count in self.n_estimators):
            raise ValueError("invalid_estimator_count")
        if (type(self.max_samples) is not int or not 2 <= self.max_samples <= 100000
                or type(self.max_text_features) is not int or not 16 <= self.max_text_features <= 100000):
            raise ValueError("invalid_feature_configuration")
        if (type(self.threshold_quantile) not in (int, float)
                or not math.isfinite(self.threshold_quantile)
                or not 0.5 <= self.threshold_quantile < 1):
            raise ValueError("invalid_threshold_quantile")

        if not isinstance(self.feature_config, FeatureConfig):
            raise ValueError("invalid_feature_configuration")

    def to_dict(self):
        return asdict(self)
