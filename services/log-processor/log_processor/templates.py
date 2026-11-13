"""Explicit template engines: Drain3 by default, deterministic masking for offline use."""
import re
import threading


def masked_template(message: str) -> str:
    message = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "<UUID>", message)
    message = re.sub(r"\b0x[0-9a-fA-F]+\b", "<HEX>", message)
    return re.sub(r"\b\d+(?:\.\d+)?\b", "<NUM>", message)


class TemplateEngine:
    def __init__(self, mode: str = "drain3", max_clusters: int = 4096):
        if mode not in ("drain3", "masked"):
            raise ValueError("invalid_template_mode")
        self.mode = mode
        self.lock = threading.Lock()
        self.miner = None
        if mode == "drain3":
            # Missing dependencies fail startup; never silently substitute a different miner.
            from drain3 import TemplateMiner
            from drain3.template_miner_config import TemplateMinerConfig
            config = TemplateMinerConfig()
            config.drain_max_clusters = max_clusters
            config.profiling_enabled = False
            self.miner = TemplateMiner(config=config)

    def extract(self, message: str) -> str:
        clean = masked_template(message)
        if self.miner is None:
            return clean
        with self.lock:
            return self.miner.add_log_message(clean)["template_mined"]
