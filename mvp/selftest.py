# -*- coding: utf-8 -*-
"""Автоматическая проверка ключевой бизнес-логики."""

import os
import tempfile
from pathlib import Path

from core import (
    check_quality,
    create_database,
    create_placement,
    create_purchase,
    get_placement_report,
    register_click,
    start_bot,
)


def main():
    previous_database = os.environ.get("MVP_DB")
    try:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["MVP_DB"] = str(Path(directory) / "test.sqlite")
            create_database(clear=True)
            placement = create_placement(
                "Проверка",
                "Тестовый канал",
                "платный внешний",
                "Креатив",
                "Предложение",
                1000,
            )
            click = register_click(placement["токен"])
            user_id = start_bot(click["переход_id"])
            create_purchase(user_id, click["переход_id"], "Курс", 2000)
            row = get_placement_report()[0]
            assert row["переходы"] == 1
            assert row["оплаченные_заказы"] == 1
            assert abs(row["атрибутированная_выручка"] - 2000) < 0.01
            assert abs(row["окупаемость"] - 1.0) < 1e-9
            assert all(x["успех"] for x in check_quality())
            print("Все автоматические проверки МВП пройдены.")
    finally:
        if previous_database is None:
            os.environ.pop("MVP_DB", None)
        else:
            os.environ["MVP_DB"] = previous_database


if __name__ == "__main__":
    main()
