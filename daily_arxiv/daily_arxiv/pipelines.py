# Pipeline: pass items through. The spider now extracts every field from
# the listing HTML directly, so we don't need a second API call per paper.


class DailyArxivPipeline:
    def __init__(self):
        self.seen = 0

    def process_item(self, item: dict, spider):
        self.seen += 1
        return item

    def close_spider(self, spider):
        spider.logger.info(f"DailyArxivPipeline: {self.seen} items emitted")
