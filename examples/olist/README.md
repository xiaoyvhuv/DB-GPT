# Olist report and evidence case

This example uses the
[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
to exercise DB-GPT's report and evidence flow against a realistic relational
datasource.

The public dataset contains anonymized Brazilian marketplace orders and is
licensed under CC BY-NC-SA 4.0. Do not redistribute the source files from this
repository; download them from Kaggle and preserve the attribution and license.

## Prepare the datasource

The downloaded CSV files and generated database live under `pilot/data/`, which
is already excluded from Git.

```bash
mkdir -p pilot/data/olist/csv
curl -L --fail \
  -o pilot/data/olist/olist.zip \
  https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce
python -m zipfile -e pilot/data/olist/olist.zip pilot/data/olist/csv
python examples/olist/build_olist_sqlite.py \
  --csv-dir pilot/data/olist/csv \
  --output pilot/data/olist/olist.db
```

The archive downloaded on 2026-08-29 had this SHA-256 digest:

```text
967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784
```

Kaggle may publish a new dataset version later. The importer therefore stores
the row count and SHA-256 digest of every imported CSV in
`_olist_import_manifest`; that manifest, rather than the archive digest above,
is the evidence for a particular local datasource build.

Add `pilot/data/olist/olist.db` as a SQLite datasource in DB-GPT. The importer
preserves the nine source relations and adds indexes only on common join keys.
It does not create a denormalized table or precompute the expected answers.

## Case question

Use the complete-month comparison period from 2017-01-01 through 2018-08-31:

> 请基于 Olist 电商数据库生成一份经营与履约诊断报告。只统计已交付订单，分析
> 2017-01-01 至 2018-08-31 的 GMV、订单量、客单价、运费占比和月度趋势；比较
> 主要商品品类及客户所在州的表现；分析配送时长、逾期交付与客户评分之间的关系；
> 分析支付方式和分期情况；最后给出三条可执行的经营建议。每个指标、图表和结论
> 都必须绑定其 SQL、输入表、结果摘要及上游证据，不能把模型推断写成数据库事实。

This single question is intended to produce:

- metric blocks for GMV, delivered orders, average order value, freight ratio,
  average review score, and late-delivery rate;
- chart blocks for monthly trend, category/state performance, delivery versus
  review score, and payment composition;
- table blocks for ranked categories, states, and operational exceptions;
- insight/text blocks whose evidence resolves to the SQL and result artifacts;
- an immutable report revision that can be opened from the chat response and
  exported through DB-GPT's existing PDF path.

## Reproducibility baseline

For the Kaggle version last updated on 2021-10-01, the importer produced
1,550,922 rows:

| Table | Rows |
| --- | ---: |
| `customers` | 99,441 |
| `geolocation` | 1,000,163 |
| `order_items` | 112,650 |
| `order_payments` | 103,886 |
| `order_reviews` | 99,224 |
| `orders` | 99,441 |
| `product_category_translation` | 71 |
| `products` | 32,951 |
| `sellers` | 3,095 |

The following values are validation anchors, not report fixtures. They use
delivered orders purchased at or after 2017-01-01 and before 2018-09-01:

| Metric | Expected value |
| --- | ---: |
| Delivered orders | 96,211 |
| GMV (`SUM(order_items.price)`) | 13,181,027.13 |
| Average order value | 137.00 |
| Freight value | 2,192,092.88 |
| Freight / GMV | 16.63% |
| Average delivery duration | 12.54 days |
| Late-delivery rate | 8.13% |
| Average review score | 4.16 |

These anchors should be rechecked whenever the source manifest changes. A
report must still retain the executed SQL and result artifacts; matching these
numbers alone is not evidence provenance.
