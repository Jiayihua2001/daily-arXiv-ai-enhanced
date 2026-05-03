import scrapy
import os
import re


class ArxivSpider(scrapy.Spider):
    """
    Crawl the daily arxiv listing pages and yield one item per paper.

    Extracts every field directly from the listing HTML — no per-paper
    API call — so the spider works reliably from CI shared IPs that
    arxiv.org's API would otherwise rate-limit.
    """

    name = "arxiv"
    allowed_domains = ["arxiv.org"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = os.environ.get(
            "CATEGORIES",
            "cond-mat.mtrl-sci,physics.chem-ph,physics.comp-ph,cond-mat.soft,cs.LG",
        )
        self.target_categories = {c.strip() for c in categories.split(",") if c.strip()}
        self.start_urls = [
            f"https://arxiv.org/list/{cat}/new" for cat in self.target_categories
        ]

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def parse(self, response):
        # Anchor index of the "Cross-listed" / "Replacements" sections — we
        # only want fresh "New submissions" entries (id strictly less than
        # the first non-new boundary).
        boundary = None
        for li in response.css("div[id=dlpage] ul li"):
            href = li.css("a::attr(href)").get() or ""
            text = " ".join(li.css("*::text").getall()).lower()
            if "item" in href and ("cross" in text or "replacement" in text):
                try:
                    n = int(href.split("item")[-1])
                    boundary = n if boundary is None else min(boundary, n)
                except ValueError:
                    pass

        for dt in response.css("dl dt"):
            anchor = dt.css("a[name^='item']::attr(name)").get()
            if not anchor:
                continue
            try:
                paper_idx = int(anchor.split("item")[-1])
            except ValueError:
                continue
            if boundary is not None and paper_idx >= boundary:
                continue

            abs_link = dt.css("a[title='Abstract']::attr(href)").get()
            if not abs_link:
                continue
            arxiv_id = abs_link.split("/")[-1]

            dd = dt.xpath("following-sibling::dd[1]")
            if not dd:
                continue

            # Title
            title_node = dd.css(".list-title")
            title = self._clean(
                "".join(
                    t for t in title_node.css("*::text").getall()
                    if t.strip() and t.strip().lower() != "title:"
                )
            )

            # Authors
            authors = [
                self._clean(a)
                for a in dd.css(".list-authors a::text").getall()
                if a.strip()
            ]

            # Subjects → category codes inside parentheses
            subjects_text = " ".join(dd.css(".list-subjects *::text").getall())
            paper_categories = re.findall(r"\(([^)]+)\)", subjects_text)
            paper_categories_set = set(paper_categories)

            # The listing page is already filtered to one of our target
            # categories, but a paper may also be cross-listed elsewhere;
            # accept it as long as it overlaps the target set.
            if self.target_categories and not (
                paper_categories_set & self.target_categories
            ):
                continue

            # Abstract
            summary = self._clean(
                " ".join(dd.css("p.mathjax::text").getall())
            )

            # Optional: comment ("Comments: ...") — present in some listings.
            comment_text = " ".join(dd.css(".list-comments *::text").getall())
            comment = self._clean(
                re.sub(r"^\s*comments?:\s*", "", comment_text, flags=re.I)
            ) or None

            yield {
                "id": arxiv_id,
                "categories": paper_categories or list(self.target_categories),
                "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
                "abs": f"https://arxiv.org/abs/{arxiv_id}",
                "authors": authors,
                "title": title,
                "comment": comment,
                "summary": summary,
            }
