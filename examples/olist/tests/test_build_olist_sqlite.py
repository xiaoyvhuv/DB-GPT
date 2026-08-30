import csv
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "build_olist_sqlite.py"

FIXTURES = {
    "olist_customers_dataset.csv": (
        [
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ],
        [["customer-1", "unique-1", "01001", "sao paulo", "SP"]],
    ),
    "olist_geolocation_dataset.csv": (
        [
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ],
        [["01001", "-23.5505", "-46.6333", "sao paulo", "SP"]],
    ),
    "olist_order_items_dataset.csv": (
        [
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ],
        [
            [
                "order-1",
                "1",
                "product-1",
                "seller-1",
                "2017-01-02 10:00:00",
                "100.50",
                "12.25",
            ]
        ],
    ),
    "olist_order_payments_dataset.csv": (
        [
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ],
        [["order-1", "1", "credit_card", "2", "112.75"]],
    ),
    "olist_order_reviews_dataset.csv": (
        [
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ],
        [
            [
                "review-1",
                "order-1",
                "5",
                "great",
                "arrived on time",
                "2017-01-10 00:00:00",
                "2017-01-11 09:00:00",
            ]
        ],
    ),
    "olist_orders_dataset.csv": (
        [
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        [
            [
                "order-1",
                "customer-1",
                "delivered",
                "2017-01-01 10:00:00",
                "2017-01-01 11:00:00",
                "2017-01-03 12:00:00",
                "2017-01-08 13:00:00",
                "2017-01-12 00:00:00",
            ]
        ],
    ),
    "olist_products_dataset.csv": (
        [
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ],
        [["product-1", "beleza_saude", "20", "100", "2", "500", "20", "10", "15"]],
    ),
    "olist_sellers_dataset.csv": (
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
        [["seller-1", "02002", "sao paulo", "SP"]],
    ),
    "product_category_name_translation.csv": (
        ["product_category_name", "product_category_name_english"],
        [["beleza_saude", "health_beauty"]],
    ),
}


def _write_fixture(directory: Path) -> None:
    directory.mkdir()
    for name, (headers, rows) in FIXTURES.items():
        with (directory / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)


def test_cli_builds_typed_nine_table_database_with_source_manifest(
    tmp_path: Path,
) -> None:
    csv_dir = tmp_path / "csv"
    database = tmp_path / "olist.db"
    _write_fixture(csv_dir)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--csv-dir",
            str(csv_dir),
            "--output",
            str(database),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone() == (1,)
        assert connection.execute(
            "SELECT price, freight_value FROM order_items"
        ).fetchone() == (100.5, 12.25)
        assert connection.execute(
            "SELECT payment_installments FROM order_payments"
        ).fetchone() == (2,)
        manifest = connection.execute(
            "SELECT source_file, row_count, sha256 "
            "FROM _olist_import_manifest ORDER BY source_file"
        ).fetchall()

    assert len(manifest) == 9
    assert manifest[0][1] == 1
    expected_hash = hashlib.sha256((csv_dir / manifest[0][0]).read_bytes()).hexdigest()
    assert manifest[0][2] == expected_hash


def test_imported_join_keys_use_indexes(tmp_path: Path) -> None:
    csv_dir = tmp_path / "csv"
    database = tmp_path / "olist.db"
    _write_fixture(csv_dir)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--csv-dir",
            str(csv_dir),
            "--output",
            str(database),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    with sqlite3.connect(database) as connection:
        plans = {
            f"{table}.{column}": connection.execute(
                f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE {column} = ?",
                ("example",),
            ).fetchall()
            for table, column in (
                ("orders", "customer_id"),
                ("order_items", "order_id"),
                ("order_items", "product_id"),
                ("order_items", "seller_id"),
                ("order_payments", "order_id"),
                ("order_reviews", "order_id"),
            )
        }

    assert all("USING INDEX" in str(plan) for plan in plans.values()), plans
