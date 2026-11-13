"""No fitting at inference: fitted TF-IDF, category encoder and scaler stay in one pipeline."""
from ml.features import CATEGORICAL_COLUMNS, NUMERIC_COLUMNS


def build_pipeline(variant: str, config, n_estimators: int):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import IsolationForest
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    if variant not in ("tfidf", "numeric_frequency"):
        raise ValueError("unknown_model_variant")
    transformers = [
        ("categorical", OneHotEncoder(handle_unknown="ignore", max_categories=128), CATEGORICAL_COLUMNS),
        ("numeric", StandardScaler(), NUMERIC_COLUMNS),
    ]
    if variant == "tfidf":
        transformers.insert(0, ("text", TfidfVectorizer(
            max_features=config.max_text_features, ngram_range=(1, 2),
            sublinear_tf=True, lowercase=False, token_pattern=r"(?u)\b\w+\b"), "text"))
    preprocessing = ColumnTransformer(transformers, sparse_threshold=1.0, remainder="drop")
    estimator = IsolationForest(n_estimators=n_estimators,
                               max_samples=config.max_samples, contamination="auto",
                               random_state=config.random_seed, n_jobs=1)
    return Pipeline([("features", preprocessing), ("model", estimator)])
