# Pipeline: enrich each crawled item with metadata from the arxiv API.
# Hardened with retries + per-item logging so transient failures (e.g.
# arxiv.org rate-limiting on a CI shared IP) don't silently drop items.

import arxiv
import time


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        self.client = arxiv.Client(self.page_size, num_retries=5, delay_seconds=3)
        self.fetched = 0
        self.failed = 0

    def process_item(self, item: dict, spider):
        item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        item["abs"] = f"https://arxiv.org/abs/{item['id']}"

        last_err = None
        for attempt in range(1, 4):
            try:
                search = arxiv.Search(id_list=[item["id"]])
                paper = next(self.client.results(search))
                item["authors"] = [a.name for a in paper.authors]
                item["title"] = paper.title
                item["categories"] = paper.categories
                item["comment"] = paper.comment
                item["summary"] = paper.summary
                self.fetched += 1
                return item
            except StopIteration as e:
                last_err = e
                spider.logger.warning(
                    f"arxiv API returned no result for {item['id']} (attempt {attempt}/3)"
                )
            except Exception as e:
                last_err = e
                spider.logger.warning(
                    f"arxiv API error for {item['id']} (attempt {attempt}/3): {e}"
                )
            time.sleep(2 ** attempt)

        self.failed += 1
        spider.logger.error(
            f"giving up on {item['id']} after 3 attempts ({last_err}); dropping"
        )
        return None  # explicitly drop

    def close_spider(self, spider):
        spider.logger.info(
            f"DailyArxivPipeline summary: fetched={self.fetched} failed={self.failed}"
        )
