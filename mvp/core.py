# -*- coding: utf-8 -*-
"""Ядро демонстрационной системы измерения маркетинга."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "mvp.sqlite"
ATTRIBUTION_WINDOW_DAYS = 7


def get_database_path() -> Path:
    configured = os.environ.get("MVP_DB", "").strip()
    if len(configured) >= 2 and configured[0] == configured[-1]:
        if configured[0] in ("'", '"'):
            configured = configured[1:-1].strip()
    path = Path(configured).expanduser() if configured else DEFAULT_DATABASE
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    if path.is_dir():
        raise ValueError(
            f"Путь базы указывает на папку: {path}. "
            "Укажите файл, например mvp.sqlite."
        )
    return path


@contextmanager
def connect_database():
    path = get_database_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
    except (OSError, sqlite3.OperationalError) as error:
        raise sqlite3.OperationalError(
            f"Не удалось открыть базу {path}: {error}. "
            "Проверьте MVP_DB и права доступа к файлу и его папке."
        ) from error
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCHEMA = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")


def create_database(clear: bool = False) -> None:
    path = get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if clear and path.exists():
        path.unlink()
    with connect_database() as db:
        db.executescript(SCHEMA)
        version = db.execute(
            "SELECT значение FROM метаданные WHERE ключ='версия'"
        ).fetchone()
        migrate = not version or version[0] != "3.0"
        db.execute(
            "INSERT OR REPLACE INTO метаданные(ключ, значение) VALUES (?, ?)",
            ("версия", "3.0"),
        )
        db.execute(
            "INSERT OR REPLACE INTO метаданные(ключ, значение) VALUES (?, ?)",
            ("окно_атрибуции_дней", str(ATTRIBUTION_WINDOW_DAYS)),
        )

    if migrate:
        restore_demo_events()
        recalculate_attribution()


def import_order_history(csv_path: Path) -> None:
    """
    Импортирует реальные реконструированные заказы из этапа разведочного
    анализа.
    """
    with (
        connect_database() as db,
        csv_path.open("r", encoding="utf-8-sig", newline="") as file,
    ):
        reader = csv.DictReader(file)
        for row in reader:
            sid = str(row["покупатель"])
            user_id = f"ист_{sid}"
            db.execute(
                "INSERT OR IGNORE INTO пользователи VALUES (?, ?, ?, ?)",
                (
                    user_id,
                    row["время_заказа"],
                    "исторический псевдонимизированный идентификатор",
                    "реальные",
                ),
            )
            order_id = f"ист_заказ_{row['заказ']}"
            amount = float(row["выручка"])
            purchase_number = int(float(row["номер_наблюдаемой_покупки"]))
            db.execute(
                (
                    "INSERT OR IGNORE INTO заказы VALUES (?, ?, NULL,"
                    " ?, ?, ?, ?, ?, ?)"
                ),
                (
                    order_id,
                    user_id,
                    row["время_заказа"],
                    amount,
                    "оплачен",
                    purchase_number,
                    row["заказ"],
                    "реальные",
                ),
            )
            payment = f"ист_оплата_{row['заказ']}"
            db.execute(
                "INSERT OR IGNORE INTO оплаты VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    payment,
                    order_id,
                    user_id,
                    row["время_заказа"],
                    amount,
                    "успешно",
                    "реальные",
                ),
            )


def import_marketing_history(xlsx_path: Path) -> int:
    """
    Импортирует краткую публичную маркетинговую хронологию без внешних
    библиотек.      Для полной автономности приложения основной запуск
    не зависит от этого импорта.     Данные перед сборкой MVP
    предварительно выгружаются в отдельный CSV.
    """
    return 0


def import_marketing_history_csv(csv_path: Path) -> None:
    with (
        connect_database() as db,
        csv_path.open("r", encoding="utf-8-sig", newline="") as file,
    ):
        for row in csv.DictReader(file):
            db.execute(
                (
                    "INSERT OR REPLACE INTO "
                    "исторические_события_маркетинга VALUES (?, ?, ?,"
                    " ?, ?, ?, ?, ?)"
                ),
                (
                    row["идентификатор_события"],
                    row["дата_начала"],
                    row["дата_окончания"],
                    row["канал"],
                    row["вид"],
                    row["описание"],
                    row["качество_источника"],
                    row["тип_данных"],
                ),
            )


def import_forecast(csv_path: Path) -> None:
    with (
        connect_database() as db,
        csv_path.open("r", encoding="utf-8-sig", newline="") as file,
    ):
        for row in csv.DictReader(file):
            # CSV создаётся на этапе сборки с русскими колонками.
            db.execute(
                "INSERT OR REPLACE INTO прогноз VALUES (?, ?, ?, ?)",
                (
                    row["дата"],
                    float(row["заказы"]),
                    float(row["выручка"]),
                    row["тип_данных"],
                ),
            )


def create_demo_registry() -> None:
    """
    Создаёт минимальный реестр кампании, двух внешних и одного
    собственного размещения.
    """
    with connect_database() as db:
        db.execute(
            "INSERT OR IGNORE INTO кампании VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "кампания_демо",
                "Следующий запуск",
                "проверка измерительного контура",
                "2026-09-15",
                "2026-09-30",
                110000.0,
                "демонстрация",
                "синтетические",
            ),
        )
        rows = [
            (
                "размещение_а",
                "кампания_демо",
                "Внешний канал А",
                "платный внешний",
                "Креатив с ценой",
                "Курс за 8 950 ₽",
                "2026-09-15 12:00:00",
                50000.0,
                "RUB",
                "а1",
                "опубликовано",
                "синтетические",
            ),
            (
                "размещение_б",
                "кампания_демо",
                "Внешний канал Б",
                "платный внешний",
                "Креатив с кейсом",
                "Курс за 8 950 ₽",
                "2026-09-17 09:00:00",
                60000.0,
                "RUB",
                "б1",
                "опубликовано",
                "синтетические",
            ),
            (
                "размещение_свой",
                "кампания_демо",
                "Основной канал",
                "собственный",
                "Продающий пост",
                "Скидка до дедлайна",
                "2026-09-19 12:00:00",
                0.0,
                "RUB",
                "с1",
                "опубликовано",
                "синтетические",
            ),
            (
                "размещение_органика",
                "кампания_демо",
                "Прямой вход",
                "органический",
                "Прямой запуск",
                "Без рекламной метки",
                "2026-09-18 11:00:00",
                0.0,
                "RUB",
                "органика",
                "служебное",
                "синтетические",
            ),
        ]
        db.executemany(
            (
                "INSERT OR IGNORE INTO размещения VALUES (?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            rows,
        )


def create_placement(campaign_name, channel, channel_type, creative, offer,
                     cost, *, placement_name=None, published_at=None,
                     publication_url="", comment="", campaign_id=None):
    values = (campaign_name, channel, creative)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("Заполните кампанию, канал и креатив.")
    if channel_type not in ("платный внешний", "собственный", "органический"):
        raise ValueError("Неизвестный тип канала.")
    if cost is None or cost == "":
        if channel_type == "платный внешний":
            raise ValueError("Для платной рекламы обязательна стоимость.")
        cost = None
    else:
        cost = validate_amount(cost, allow_zero=True)
    published_at = published_at if published_at is not None else now()
    try:
        publication_time = datetime.fromisoformat(published_at)
        if publication_time.tzinfo is not None:
            raise ValueError
        published_at = publication_time.isoformat(sep=" ")
    except (ValueError, TypeError) as error:
        raise ValueError("Укажите дату и время публикации без часового пояса.") from error
    if publication_url:
        parsed = urlparse(publication_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Ссылка на публикацию должна начинаться с http(s)://.")
    placement_name = creative if placement_name is None else placement_name.strip()
    if not placement_name:
        raise ValueError("Укажите название размещения.")
    placement_id, token = new_id("placement"), secrets.token_urlsafe(12)
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        if campaign_id:
            campaign = db.execute(
                "SELECT * FROM кампании WHERE кампания_id=? "
                "AND тип_данных='синтетические'", (campaign_id,)
            ).fetchone()
            if not campaign:
                raise ValueError("Кампания не найдена.")
        else:
            campaign = db.execute(
                "SELECT * FROM кампании WHERE название=? "
                "AND тип_данных='синтетические' ORDER BY кампания_id LIMIT 1",
                (campaign_name.strip(),),
            ).fetchone()
        campaign_id = campaign["кампания_id"] if campaign else new_id("campaign")
        if not campaign:
            db.execute(
                "INSERT INTO кампании VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign_id, campaign_name.strip(), "измерение эффективности",
                 published_at[:10], None, cost, "активна", "синтетические"),
            )
        db.execute(
            "INSERT INTO размещения VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (placement_id, campaign_id, channel.strip(), channel_type,
             creative.strip(), offer.strip(), published_at, cost, "RUB", token,
             "опубликовано", "синтетические"),
        )
        db.execute(
            "INSERT INTO placement_details VALUES (?, ?, ?, ?)",
            (placement_id, placement_name, publication_url, comment),
        )
    return {"кампания_id": campaign_id, "размещение_id": placement_id,
            "токен": token, "tracking_path": f"/t/{token}"}


def register_click(token: str, user_id: str | None = None) -> dict:
    with connect_database() as db:
        placement = db.execute(
            "SELECT * FROM размещения WHERE токен = ?", (token,)
        ).fetchone()
        if not placement:
            raise KeyError("Неизвестная отслеживаемая ссылка")
        if (
            user_id
            and not db.execute(
                "SELECT 1 FROM пользователи WHERE пользователь_id=?",
                (user_id,),
            ).fetchone()
        ):
            user_id = None
        click_id = new_id("переход")
        timestamp = now()
        db.execute(
            "INSERT INTO переходы VALUES (?, ?, ?, ?, ?, ?)",
            (
                click_id,
                placement["размещение_id"],
                user_id,
                timestamp,
                "отслеживаемая ссылка",
                "синтетические",
            ),
        )
        db.execute(
            "INSERT INTO события VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("событие"),
                user_id,
                click_id,
                placement["размещение_id"],
                "переход",
                timestamp,
                "{}",
                "синтетические",
            ),
        )
        return {
            "переход_id": click_id,
            "размещение_id": placement["размещение_id"],
            "канал": placement["канал"],
        }


def start_bot(click_id, existing_user=None):
    user_key = existing_user or new_id("user")
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        if existing_user:
            require_user(db, existing_user)
        db.execute(
            "INSERT OR IGNORE INTO пользователи VALUES (?, ?, ?, ?)",
            (user_key, now(), "внутренний user_key, симулятор бота", "синтетические"),
        )
        if click_id:
            click = db.execute(
                "SELECT * FROM переходы WHERE переход_id=?", (click_id,)
            ).fetchone()
            if not click:
                raise ValueError("Переход не найден.")
            if click["пользователь_id"] not in (None, user_key):
                raise ValueError("Переход уже связан с другим пользователем.")
            db.execute("UPDATE переходы SET пользователь_id=? WHERE переход_id=?",
                       (user_key, click_id))
            db.execute("UPDATE события SET пользователь_id=? WHERE переход_id=?",
                       (user_key, click_id))
        duplicate = db.execute(
            "SELECT 1 FROM события WHERE пользователь_id=? "
            "AND переход_id IS ? AND тип_события='запуск бота'",
            (user_key, click_id),
        ).fetchone()
        if not duplicate:
            record_event(db, "запуск бота", user_key, click_id)
    return user_key


def create_purchase(user_id, click_id, interest, amount):
    """Совместимый быстрый сценарий; интерфейс использует отдельные шаги."""
    amount = validate_amount(amount)
    with connect_database() as db:
        require_user(db, user_id)
        require_click(db, user_id, click_id)
    lead_id = create_lead(user_id, click_id, interest)
    start_manager_dialogue(user_id, lead_id)
    order_id = create_order(user_id, lead_id, amount)
    payment_id = register_payment(user_id, order_id)
    return {"лид_id": lead_id, "заказ_id": order_id, "оплата_id": payment_id}


def attribution_weights(count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [1.0]
    if count == 2:
        return [0.5, 0.5]
    middle_weight = 0.2 / (count - 2)
    return [0.4] + [middle_weight] * (count - 2) + [0.4]


def recalculate_attribution(db=None):
    """Каноническое касание — переход; технические события не участвуют."""
    if db is None:
        with connect_database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recalculate_attribution(connection)
        return
    db.execute(
        "DELETE FROM атрибуция WHERE заказ_id IN "
        "(SELECT заказ_id FROM заказы WHERE тип_данных='синтетические')"
    )
    payments = db.execute(
        "SELECT p.*,o.номер_наблюдаемой_покупки FROM оплаты p "
        "JOIN заказы o USING(заказ_id) WHERE p.статус='успешно' "
        "AND p.тип_данных='синтетические' AND o.тип_данных='синтетические' "
        "ORDER BY julianday(p.время),p.rowid"
    ).fetchall()
    for payment in payments:
        paid_at = datetime.fromisoformat(payment["время"])
        since = (paid_at - timedelta(days=ATTRIBUTION_WINDOW_DAYS)).isoformat(sep=" ")
        touches = db.execute(
            "SELECT p.*,r.тип_канала FROM переходы p "
            "JOIN размещения r USING(размещение_id) "
            "WHERE p.пользователь_id=? AND p.тип_данных='синтетические' "
            "AND julianday(p.время) BETWEEN julianday(?) AND julianday(?) "
            "ORDER BY julianday(p.время),p.rowid",
            (payment["пользователь_id"], since, payment["время"]),
        ).fetchall()
        known = [touch for touch in touches if touch["тип_канала"] != "органический"]
        touches = known or touches
        if not touches:
            previous = db.execute(
                "SELECT COUNT(*) FROM оплаты WHERE пользователь_id=? "
                "AND статус='успешно' AND оплата_id<>? "
                "AND julianday(время)<julianday(?)",
                (payment["пользователь_id"], payment["оплата_id"], payment["время"]),
            ).fetchone()[0]
            category = ("повторная без свежего касания" if previous else "неизвестный источник")
            db.execute("INSERT INTO атрибуция VALUES (?, NULL, ?, 1, 1, 1, ?, ?)",
                       (payment["заказ_id"], category, payment["сумма"], "40–20–40, 7 дней"))
            continue
        weights = attribution_weights(len(touches))
        remaining = Decimal(str(payment["сумма"]))
        for index, (touch, weight) in enumerate(zip(touches, weights), 1):
            revenue = remaining if index == len(touches) else (
                Decimal(str(payment["сумма"])) * Decimal(str(weight))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            remaining -= revenue
            db.execute("INSERT INTO атрибуция VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (payment["заказ_id"], touch["размещение_id"], touch["тип_канала"],
                        index, len(touches), weight, float(revenue), "40–20–40, 7 дней"))


def get_metrics() -> dict:
    with connect_database() as db:
        historical = db.execute(
            (
                "SELECT COUNT(*) n, COALESCE(SUM(сумма),0) s FROM"
                " оплаты WHERE тип_данных='реальные' AND "
                "статус='успешно'"
            )
        ).fetchone()
        demo = db.execute(
            (
                "SELECT COUNT(*) n, COALESCE(SUM(сумма),0) s FROM"
                " оплаты WHERE тип_данных='синтетические' AND "
                "статус='успешно'"
            )
        ).fetchone()
        funnel = db.execute(
            (
                "SELECT\n                (SELECT COUNT(*) "
                "FROM переходы WHERE тип_данных='синтетич"
                "еские') переходы,\n                (SELEC"
                "T COUNT(*) FROM события WHERE тип_данных"
                "='синтетические' AND тип_события='запуск"
                " бота') запуски,\n                (SELECT"
                " COUNT(*) FROM лиды WHERE тип_данных='си"
                "нтетические') лиды,\n                (SEL"
                "ECT COUNT(*) FROM оплаты WHERE тип_данны"
                "х='синтетические' AND статус='успешно') "
                "оплаты"
            )
        ).fetchone()
        forecast = db.execute(
            (
                "SELECT COALESCE(SUM(выручка),0) s, "
                "COALESCE(SUM(заказы),0) n FROM прогноз"
            )
        ).fetchone()
        incrementality = {
            r["показатель"]: r["значение"]
            for r in db.execute(
                "SELECT показатель, значение FROM причинный_пример"
            ).fetchall()
        }
        return {
            "исторические_заказы": historical["n"],
            "историческая_выручка": historical["s"],
            "демо_заказы": db.execute(
                "SELECT COUNT(*) FROM заказы WHERE тип_данных='синтетические'"
            ).fetchone()[0],
            "пользователи": db.execute(
                "SELECT COUNT(DISTINCT пользователь_id) FROM переходы "
                "WHERE тип_данных='синтетические' AND пользователь_id IS NOT NULL"
            ).fetchone()[0],
            "кампании": db.execute("SELECT COUNT(*) FROM кампании WHERE тип_данных='синтетические'").fetchone()[0],
            "размещения": db.execute("SELECT COUNT(*) FROM размещения WHERE тип_данных='синтетические'").fetchone()[0],
            "стоимость_рекламы": db.execute("SELECT COALESCE(SUM(стоимость),0) FROM размещения WHERE тип_данных='синтетические' AND тип_канала='платный внешний'").fetchone()[0],
            "атрибутированная_выручка": db.execute("SELECT COALESCE(SUM(a.атрибутированная_выручка),0) FROM атрибуция a JOIN заказы o USING(заказ_id) WHERE o.тип_данных='синтетические'").fetchone()[0],
            "демо_выручка": demo["s"],
            "переходы": funnel["переходы"],
            "запуски": funnel["запуски"],
            "лиды": funnel["лиды"],
            "оплаты": funnel["оплаты"],
            "прогноз_заказов_7д": forecast["n"],
            "прогноз_выручки_7д": forecast["s"],
            "дополнительная_выручка_пример": incrementality.get(
                "Дополнительная выручка"
            ),
            "окупаемость_дополнительной_маржи_пример": incrementality.get(
                "Окупаемость по дополнительной марже при сценарной марже 70%"
            ),
            "нижняя_граница_окупаемости_пример": incrementality.get(
                "Нижняя граница окупаемости по дополнительной марже"
            ),
        }


def get_placement_report():
    with connect_database() as db:
        rows = db.execute(
            "SELECT r.*,c.название AS кампания,COALESCE(d.name,r.креатив) AS название,"
            "COALESCE(d.publication_url,'') AS ссылка_публикации,"
            "COALESCE(d.comment,'') AS комментарий "
            "FROM размещения r JOIN кампании c USING(кампания_id) "
            "LEFT JOIN placement_details d ON d.placement_id=r.размещение_id "
            "WHERE r.тип_данных='синтетические' ORDER BY r.rowid DESC"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            placement_id = row["размещение_id"]
            item["переходы"] = db.execute(
                "SELECT COUNT(*) FROM переходы WHERE размещение_id=?", (placement_id,)
            ).fetchone()[0]
            item["лиды"] = db.execute(
                "SELECT COUNT(*) FROM лиды l JOIN переходы p "
                "ON p.переход_id=l.исходный_переход_id WHERE p.размещение_id=?", (placement_id,)
            ).fetchone()[0]
            payment = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(pay.сумма),0) FROM оплаты pay "
                "JOIN заказы o USING(заказ_id) JOIN лиды l USING(лид_id) "
                "JOIN переходы p ON p.переход_id=l.исходный_переход_id "
                "WHERE p.размещение_id=? AND pay.статус='успешно'", (placement_id,)
            ).fetchone()
            item["оплаченные_заказы"], item["прямая_выручка"] = payment
            item["атрибутированная_выручка"] = db.execute(
                "SELECT COALESCE(SUM(a.атрибутированная_выручка),0) FROM атрибуция a "
                "JOIN заказы o USING(заказ_id) WHERE a.размещение_id=? "
                "AND o.тип_данных='синтетические'", (placement_id,)
            ).fetchone()[0]
            cost = item["стоимость"]
            item["окупаемость"] = None if cost is None or cost <= 0 else (
                item["атрибутированная_выручка"] - cost
            ) / cost
            result.append(item)
    return result


def get_coverage() -> list[dict]:
    with connect_database() as db:
        checks = [
            (
                "оплаты с идентификатором пользователя",
                (
                    "SELECT COUNT(*) FROM оплаты WHERE "
                    "тип_данных='синтетические' AND пользователь_id "
                    "IS NOT NULL"
                ),
                "SELECT COUNT(*) FROM оплаты WHERE тип_данных='синтетические'",
            ),
            (
                "лиды с исходным переходом",
                (
                    "SELECT COUNT(*) FROM лиды WHERE "
                    "тип_данных='синтетические' AND "
                    "исходный_переход_id IS NOT NULL"
                ),
                "SELECT COUNT(*) FROM лиды WHERE тип_данных='синтетические'",
            ),
            (
                "платные размещения с известной стоимостью",
                (
                    "SELECT COUNT(*) FROM размещения WHERE "
                    "тип_данных='синтетические' AND "
                    "тип_канала='платный внешний' AND стоимость IS "
                    "NOT NULL"
                ),
                (
                    "SELECT COUNT(*) FROM размещения WHERE "
                    "тип_данных='синтетические' AND "
                    "тип_канала='платный внешний'"
                ),
            ),
            (
                "размещения с временем публикации",
                (
                    "SELECT COUNT(*) FROM размещения WHERE "
                    "тип_данных='синтетические' AND время_публикации "
                    "IS NOT NULL"
                ),
                (
                    "SELECT COUNT(*) FROM размещения WHERE "
                    "тип_данных='синтетические'"
                ),
            ),
        ]
        out = []
        for name, numerator_sql, denominator_sql in checks:
            a = db.execute(numerator_sql).fetchone()[0]
            b = db.execute(denominator_sql).fetchone()[0]
            out.append(
                {
                    "показатель": name,
                    "числитель": a,
                    "знаменатель": b,
                    "доля": None if b == 0 else a / b,
                }
            )
        return out


def check_quality():
    """Только чтение: проверка не исправляет атрибуцию незаметно."""
    with connect_database() as db:
        result = [{"проверка": "целостность внешних ключей",
                   "успех": not db.execute("PRAGMA foreign_key_check").fetchall(),
                   "детали": "PRAGMA foreign_key_check"}]
        checks = {
            "платежи без заказов": "SELECT COUNT(*) FROM оплаты p LEFT JOIN заказы o USING(заказ_id) WHERE o.заказ_id IS NULL",
            "заказы без пользователей": "SELECT COUNT(*) FROM заказы o LEFT JOIN пользователи u USING(пользователь_id) WHERE u.пользователь_id IS NULL",
            "платные размещения без стоимости": "SELECT COUNT(*) FROM размещения WHERE тип_канала='платный внешний' AND стоимость IS NULL",
            "размещения без даты публикации": "SELECT COUNT(*) FROM размещения WHERE julianday(время_публикации) IS NULL",
            "веса атрибуции складываются в единицу": "SELECT COUNT(*) FROM (SELECT p.заказ_id FROM оплаты p LEFT JOIN атрибуция a USING(заказ_id) WHERE p.тип_данных='синтетические' AND p.статус='успешно' GROUP BY p.заказ_id HAVING ABS(COALESCE(SUM(a.вес),0)-1)>0.000001)",
            "атрибутированная выручка сходится с оплатой": "SELECT COUNT(*) FROM (SELECT p.заказ_id FROM оплаты p LEFT JOIN атрибуция a USING(заказ_id) WHERE p.тип_данных='синтетические' AND p.статус='успешно' GROUP BY p.заказ_id HAVING ABS(COALESCE(SUM(a.атрибутированная_выручка),0)-MAX(p.сумма))>0.001)",
            "история не атрибутирована": "SELECT COUNT(*) FROM атрибуция a JOIN заказы o USING(заказ_id) WHERE o.тип_данных='реальные'",
            "согласованность оплаты и заказа": "SELECT COUNT(*) FROM оплаты p JOIN заказы o USING(заказ_id) WHERE p.пользователь_id<>o.пользователь_id OR ABS(p.сумма-o.сумма)>0.001",
            "атрибуция без успешной оплаты": "SELECT COUNT(*) FROM атрибуция a LEFT JOIN оплаты p USING(заказ_id) WHERE p.оплата_id IS NULL OR p.статус<>'успешно'",
        }
        for label, query in checks.items():
            count = db.execute(query).fetchone()[0]
            result.append({"проверка": label, "успех": count == 0, "детали": f"Нарушений: {count}"})
        unknown = db.execute("SELECT COUNT(DISTINCT заказ_id) FROM атрибуция WHERE категория='неизвестный источник'").fetchone()[0]
        paid = db.execute("SELECT COUNT(*) FROM оплаты WHERE тип_данных='синтетические' AND статус='успешно'").fetchone()[0]
        ratio = f"{unknown / paid:.1%}" if paid else "нет оплат"
        result.append({"проверка": "доля неизвестных источников", "успех": unknown == 0,
                       "уровень": "предупреждение", "детали": f"{unknown}/{paid}: {ratio}. Это не органика."})
    return result


def export_reports(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)

    placements = []
    for r in get_placement_report():
        placements.append(
            {
                "идентификатор_размещения": r["размещение_id"],
                "канал": r["канал"],
                "тип_канала": r["тип_канала"],
                "креатив": r["креатив"],
                "стоимость": r["стоимость"],
                "токен": r["токен"],
                "переходы": r["переходы"],
                "лиды": r["лиды"],
                "оплаченные_заказы": r["оплаченные_заказы"],
                "прямая_выручка": r["прямая_выручка"],
                "атрибутированная_выручка": r["атрибутированная_выручка"],
                "окупаемость": r["окупаемость"],
            }
        )
    tables = {
        "mvp_placements.csv": (
            placements,
            [
                "идентификатор_размещения",
                "канал",
                "тип_канала",
                "креатив",
                "стоимость",
                "токен",
                "переходы",
                "лиды",
                "оплаченные_заказы",
                "прямая_выручка",
                "атрибутированная_выручка",
                "окупаемость",
            ],
        ),
        "mvp_coverage.csv": (
            get_coverage(),
            ["показатель", "числитель", "знаменатель", "доля"],
        ),
        "mvp_validation.csv": (
            [
                {**x, "успех": "да" if x["успех"] else "нет"}
                for x in check_quality()
            ],
            ["проверка", "успех", "уровень", "детали"],
        ),
    }
    for name, (rows, fields) in tables.items():
        with (directory / name).open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    with connect_database() as db:
        queries = {
            "mvp_attribution.csv": (
                "SELECT a.заказ_id AS заказ, a.размещение_id AS "
                "размещение, r.канал AS канал, a.категория AS "
                "категория, a.порядок_касания AS порядок_касания,"
                " a.всего_касаний AS всего_касаний, a.вес AS вес,"
                " a.атрибутированная_выручка AS "
                "атрибутированная_выручка, a.модель AS модель "
                "FROM атрибуция a LEFT JOIN размещения r "
                "USING(размещение_id) ORDER BY a.заказ_id, "
                "a.порядок_касания"
            ),
            "mvp_funnel.csv": (
                "SELECT r.размещение_id AS размещение, r.канал AS"
                " канал, COUNT(DISTINCT p.переход_id) AS "
                "переходы, COUNT(DISTINCT CASE WHEN "
                "e.тип_события='запуск бота' THEN "
                "e.пользователь_id END) AS запуски_бота, "
                "COUNT(DISTINCT l.лид_id) AS лиды, COUNT(DISTINCT"
                " CASE WHEN o.статус='оплачен' THEN o.заказ_id END) AS оплаты FROM размещения r LEFT "
                "JOIN переходы p USING(размещение_id) LEFT JOIN "
                "события e ON e.размещение_id=r.размещение_id "
                "LEFT JOIN лиды l ON "
                "l.исходный_переход_id=p.переход_id LEFT JOIN "
                "заказы o ON o.лид_id=l.лид_id WHERE "
                "r.тип_данных='синтетические' GROUP BY "
                "r.размещение_id, r.канал ORDER BY r.канал"
            ),
        }
        for name, query in queries.items():
            cur = db.execute(query)
            fields = [d[0] for d in cur.description]
            with (directory / name).open(
                "w", encoding="utf-8-sig", newline=""
            ) as f:
                w = csv.writer(f)
                w.writerow(fields)
                w.writerows(cur.fetchall())


def format_rubles(x: float) -> str:
    return f"{x:,.0f} ₽".replace(",", " ")


def validate_amount(value, *, allow_zero=False):
    """Проверяет конечную сумму с точностью до копейки."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > Decimal("1000000000000"):
            raise ValueError
        if amount < 0 or (not allow_zero and amount == 0):
            raise ValueError
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Укажите допустимую сумму с точностью до копейки.") from error
    return float(amount)


def require_user(db, user_key):
    user = db.execute(
        "SELECT * FROM пользователи WHERE пользователь_id=?", (user_key,)
    ).fetchone()
    if not user or user["тип_данных"] != "синтетические":
        raise ValueError("Демонстрационный пользователь не найден.")
    return user


def require_click(db, user_key, click_id):
    if not click_id:
        return None
    click = db.execute(
        "SELECT * FROM переходы WHERE переход_id=?", (click_id,)
    ).fetchone()
    if not click or click["пользователь_id"] != user_key:
        raise ValueError("Переход не принадлежит пользователю.")
    return click


def record_event(db, event_type, user_key, click_id=None, *,
                 lead_id=None, order_id=None, payment_id=None,
                 properties=None, timestamp=None, event_id=None):
    placement_id = None
    if click_id:
        click = db.execute(
            "SELECT размещение_id FROM переходы WHERE переход_id=?", (click_id,)
        ).fetchone()
        placement_id = click[0] if click else None
    event_id = event_id or new_id("event")
    db.execute(
        "INSERT OR IGNORE INTO события VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, user_key, click_id, placement_id, event_type,
         timestamp or now(), json.dumps(properties or {}, ensure_ascii=False),
         "синтетические"),
    )
    db.execute(
        "INSERT OR IGNORE INTO event_links VALUES (?, ?, ?, ?)",
        (event_id, lead_id, order_id, payment_id),
    )
    return event_id


def create_lead(user_key, click_id, product):
    product = product.strip()
    if not product:
        raise ValueError("Укажите продукт.")
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        require_user(db, user_key)
        require_click(db, user_key, click_id)
        lead_id = new_id("lead")
        db.execute(
            "INSERT INTO лиды VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lead_id, user_key, click_id, product, now(), "новый", "синтетические"),
        )
        record_event(db, "лид", user_key, click_id, lead_id=lead_id,
                     properties={"продукт": product})
    return lead_id


def require_lead(db, user_key, lead_id):
    require_user(db, user_key)
    lead = db.execute(
        "SELECT * FROM лиды WHERE лид_id=? AND пользователь_id=?",
        (lead_id, user_key),
    ).fetchone()
    if not lead:
        raise ValueError("Лид не принадлежит пользователю.")
    return lead


def start_manager_dialogue(user_key, lead_id, manager=""):
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        lead = require_lead(db, user_key, lead_id)
        existing = db.execute(
            "SELECT dialogue_id FROM manager_dialogues WHERE lead_id=?", (lead_id,)
        ).fetchone()
        if existing:
            return existing[0]
        dialogue_id = new_id("dialogue")
        db.execute(
            "INSERT INTO manager_dialogues VALUES (?, ?, ?, ?, ?, ?)",
            (dialogue_id, lead_id, user_key, lead["интерес"], now(), manager.strip()),
        )
        record_event(db, "диалог с менеджером", user_key,
                     lead["исходный_переход_id"], lead_id=lead_id,
                     properties={"менеджер": manager.strip(), "продукт": lead["интерес"]})
        db.execute("UPDATE лиды SET статус='в работе' WHERE лид_id=?", (lead_id,))
    return dialogue_id


def create_order(user_key, lead_id, amount):
    amount = validate_amount(amount)
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        lead = require_lead(db, user_key, lead_id)
        existing = db.execute(
            "SELECT заказ_id,сумма FROM заказы WHERE лид_id=?", (lead_id,)
        ).fetchone()
        if existing:
            if existing["сумма"] != amount:
                raise ValueError("Заказ уже создан с другой суммой.")
            return existing["заказ_id"]
        order_id = new_id("order")
        db.execute(
            "INSERT INTO заказы VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (order_id, user_key, lead_id, now(), amount, "ожидает оплаты", "синтетические"),
        )
        record_event(db, "заказ", user_key, lead["исходный_переход_id"],
                     lead_id=lead_id, order_id=order_id,
                     properties={"продукт": lead["интерес"], "сумма": amount})
    return order_id


def register_payment(user_key, order_id, amount=None):
    with connect_database() as db:
        db.execute("BEGIN IMMEDIATE")
        require_user(db, user_key)
        order = db.execute(
            "SELECT * FROM заказы WHERE заказ_id=? AND пользователь_id=?",
            (order_id, user_key),
        ).fetchone()
        if not order:
            raise ValueError("Заказ не принадлежит пользователю.")
        amount = validate_amount(order["сумма"] if amount is None else amount)
        if amount != order["сумма"]:
            raise ValueError("Сумма оплаты должна совпадать с суммой заказа.")
        existing = db.execute("SELECT * FROM оплаты WHERE заказ_id=?", (order_id,)).fetchone()
        if existing:
            if existing["статус"] != "успешно":
                raise ValueError("У заказа уже есть платёж с другим статусом.")
            return existing["оплата_id"]
        payment_id, timestamp = new_id("payment"), now()
        number = db.execute(
            "SELECT COUNT(*)+1 FROM оплаты WHERE пользователь_id=? AND статус='успешно'",
            (user_key,),
        ).fetchone()[0]
        db.execute("INSERT INTO оплаты VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (payment_id, order_id, user_key, timestamp, amount,
                    "успешно", "синтетические"))
        db.execute(
            "UPDATE заказы SET статус='оплачен',номер_наблюдаемой_покупки=? WHERE заказ_id=?",
            (number, order_id),
        )
        lead = require_lead(db, user_key, order["лид_id"])
        db.execute("UPDATE лиды SET статус='конвертирован' WHERE лид_id=?", (order["лид_id"],))
        record_event(db, "оплата", user_key, lead["исходный_переход_id"],
                     lead_id=order["лид_id"], order_id=order_id,
                     payment_id=payment_id, properties={"сумма": amount}, timestamp=timestamp)
        recalculate_attribution(db)
        check = db.execute(
            "SELECT SUM(вес),SUM(атрибутированная_выручка) FROM атрибуция WHERE заказ_id=?",
            (order_id,),
        ).fetchone()
        if abs(check[0] - 1) > 1e-9 or abs(check[1] - amount) > 0.001:
            raise ValueError("Не удалось согласовать атрибуцию оплаты.")
    return payment_id


def get_efficiency_report(level="размещение"):
    keys = {"размещение": "размещение_id", "кампания": "кампания_id",
            "канал": "канал", "креатив": "креатив"}
    if level not in keys:
        raise ValueError("Неизвестный уровень отчёта.")
    groups = {}
    for row in get_placement_report():
        key = row[keys[level]]
        if key not in groups:
            label = row["название"] if level == "размещение" else row[level]
            groups[key] = {"ключ": key, "название": label, "стоимость": 0,
                           "переходы": 0, "лиды": 0, "оплаченные_заказы": 0,
                           "прямая_выручка": 0, "атрибутированная_выручка": 0,
                           "неизвестная_стоимость": False}
        group = groups[key]
        group["неизвестная_стоимость"] |= row["стоимость"] is None
        group["стоимость"] += row["стоимость"] or 0
        for metric in ("переходы", "лиды", "оплаченные_заказы", "прямая_выручка", "атрибутированная_выручка"):
            group[metric] += row[metric]
    for group in groups.values():
        if group["неизвестная_стоимость"]:
            group["стоимость"] = None
        cost = group["стоимость"]
        group["окупаемость"] = None if cost is None or cost <= 0 else (
            group["атрибутированная_выручка"] - cost
        ) / cost
    return list(groups.values())


def get_events(user_key=None):
    with connect_database() as db:
        rows = db.execute(
            "SELECT e.*,p.источник,c.название AS кампания,"
            "COALESCE(d.name,r.креатив) AS размещение,r.канал,"
            "links.lead_id,links.order_id,links.payment_id "
            "FROM события e LEFT JOIN переходы p USING(переход_id) "
            "LEFT JOIN размещения r ON r.размещение_id=e.размещение_id "
            "LEFT JOIN кампании c ON c.кампания_id=r.кампания_id "
            "LEFT JOIN placement_details d ON d.placement_id=r.размещение_id "
            "LEFT JOIN event_links links ON links.event_id=e.событие_id "
            "WHERE e.тип_данных='синтетические' "
            "AND (? IS NULL OR e.пользователь_id=?) ORDER BY julianday(e.время),e.rowid",
            (user_key, user_key),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["свойства"] = json.loads(item["свойства"] or "{}")
            result.append(item)
    return result


def get_order_attribution(order_id):
    with connect_database() as db:
        return [dict(row) for row in db.execute(
            "SELECT a.*,r.канал FROM атрибуция a LEFT JOIN размещения r USING(размещение_id) "
            "WHERE a.заказ_id=? ORDER BY a.порядок_касания", (order_id,)
        )]


def restore_demo_events():
    """Восстанавливает события только из существующих синтетических записей."""
    with connect_database() as db:
        for lead in db.execute("SELECT * FROM лиды WHERE тип_данных='синтетические'").fetchall():
            exists = db.execute("SELECT 1 FROM event_links WHERE lead_id=?", (lead["лид_id"],)).fetchone()
            if not exists:
                record_event(db, "лид", lead["пользователь_id"], lead["исходный_переход_id"],
                             lead_id=lead["лид_id"], timestamp=lead["время"],
                             event_id="restored_lead_" + lead["лид_id"],
                             properties={"восстановлено_из_демобазы": True})
        for order in db.execute("SELECT * FROM заказы WHERE тип_данных='синтетические'").fetchall():
            lead = db.execute("SELECT * FROM лиды WHERE лид_id=?", (order["лид_id"],)).fetchone()
            click_id = lead["исходный_переход_id"] if lead else None
            exists = db.execute("SELECT 1 FROM event_links WHERE order_id=?", (order["заказ_id"],)).fetchone()
            if not exists:
                record_event(db, "заказ", order["пользователь_id"], click_id,
                             lead_id=order["лид_id"], order_id=order["заказ_id"], timestamp=order["время"],
                             event_id="restored_order_" + order["заказ_id"],
                             properties={"сумма": order["сумма"], "восстановлено_из_демобазы": True})
            for payment in db.execute("SELECT * FROM оплаты WHERE заказ_id=?", (order["заказ_id"],)).fetchall():
                if not db.execute("SELECT 1 FROM event_links WHERE payment_id=?", (payment["оплата_id"],)).fetchone():
                    record_event(db, "оплата", order["пользователь_id"], click_id,
                                 lead_id=order["лид_id"], order_id=order["заказ_id"],
                                 payment_id=payment["оплата_id"], timestamp=payment["время"],
                                 event_id="restored_payment_" + payment["оплата_id"],
                                 properties={"сумма": payment["сумма"], "восстановлено_из_демобазы": True})
