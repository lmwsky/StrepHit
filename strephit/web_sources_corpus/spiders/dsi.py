# -*- coding: utf-8 -*-
from scrapy import Request

from strephit.web_sources_corpus.spiders import BaseSpider
from strephit.web_sources_corpus.items import WebSourcesCorpusItem
from strephit.commons import text


class DsiSpider(BaseSpider):
    name = "dsi"
    allowed_domains = ["www.uni-stuttgart.de"]
    page_url = 'http://www.uni-stuttgart.de/hi/gnt/dsi2/index.php?table_name=dsi&' \
               'function=search&where_clause=&order=lastname&order_type=ASC&page=%d'

    list_page_selectors = None
    detail_page_selectors = 'xpath:.//a[contains(., "Detail page of this illustrator")]/@href'
    next_page_selectors = 'xpath:.//a[contains(., ">")]/@href'

    item_class = WebSourcesCorpusItem

    def start_requests(self):
        for i in range(1070):
            yield Request(self.page_url % i, self.parse)

    def refine_item(self, response, item):
        data = {
            key: [v.strip() for v in value.split(';')
                  if v.strip() and 'http://' not in v and 'viaf.org' not in v]
            for key, value in text.extract_dict(
                response,
                'xpath:(.//table)[last()]//tr/td[@class="td_label_details"]',
                'xpath:(.//table)[last()]//tr/td[@class="td_value_details"]'
            ).iteritems()
        }

        last = ' '.join(data.get('Last Name', ''))
        first = ' '.join(data.get('Given Name', ''))
        item['name'] = '%s, %s' % (last, first)
        item['other'] = data

        return super(DsiSpider, self).refine_item(response, item)
