from __future__ import annotations

import csv
import math
from pathlib import Path


REQUIRED_CSVS = (
    "warranty_claims.csv",
    "product_warranty_records.csv",
    "replacement_history.csv",
)


class CsvDataSource:
    """Loads the supplied seed datasets and offers indexed lookups."""

    def __init__(self, folder: Path):
        missing = [name for name in REQUIRED_CSVS if not (folder / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Missing CSV file(s) in {folder}: {', '.join(missing)}")
        self.claims = self._read_csv(folder / "warranty_claims.csv")
        self.warranty = self._read_csv(folder / "product_warranty_records.csv")
        self.replacements = self._read_csv(folder / "replacement_history.csv")
        self._validate_data()
        self._warranty_by_ref = {row["product_ref"]: self._normalize_warranty(row) for row in self.warranty}
        self._replacement_by_customer = {}
        for row in self.replacements:
            key = row["customer_name"].strip().casefold()
            self._replacement_by_customer[key] = self._replacement_by_customer.get(key, 0) + int(
                float(row.get("replacements_last_30_days", 0) or 0)
            )

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"{path.name} has no header row.")
            if any(not name.strip() for name in reader.fieldnames) or len(set(reader.fieldnames)) != len(
                reader.fieldnames
            ):
                raise ValueError(f"{path.name} has blank or duplicate column names.")
            records = []
            for line_number, row in enumerate(reader, start=2):
                if None in row:
                    raise ValueError(f"{path.name} has too many values on line {line_number}.")
                records.append({key: value or "" for key, value in row.items()})
            return records

    def _validate_data(self) -> None:
        required_columns = {
            "warranty_claims.csv": (
                self.claims,
                {
                    "claim_id",
                    "customer_name",
                    "product_ref",
                    "claim_date",
                    "claim_text",
                },
            ),
            "product_warranty_records.csv": (
                self.warranty,
                {
                    "product_ref",
                    "customer_name",
                    "product_name",
                    "claim_category",
                    "credit_eligible",
                    "credit_value",
                    "purchase_price",
                },
            ),
            "replacement_history.csv": (
                self.replacements,
                {"customer_name", "replacements_last_30_days"},
            ),
        }
        for filename, (frame, expected) in required_columns.items():
            columns = set(frame[0]) if frame else set()
            missing = expected - columns
            if missing:
                raise ValueError(f"{filename} is missing required column(s): {', '.join(sorted(missing))}")
        for filename, frame, key in (
            ("warranty_claims.csv", self.claims, "claim_id"),
            ("product_warranty_records.csv", self.warranty, "product_ref"),
        ):
            keys = [str(row.get(key, "")).strip() for row in frame]
            if any(not value for value in keys):
                raise ValueError(f"{filename} contains a blank {key}.")
            if len(keys) != len(set(keys)):
                raise ValueError(f"{filename} contains duplicate {key} values.")
        for filename, frame, field in (
            ("product_warranty_records.csv", self.warranty, "credit_value"),
            ("product_warranty_records.csv", self.warranty, "purchase_price"),
            ("replacement_history.csv", self.replacements, "replacements_last_30_days"),
        ):
            try:
                values = [float(row[field]) for row in frame]
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"{filename} contains an invalid {field} value.") from exc
            if any(not math.isfinite(value) for value in values):
                raise ValueError(f"{filename} contains an invalid {field} value.")
            # A negative catalog credit is bad monetary data, but it must not
            # prevent the rest of the seed claims from loading. Keep the raw
            # value so policy can route this one record for human review.
            if field != "credit_value" and any(value < 0 for value in values):
                raise ValueError(f"{filename} contains an invalid {field} value.")
            if field == "replacements_last_30_days" and any(not value.is_integer() for value in values):
                raise ValueError(f"{filename} contains a non-integer {field} value.")
        flags = {"true", "false", "1", "0", "yes", "no"}
        if any(str(row["credit_eligible"]).strip().lower() not in flags for row in self.warranty):
            raise ValueError("product_warranty_records.csv contains an invalid credit_eligible value.")

    @staticmethod
    def _normalize_warranty(row: dict) -> dict:
        row = dict(row)
        row["purchase_price"] = float(row.get("purchase_price") or 0)
        row["credit_value"] = float(row.get("credit_value") or 0)
        row["credit_eligible"] = str(row.get("credit_eligible", "")).strip().lower() in {
            "true",
            "1",
            "yes",
        }
        return row

    def claims_as_records(self) -> list[dict]:
        return [dict(row) for row in self.claims]

    def lookup_warranty(self, product_ref: str) -> dict | None:
        return self._warranty_by_ref.get(product_ref)

    def replacements_last_30_days(self, customer_name: str) -> int:
        return self._replacement_by_customer.get(customer_name.strip().casefold(), 0)
