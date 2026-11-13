import argparse
import json
import os
from pathlib import Path
import sys

from ml.features import FeatureConfig, FeatureStateError

from .config import TrainingConfig
from .data import DatasetError, load_dataset, temporal_split
from .synthetic import synthetic_cases


def main() -> int:
    parser = argparse.ArgumentParser(description="LogSentinel one-shot ML training service")
    sub = parser.add_subparsers(dest="command", required=True)
    synthetic = sub.add_parser("make-synthetic")
    synthetic.add_argument("--output", required=True)
    synthetic.add_argument("--labels", help="Optional separate annotation JSONL; never model input")
    synthetic.add_argument("--count", type=int, default=600)
    synthetic.add_argument("--seed", type=int, default=42)
    for name in ("inspect", "train"):
        command = sub.add_parser(name)
        command.add_argument("--dataset", required=True, help="Normalized event JSONL from processor export")
        command.add_argument("--provenance", help="Required JSON manifest for local/public nonsynthetic data")
        command.add_argument("--holdout-fraction", type=float, default=0.2)
        command.add_argument("--min-train", type=int, default=32)
        command.add_argument("--min-holdout", type=int, default=8)
        if name == "train":
            command.add_argument("--output", default=os.environ.get("TRAINING_OUTPUT", "ml/artifacts"))
            command.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI"))
            command.add_argument("--experiment", default=os.environ.get("MLFLOW_EXPERIMENT_NAME", "LogSentinel"))
            command.add_argument("--artifact-root", default=os.environ.get("MLFLOW_ARTIFACT_ROOT"))
            command.add_argument("--seed", type=int, default=os.environ.get("RANDOM_SEED", "42"))
            command.add_argument("--estimators", type=int, nargs="+", default=[100, 200])
            command.add_argument("--max-samples", type=int, default=256)
            command.add_argument("--max-text-features", type=int, default=2048)
            command.add_argument("--threshold-quantile", type=float, default=0.99)
            command.add_argument("--window-seconds", type=float, default=os.environ.get("FEATURE_WINDOW_SECONDS", "60"))
            command.add_argument("--max-window-events", type=int, default=100000)
    from .lifecycle_cli import COMMANDS, add_commands, execute
    add_commands(sub)
    args = parser.parse_args()
    try:
        if args.command in COMMANDS:
            print(json.dumps(execute(args), indent=2))
            return 0
        if args.command == "make-synthetic":
            if not 80 <= args.count <= 1000000:
                raise ValueError("synthetic_count_must_be_between_80_and_1000000")
            output = Path(args.output)
            labels = Path(args.labels) if args.labels else None
            if output.exists() or (labels and labels.exists()) or (labels and output.resolve() == labels.resolve()):
                raise ValueError("output_already_exists")
            output.parent.mkdir(parents=True, exist_ok=True)
            if labels:
                labels.parent.mkdir(parents=True, exist_ok=True)
            from contextlib import ExitStack
            with ExitStack() as stack:
                stream = stack.enter_context(output.open("x"))
                annotations = stack.enter_context(labels.open("x")) if labels else None
                for event, label in synthetic_cases(args.count, args.seed):
                    stream.write(event.to_json() + "\n")
                    if annotations:
                        annotations.write(json.dumps(label) + "\n")
            print(json.dumps({"status": "created", "synthetic": True, "records": args.count}))
            return 0
        provenance = json.loads(Path(args.provenance).read_text()) if args.provenance else None
        dataset = load_dataset(args.dataset, provenance)
        if args.command == "inspect":
            split = temporal_split(dataset.events, args.holdout_fraction, args.min_train, args.min_holdout)
            print(json.dumps({"dataset_version": dataset.version, "provenance": dataset.provenance,
                              "records": len(dataset.events), "duplicates_removed": dataset.duplicates_removed,
                              "split": split.manifest()}, indent=2))
            return 0
        config = TrainingConfig(random_seed=args.seed, holdout_fraction=args.holdout_fraction,
                                min_train=args.min_train, min_holdout=args.min_holdout,
                                n_estimators=tuple(args.estimators), max_samples=args.max_samples,
                                max_text_features=args.max_text_features,
                                threshold_quantile=args.threshold_quantile,
                                feature_config=FeatureConfig(args.window_seconds, args.max_window_events))
        from .train import train
        destination = train(dataset, args.output, config)
        tracking = None
        if args.tracking_uri:
            from .tracking import track_training
            tracking = track_training(str(destination), args.tracking_uri, args.experiment, args.artifact_root)
        print(json.dumps({"status": "candidate_not_approved", "artifact_directory": str(destination),
                          "dataset_version": dataset.version, "tracking_run_id": tracking["run_id"] if tracking else None}))
        return 0
    except ImportError:
        print('{"status":"error","reason":"training_dependencies_unavailable"}', file=sys.stderr)
        return 2
    except (DatasetError, FeatureStateError, ValueError) as exc:
        # Allow only known safe reason codes, never paths or record contents in CLI failures.
        reason = str(exc)
        if not reason.replace("_", "").isalnum() or len(reason) > 100:
            reason = "invalid_training_input"
        print(json.dumps({"status": "error", "reason": reason}), file=sys.stderr)
        return 2
    except RuntimeError:
        print('{"status":"error","reason":"lifecycle_backend_operation_failed"}', file=sys.stderr)
        return 2
    except OSError:
        print('{"status":"error","reason":"training_file_access_failed"}', file=sys.stderr)
        return 2
    except Exception:
        print('{"status":"error","reason":"lifecycle_operation_failed"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
