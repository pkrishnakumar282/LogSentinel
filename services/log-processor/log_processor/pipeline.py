from shared.schemas import RawLog

from .normalizer import Normalizer


class ProcessingPipeline:
    def __init__(self, normalizer: Normalizer, publisher):
        self.normalizer = normalizer
        self.publisher = publisher

    def ingest(self, record: RawLog):
        return self.publisher.publish(self.normalizer.normalize(record))
