# -*- coding: utf-8 -*-
"""Сборка автономной демонстрационной базы."""

import csv
import os
import shutil
import tempfile
from pathlib import Path

from core import (
    BASE_DIR,
    check_quality,
    connect_database,
    create_database,
    create_demo_registry,
    get_database_path,
    import_forecast,
    import_marketing_history_csv,
    import_order_history,
    recalculate_attribution,
    restore_demo_events,
)


def load_scenario() -> None:
    """Загружает синтетические пути из результата этапа атрибуции."""
    events_path = BASE_DIR / "data" / "demo_events.csv"
    orders_path = BASE_DIR / "data" / "demo_orders.csv"
    users = {}
    with orders_path.open("r", encoding="utf-8-sig", newline="") as f:
        orders = list(csv.DictReader(f))
    with events_path.open("r", encoding="utf-8-sig", newline="") as f:
        events = list(csv.DictReader(f))

    with connect_database() as db:
        for row in orders:
            u = row["пользователь"]
            users[u] = True
            db.execute(
                "INSERT OR IGNORE INTO пользователи VALUES (?, ?, ?, ?)",
                (
                    u,
                    row["время_оплаты"],
                    "синтетическая детерминированная склейка",
                    "синтетические",
                ),
            )
        placement_map = {
            "Размещение А-1": "размещение_а",
            "Размещение Б-1": "размещение_б",
            "Собственный продающий пост": "размещение_свой",
            "Нативный пост": "размещение_свой",
            "Пост со скидкой": "размещение_свой",
            "Продающий пост запуска": "размещение_свой",
            "Прямой запуск": "размещение_органика",
        }
        seen = set()
        for row in events:
            if row["кандидат_в_атрибуцию"] != "1":
                continue
            grp = row["группа_касания"]
            if grp in seen:
                continue
            seen.add(grp)
            placement = placement_map.get(row["размещение"])
            if not placement:
                continue
            pid = f"демо_переход_{grp}"
            u = row["пользователь"]
            db.execute(
                "INSERT OR IGNORE INTO переходы VALUES (?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    placement,
                    u,
                    row["время_события"],
                    "синтетический путь этапа 5",
                    "синтетические",
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO события VALUES (?, ?, "
                "?, ?, ?, ?, ?, ?)",
                (
                    f"демо_событие_{grp}",
                    u,
                    pid,
                    placement,
                    "переход",
                    row["время_события"],
                    "{}",
                    "синтетические",
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO события VALUES (?, ?, "
                "?, ?, ?, ?, ?, ?)",
                (
                    f"демо_бот_{grp}",
                    u,
                    pid,
                    placement,
                    "запуск бота",
                    row["время_события"],
                    "{}",
                    "синтетические",
                ),
            )

        for row in orders:
            u = row["пользователь"]
            oid = row["заказ"]
            amount = float(row["сумма_оплаты"])
            timestamp = row["время_оплаты"]
            purchase_number = int(row["номер_покупки"])
            # Связываем лид с последним известным переходом пользователя до
            # оплаты, если он есть.
            p = db.execute(
                (
                    "SELECT переход_id FROM переходы WHERE "
                    "пользователь_id=? AND "
                    "datetime(время)<=datetime(?) ORDER BY "
                    "datetime(время) DESC LIMIT 1"
                ),
                (u, timestamp),
            ).fetchone()
            pid = p[0] if p else None
            lid = f"демо_лид_{oid}"
            db.execute(
                "INSERT OR IGNORE INTO лиды VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    lid,
                    u,
                    pid,
                    "демонстрационный курс",
                    timestamp,
                    "конвертирован",
                    "синтетические",
                ),
            )
            db.execute(
                (
                    "INSERT OR IGNORE INTO заказы VALUES (?, ?, ?, ?,"
                    " ?, ?, ?, NULL, ?)"
                ),
                (
                    oid,
                    u,
                    lid,
                    timestamp,
                    amount,
                    "оплачен",
                    purchase_number,
                    "синтетические",
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO оплаты VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"демо_оплата_{oid}",
                    oid,
                    u,
                    timestamp,
                    amount,
                    "успешно",
                    "синтетические",
                ),
            )
    recalculate_attribution()


def load_incrementality_example() -> None:
    path = BASE_DIR / "data" / "incrementality_metrics.csv"
    if not path.exists():
        return
    with (
        path.open("r", encoding="utf-8-sig", newline="") as f,
        connect_database() as db,
    ):
        for row in csv.DictReader(f):
            db.execute(
                "INSERT OR REPLACE INTO причинный_пример "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row["показатель"],
                    float(row["значение"]) if row["значение"] else None,
                    row["единица"],
                    row["пояснение"],
                    row["тип_данных"],
                ),
            )


def build_database() -> None:
    create_database()
    import_order_history(BASE_DIR / "data" / "real_orders.csv")
    import_marketing_history_csv(
        BASE_DIR / "data" / "historical_marketing_events.csv"
    )
    import_forecast(BASE_DIR / "data" / "forecast_7d_ru.csv")
    create_demo_registry()
    load_scenario()
    load_incrementality_example()
    restore_demo_events()


def main() -> None:
    # Собираем рядом с целевой базой: ошибка импорта не затронет рабочие
    # данные.
    target = get_database_path().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    previous_database = os.environ.get("MVP_DB")
    # Файл наследует права целевой папки, а не закрытой временной папки.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="seed_", suffix=".sqlite", dir=target.parent
    )
    os.close(descriptor)
    temporary_database = Path(temporary_name)
    try:
        os.environ["MVP_DB"] = str(temporary_database)
        build_database()
        if not all(x["успех"] for x in check_quality()
                   if x.get("уровень") != "предупреждение"):
            raise ValueError("Собранная база не прошла проверки качества")
        if target.exists():
            shutil.copy2(target, target.with_suffix(".sqlite.bak"))
        os.replace(temporary_database, target)
    finally:
        if previous_database is None:
            os.environ.pop("MVP_DB", None)
        else:
            os.environ["MVP_DB"] = previous_database
        temporary_database.unlink(missing_ok=True)
    print("База готова:", target)


if __name__ == "__main__":
    main()
