"""Lifecycle subcommands kept separate from the Phase 3 training CLI."""
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from shared.schemas.logs import utc_time

from .artifacts import write_json
from .data import load_dataset
from .evaluation import GatePolicy

COMMANDS = {"make-evaluation", "track", "calibrate", "evaluate", "register", "promote", "audit"}


def add_commands(sub):
    make = sub.add_parser("make-evaluation", help="Generate separate future synthetic validation and test data")
    make.add_argument("--history-dataset", required=True)
    make.add_argument("--output", required=True)
    make.add_argument("--count-per-split", type=int, default=300)
    make.add_argument("--seed", type=int, default=84)
    for name in ("track", "calibrate", "evaluate", "register", "promote", "audit"):
        command = sub.add_parser(name)
        if name in ("track", "calibrate", "evaluate"):
            command.add_argument("--run-dir", required=True)
        if name in ("calibrate", "evaluate"):
            command.add_argument("--history-dataset", required=True)
            command.add_argument("--history-provenance")
            command.add_argument("--validation-dataset", required=True)
            command.add_argument("--validation-provenance")
            command.add_argument("--output", default="ml/artifacts")
        if name == "calibrate":
            command.add_argument("--validation-labels")
            command.add_argument("--policy", help="JSON GatePolicy frozen before test scoring")
            command.add_argument("--candidate-id", help="Explicit choice required for unlabeled validation")
        if name == "evaluate":
            command.add_argument("--plan-dir", required=True)
            command.add_argument("--test-dataset", required=True)
            command.add_argument("--test-provenance")
            command.add_argument("--test-labels")
        if name in ("track", "evaluate", "register", "promote"):
            command.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///.local/mlflow/tracking.db"))
            command.add_argument("--experiment", default=os.environ.get("MLFLOW_EXPERIMENT_NAME", "LogSentinel"))
            command.add_argument("--artifact-root", default=os.environ.get("MLFLOW_ARTIFACT_ROOT"))
        if name == "register":
            command.add_argument("--evaluation-dir", required=True)
        if name in ("register", "promote", "audit"):
            command.add_argument("--audit-path", default=os.environ.get("REGISTRY_AUDIT_PATH", ".local/mlflow/lifecycle.sqlite3"))
        if name in ("register", "promote"):
            command.add_argument("--actor", required=True)
            command.add_argument("--model-name", default=os.environ.get("MODEL_NAME", "logsentinel-model"))
            command.add_argument("--token-stdin", action="store_true", required=True, help="Read the admin token from stdin, never argv")
        if name == "promote":
            command.add_argument("--version", required=True)
            command.add_argument("--alias", choices=["staging", "champion"], required=True)
            command.add_argument("--reason", required=True)
        if name == "evaluate":
            command.add_argument("--no-tracking", action="store_true", help="Explicit local evaluation only")


def dataset(path, provenance_path=None):
    provenance = json.loads(Path(provenance_path).read_text()) if provenance_path else None
    return load_dataset(path, provenance)


def execute(args) -> dict:
    if args.command == "make-evaluation":
        from .synthetic import future_cases
        history = dataset(args.history_dataset)
        if history.provenance["kind"] != "synthetic" or not 80 <= args.count_per_split <= 1000000:
            raise ValueError("invalid_synthetic_evaluation_configuration")
        root = Path(args.output)
        root.mkdir(parents=True, exist_ok=False)
        start = utc_time(history.events[-1].timestamp) + timedelta(seconds=1)
        for offset, role in enumerate(("validation", "test")):
            with (root / f"{role}.jsonl").open("x") as events, (root / f"{role}.labels.jsonl").open("x") as labels:
                for event, label in future_cases(start + timedelta(seconds=offset * args.count_per_split),
                                                 args.count_per_split, args.seed + offset, role):
                    events.write(event.to_json() + "\n")
                    labels.write(json.dumps(label) + "\n")
        return {"status": "created", "synthetic": True, "records_per_split": args.count_per_split}
    if args.command == "track":
        if (Path(args.run_dir) / "evaluation.json").is_file():
            from .tracking import track_evaluation
            return {"run_id": track_evaluation(args.run_dir, args.tracking_uri, args.experiment,
                                               args.artifact_root), "status": "tracked"}
        from .tracking import track_training
        return track_training(args.run_dir, args.tracking_uri, args.experiment, args.artifact_root)
    if args.command == "calibrate":
        from .lifecycle import calibrate
        policy = GatePolicy(**json.loads(Path(args.policy).read_text())) if args.policy else GatePolicy()
        path = calibrate(args.run_dir, dataset(args.history_dataset, args.history_provenance),
            dataset(args.validation_dataset, args.validation_provenance), args.validation_labels,
            args.output, policy, args.candidate_id)
        return {"status": "frozen_before_test", "plan_directory": str(path)}
    if args.command == "evaluate":
        from .lifecycle import evaluate
        from .integrity import read_sealed
        path = evaluate(args.run_dir, args.plan_dir, dataset(args.history_dataset, args.history_provenance),
            dataset(args.validation_dataset, args.validation_provenance),
            dataset(args.test_dataset, args.test_provenance), args.test_labels, args.output)
        report = read_sealed(str(path), "evaluation.json")
        result = {"status": "evaluated", "evaluation_directory": str(path), "gate": report["gate"]}
        if not args.no_tracking:
            from .tracking import track_evaluation
            result["mlflow_run_id"] = track_evaluation(str(path), args.tracking_uri, args.experiment, args.artifact_root)
        return result
    if args.command == "audit":
        from .audit import LifecycleAudit
        return {"events": LifecycleAudit(args.audit_path).entries()}
    token = sys.stdin.readline(4096).rstrip("\r\n")
    if args.command == "register":
        from .registry import register_evaluation
        return register_evaluation(args.evaluation_dir, args.tracking_uri, args.audit_path,
            token, args.actor, args.model_name, args.experiment, args.artifact_root)
    if args.command == "promote":
        from .registry import promote
        return promote(args.tracking_uri, args.audit_path, token, args.actor, args.model_name,
                       args.version, args.alias, args.reason)
    raise ValueError("unknown_lifecycle_command")
