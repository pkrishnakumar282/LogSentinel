"""MLflow flavor for already-extracted feature rows. Ordered context remains a service concern."""
import mlflow.pyfunc


class FeatureRowModel(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        import joblib
        self.bundle = joblib.load(context.artifacts["bundle"])

    def predict(self, context, model_input, params=None):
        import pandas as pd
        scores = self.bundle.score_rows(model_input.to_dict(orient="records"))
        return pd.DataFrame({"anomaly_score": scores,
                             "is_anomaly": [value > self.bundle.threshold for value in scores]})
