"""Read-only MLflow metadata adapter; never downloads/deserializes model artifacts."""
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from shared.common.redaction import redact


class Registry:
    def __init__(self, uri=None):
        self.uri = uri or os.environ.get('MLFLOW_TRACKING_URI','')

    def client(self):
        if not self.uri:
            raise ValueError('tracking_not_configured')
        if self.uri.startswith('sqlite:///') and not Path(self.uri[10:]).is_file():
            raise ValueError('tracking_store_missing')
        parsed = urlsplit(self.uri)
        if parsed.scheme not in ('sqlite','http','https') or parsed.username or parsed.password:
            raise ValueError('invalid_tracking_uri')
        os.environ.setdefault('MLFLOW_ENABLE_TELEMETRY','false')
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT','5')
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES','0')
        from mlflow.tracking import MlflowClient
        return MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)

    def models(self):
        client = self.client()
        items = []
        versions = client.search_model_versions(max_results=100)
        for model in client.search_registered_models(max_results=100):
            matching = [version for version in versions if version.name == model.name]
            versions_public = []
            for version in matching:
                run_data = client.get_run(version.run_id).data if version.run_id else None
                params = run_data.params if run_data else {}
                metrics = run_data.metrics if run_data else {}
                versions_public.append({'version':str(version.version),'run_id':version.run_id,
                    'status':version.status,'created_at':version.creation_timestamp,
                    'validation_status':version.tags.get('logsentinel.validation_status','unknown'),
                    'scope':version.tags.get('logsentinel.scope','unknown'),
                    'candidate_id':version.tags.get('logsentinel.candidate_id'),
                    'parameters':{key:redact(str(params[key])) for key in ('threshold','candidate_id','training_dataset_version','evaluation_sha256','policy') if key in params},
                    'metrics':{key:value for key,value in metrics.items() if key in ('precision','recall','f1','false_positive_rate') and math.isfinite(value)}})
            items.append({'name': redact(model.name), 'aliases': {key:str(value) for key,value in model.aliases.items()}, 'versions':versions_public})
        return {'items': items, 'bounded': True}

    def experiments(self):
        client = self.client()
        deadline = time.monotonic() + 10
        experiments = client.search_experiments(max_results=10)
        items = []
        allowed_params = {'algorithm','candidate_id','dataset_version','random_seed','n_estimators','max_samples','feature_version','variant','config.random_seed','config.n_estimators','config.max_samples','config.feature_config.window_seconds'}
        for experiment in experiments:
            if time.monotonic() > deadline:
                raise TimeoutError('registry_deadline')
            runs = client.search_runs([experiment.experiment_id], max_results=100, order_by=['attributes.start_time DESC'])
            items.append({'id': experiment.experiment_id, 'name': redact(experiment.name), 'runs': [
                {'id': run.info.run_id, 'name': redact(run.data.tags.get('mlflow.runName','')),
                 'status': run.info.status, 'start_time': run.info.start_time, 'end_time': run.info.end_time,
                 'parameters': {key: redact(str(value)) for key, value in run.data.params.items() if key in allowed_params},
                 'metrics': {key: value for key, value in run.data.metrics.items() if math.isfinite(value)}} for run in runs]})
        return {'items': items, 'bounded': True}
