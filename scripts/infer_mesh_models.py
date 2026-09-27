#!/usr/bin/env python3

import argparse
import csv
from collections import Counter
from pathlib import Path


CANDIDATES = {
    (20, 21): ("Generic OnOff Get -> Status", "high"),
    (22, 21): ("Generic OnOff Set -> Status", "high"),
    (20, 22): ("Light Lightness Get -> Status", "medium"),
    (25, 22): ("Light Lightness Set with transition -> Status", "low"),
    (20, 24): ("Light CTL Temperature Get -> Status", "medium"),
    (25, 24): ("Light CTL Temperature Set -> Status", "medium"),
    (20, 26): ("Light HSL Get -> Status", "medium"),
    (27, 26): ("Light HSL Set -> Status", "high"),
}


def load_rows(path):
    rows = []
    with path.open(newline="") as source:
        for row in csv.DictReader(source):
            rows.append(
                {
                    "frame": int(row["frame"]),
                    "time": float(row["time_relative"]),
                    "direction": row["direction"],
                    "length": int(row["pdu_length"]),
                }
            )
    return rows


def isolated_transactions(rows, maximum_latency):
    transactions = []
    for index, request in enumerate(rows):
        if request["direction"] != "phone->proxy":
            continue
        responses = []
        cursor = index + 1
        while cursor < len(rows) and rows[cursor]["direction"] == "proxy->phone":
            responses.append(rows[cursor])
            cursor += 1
        if len(responses) != 1:
            continue
        response = responses[0]
        latency = response["time"] - request["time"]
        if latency <= maximum_latency:
            transactions.append((request, response, latency))
    return transactions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "csv_file",
        type=Path,
        nargs="?",
        default=Path("old/analysis/proxy_pdus.csv"),
    )
    parser.add_argument("--maximum-latency", type=float, default=0.2)
    arguments = parser.parse_args()

    rows = load_rows(arguments.csv_file)
    transactions = isolated_transactions(rows, arguments.maximum_latency)
    counts = Counter((request["length"], response["length"]) for request, response, _ in transactions)

    print("Isolated one-request/one-response transactions")
    print("The model labels are structural candidates, not decrypted results.\n")
    for request, response, latency in transactions:
        shape = (request["length"], response["length"])
        label, confidence = CANDIDATES.get(shape, ("unclassified", "none"))
        print(
            f"tx={request['frame']:4d}/{request['length']:2d} "
            f"rx={response['frame']:4d}/{response['length']:2d} "
            f"latency={latency * 1000:6.1f} ms "
            f"confidence={confidence:6s} {label}"
        )

    print("\nShape counts")
    for shape, count in counts.most_common():
        label, confidence = CANDIDATES.get(shape, ("unclassified", "none"))
        print(
            f"{shape[0]:2d}->{shape[1]:2d}: {count:2d} "
            f"confidence={confidence:6s} {label}"
        )


if __name__ == "__main__":
    main()
