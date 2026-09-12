#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Локальное веб-приложение сквозной системы измерения без внешних
зависимостей.
"""

import argparse
import html
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core import (
    BASE_DIR,
    check_quality,
    connect_database,
    create_database,
    create_placement,
    create_purchase,
    export_reports,
    format_rubles,
    get_coverage,
    get_metrics,
    get_placement_report,
    register_click,
    start_bot,
)


def format_percent(value: float | None) -> str:
    """Форматирует долю; отсутствующее значение показывает прочерком."""
    return "—" if value is None else f"{value:.1%}"


def render_page(header: str, content: str) -> bytes:
    navigation = ""
    for url, title in (
        ("/", "Контрольная панель"),
        ("/registry", "Реестр"),
        ("/quality", "Качество"),
    ):
        active = "active" if header == title else ""
        navigation += f'<a class="{active}" href="{url}">{title}</a>'
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(header)} · Поступашки</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/app.js" defer></script></head><body>'
        '<aside class="sidebar"><a class="brand" href="/">'
        '<span class="brand-icon">П</span>Поступашки</a>'
        "<small>Маркетинг и аналитика</small>"
        f'<nav aria-label="Основная навигация">{navigation}</nav>'
        '<div class="sidebar-footer"><span class="status-dot"></span>'
        "Локальное приложение<br>Демонстрационный проект</div></aside>"
        '<div class="workspace"><header class="topbar">'
        f"<strong>{html.escape(header)}</strong>"
        "<span>Рабочее пространство / Поступашки</span>"
        '<div class="avatar" aria-label="Команда проекта">П</div></header>'
        f'<main class="обертка">{content}</main></div></body></html>'
    ).encode("utf-8")


def get_cookie_user(header: str | None) -> str | None:
    if not header:
        return None
    c = cookies.SimpleCookie()
    try:
        c.load(header)
    except cookies.CookieError:
        return None
    u = c.get("mvp_user").value if c.get("mvp_user") else None
    with connect_database() as db:
        return (
            u
            if u
            and db.execute(
                "SELECT 1 FROM пользователи WHERE пользователь_id=?", (u,)
            ).fetchone()
            else None
        )


class RequestHandler(BaseHTTPRequestHandler):
    def send_headers(
        self,
        status_code=200,
        content_type="text/html; charset=utf-8",
        cookie=None,
    ):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    @staticmethod
    def read_amount(value):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError as error:
            raise ValueError("Введите сумму числом, например 8950.") from error

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 65536:
            raise ValueError("Недопустимый размер формы")
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def do_GET(self):
        try:
            self.handle_get()
        except sqlite3.Error:
            self.show_database_error()

    def show_database_error(self):
        self.send_headers(503)
        self.wfile.write(
            render_page(
                "Временно недоступно",
                '<section class="error-card"><h1>Данные недоступны</h1>'
                "<p>Не удалось обратиться к базе. Повторите попытку позже.</p>"
                '<a class="кнопка" href="/">Повторить</a></section>',
            )
        )

    def handle_get(self):
        path = urlparse(self.path)
        if path.path in ("/static/app.css", "/static/app.js"):
            asset = BASE_DIR / "static" / path.path.rsplit("/", 1)[1]
            kind = "text/css" if asset.suffix == ".css" else "text/javascript"
            self.send_headers(200, kind + "; charset=utf-8")
            self.wfile.write(asset.read_bytes())
            return
        if path.path == "/receipt":
            return self.show_receipt(
                parse_qs(path.query).get("order", [""])[0]
            )
        if path.path == "/":
            return self.show_dashboard()
        if path.path == "/registry":
            return self.show_registry()
        if path.path == "/quality":
            return self.show_quality()
        if path.path.startswith("/r/"):
            return self.follow_link(unquote(path.path.split("/r/", 1)[1]))
        if path.path == "/start":
            return self.show_start(
                parse_qs(path.query).get("click", [None])[0]
            )
        if path.path == "/api/summary":
            self.send_headers(200, "application/json; charset=utf-8")
            self.wfile.write(
                json.dumps(get_metrics(), ensure_ascii=False).encode("utf-8")
            )
            return
        if path.path == "/api/placements":
            self.send_headers(200, "application/json; charset=utf-8")
            self.wfile.write(
                json.dumps(get_placement_report(), ensure_ascii=False).encode(
                    "utf-8"
                )
            )
            return
        self.send_headers(404)
        self.wfile.write(
            render_page("Не найдено", "<h1>Страница не найдена</h1>")
        )

    def do_POST(self):
        try:
            self.handle_post()
        except sqlite3.Error:
            self.show_database_error()
        except (ValueError, KeyError) as error:
            self.send_headers(400)
            self.wfile.write(
                render_page(
                    "Ошибка",
                    '<section class="error-card">'
                    "<h1>Проверьте данные формы</h1><p>"
                    + html.escape(str(error))
                    + '</p><button class="кнопка" type="button" data-back>'
                    + "Вернуться к форме</button>"
                    + ' <a href="/">На главную</a></section>',
                )
            )

    def handle_post(self):
        path = urlparse(self.path)
        if path.path == "/bot-start":
            return self.handle_bot_start()
        if path.path == "/purchase":
            return self.handle_purchase()
        if path.path == "/registry":
            return self.add_placement()
        if path.path == "/export":
            archive = io.BytesIO()
            with tempfile.TemporaryDirectory() as directory:
                export_reports(Path(directory))
                with zipfile.ZipFile(
                    archive, "w", zipfile.ZIP_DEFLATED
                ) as bundle:
                    for report in Path(directory).glob("*.csv"):
                        bundle.write(report, report.name)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", 'attachment; filename="reports.zip"'
            )
            self.end_headers()
            self.wfile.write(archive.getvalue())
            return
        self.send_headers(404)
        self.wfile.write(
            render_page("Не найдено", "<h1>Страница не найдена</h1>")
        )

    def show_dashboard(self):
        m = get_metrics()
        placements = get_placement_report()
        rows = ""
        for r in placements:
            romi = (
                "—" if r["окупаемость"] is None else f"{r['окупаемость']:.1%}"
            )
            css_class = (
                ""
                if r["окупаемость"] is None
                else ("хорошо" if r["окупаемость"] >= 0 else "плохо")
            )
            rows += (
                "<tr><td>"
                f"{html.escape(r['канал'])}"
                "</td><td>"
                f"{html.escape(r['креатив'])}"
                "</td><td>"
                f"{format_rubles(r['стоимость'])}"
                "</td><td>"
                f"{r['переходы']}"
                "</td><td>"
                f"{r['лиды']}"
                "</td><td>"
                f"{r['оплаченные_заказы']}"
                "</td><td>"
                f"{format_rubles(r['атрибутированная_выручка'])}"
                "</td><td class='"
                f"{css_class}"
                "'>"
                f"{romi}"
                "</td><td><a class='кнопка вторичная' href='/r/"
                f"{quote(r['токен'])}"
                "'>Открыть</a></td></tr>"
            )
        body = (
            "\n        <h1>Сквозное измерение: от разм"
            "ещения до выручки</h1>\n        <div clas"
            "s='заметка'><b>Важно.</b> Реальные истор"
            "ические продажи показаны отдельно и не а"
            "трибутируются задним числом. Маркетингов"
            "ая часть МВП использует явно отмеченные "
            "синтетические данные для проверки будуще"
            "го процесса.</div>\n        <div class='с"
            "екция'><h2>Реальная историческая база</h"
            "2><div class='сетка'>\n          <div cla"
            "ss='карта'><div class='подпись'>Реконстр"
            "уированные заказы</div><div class='число"
            "'>"
            f"{m['исторические_заказы']}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Наблюдаемая выруч"
            "ка</div><div class='число'>"
            f"{format_rubles(m['историческая_выручка'])}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Инерционный прогн"
            "оз на семь дней</div><div class='число'>"
            f"{format_rubles(m['прогноз_выручки_7д'])}"
            "</div></div>\n        </div></div>\n      "
            "  <div class='секция'><h2>Демонстрационн"
            "ый сквозной контур</h2><div class='сетка"
            "'>\n          <div class='карта'><div cla"
            "ss='подпись'>Переходы</div><div class='ч"
            "исло'>"
            f"{m['переходы']}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Запуски бота</div"
            "><div class='число'>"
            f"{m['запуски']}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Лиды</div><div cl"
            "ass='число'>"
            f"{m['лиды']}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Оплаты</div><div "
            "class='число'>"
            f"{m['оплаты']}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Оплаченная выручк"
            "а</div><div class='число'>"
            f"{format_rubles(m['демо_выручка'])}"
            "</div></div>\n        </div></div>\n      "
            "  <div class='секция'><h2>Размещения и а"
            "трибуционная окупаемость</h2><table clas"
            "s='таблица'><thead><tr><th>Канал</th><th"
            ">Креатив</th><th>Стоимость</th><th>Перех"
            "оды</th><th>Лиды</th><th>Оплаты</th><th>"
            "Атрибутированная выручка</th><th>Окупаем"
            "ость</th><th>Проверить ссылку</th></tr><"
            "/thead><tbody>"
            f"{rows}"
            "</tbody></table></div>\n        <div clas"
            "s='секция'><h2>Причинный контур — только"
            " синтетический пример</h2><div class='се"
            "тка'>\n          <div class='карта'><div "
            "class='подпись'>Дополнительная выручка</"
            "div><div class='число'>"
            f"{format_rubles(m['дополнительная_выручка_пример'] or 0)}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Окупаемость допол"
            "нительной маржи</div><div class='число'>"
            f"{format_percent(m['окупаемость_дополнительной_маржи_пример'])}"
            "</div></div>\n          <div class='карта"
            "'><div class='подпись'>Нижняя граница оц"
            "енки</div><div class='число'>"
            f"{format_percent(m['нижняя_граница_окупаемости_пример'])}"
            "</div></div>\n        </div><p class='под"
            "пись'>Этот блок показывает, что атрибуци"
            "онная окупаемость и причинная эффективно"
            "сть хранятся раздельно. Значения не отно"
            "сятся к исторической рекламе.</p></div>\n"
            "        <div class='секция'><form method"
            "='post' action='/export'><button class='"
            "кнопка' type='submit'>Обновить выгрузки<"
            "/button></form></div>\n        "
        )
        self.send_headers()
        chart = '<section class="chart"><h2>Выручка по каналам</h2>'
        chart += "<p>Атрибутированная выручка · синтетические данные</p>"
        maximum = max(
            (row["атрибутированная_выручка"] for row in placements), default=0
        )
        for row in placements:
            revenue = row["атрибутированная_выручка"]
            width = max(0, revenue / maximum * 100) if maximum > 0 else 0
            chart += (
                '<div class="bar-row">'
                f"<span>{html.escape(row['канал'])}</span>"
                '<div class="bar-track" aria-hidden="true">'
                f'<div class="bar-fill" style="width:{width:.1f}%"></div>'
                '</div><span class="bar-value">'
                f"{format_rubles(revenue)}</span></div>"
            )
        if not placements:
            chart += (
                '<p class="empty">Добавьте первое размещение в реестре.</p>'
            )
        chart += "</section>"
        toolbar = (
            '<div class="toolbar"><span class="badge">Обзор проекта</span>'
            '<a class="кнопка" href="/registry">+ Новое размещение</a></div>'
        )
        body = body.replace("Обновить выгрузки", "Скачать отчёты · ZIP")
        self.wfile.write(
            render_page("Контрольная панель", toolbar + body + chart)
        )

    def show_registry(self):
        rows = get_placement_report()
        trs = "".join(
            (
                "<tr><td>"
                f"{html.escape(r['канал'])}"
                "</td><td>"
                f"{html.escape(r['тип_канала'])}"
                "</td><td>"
                f"{html.escape(r['креатив'])}"
                "</td><td>"
                f"{format_rubles(r['стоимость'])}"
                "</td><td class='код'>/r/"
                f"{html.escape(r['токен'])}"
                "</td></tr>"
            )
            for r in rows
        )
        body = (
            "<h1>Реестр размещений</h1><p>Каждое соче"
            "тание кампании, канала и креатива получа"
            "ет отдельный токен. Стоимость фиксируетс"
            "я до расчёта окупаемости.</p>\n        <f"
            "orm method='post' action='/registry' cla"
            "ss='форма'>\n        <div class='поле'><l"
            "abel>Кампания</label><input name='campai"
            "gn' required value='Следующий запуск'></"
            "div>\n        <div class='поле'><label>Ка"
            "нал</label><input name='channel' require"
            "d></div>\n        <div class='поле'><labe"
            "l>Тип канала</label><select name='kind'>"
            "<option>платный внешний</option><option>"
            "собственный</option></select></div>\n    "
            "    <div class='поле'><label>Креатив</la"
            "bel><input name='creative' required></di"
            "v>\n        <div class='поле'><label>Пред"
            "ложение</label><input name='offer'></div"
            ">\n        <div class='поле'><label>Стоим"
            "ость, ₽</label><input name='cost' type='"
            "number' min='0' step='1' required></div>"
            "\n        <div class='широкое'><button cl"
            "ass='кнопка'>Создать отслеживаемую ссылк"
            "у</button></div></form>\n        <div cla"
            "ss='секция'><table class='таблица'><thea"
            "d><tr><th>Канал</th><th>Тип</th><th>Креа"
            "тив</th><th>Стоимость</th><th>Ссылка</th"
            "></tr></thead><tbody>"
            f"{trs}"
            "</tbody></table></div>"
        )
        self.send_headers()
        search = (
            '<div class="toolbar"><input class="search" id="placement-search"'
            ' type="search" placeholder="Поиск по каналу или креативу"'
            ' aria-label="Поиск размещений">'
            f'<span class="badge">Размещений: {len(rows)}</span></div>'
        )
        body = body.replace("<table", search + "<table", 1)
        self.wfile.write(render_page("Реестр", body))

    def show_quality(self):
        coverage_rows = get_coverage()
        validation = check_quality()
        coverage_html_rows = []
        for x in coverage_rows:
            ratio = "—" if x["доля"] is None else f"{x['доля']:.1%}"
            coverage_html_rows.append(
                (
                    "<tr><td>"
                    f"{x['показатель']}"
                    "</td><td>"
                    f"{x['числитель']}"
                    "/"
                    f"{x['знаменатель']}"
                    "</td><td>"
                    f"{ratio}"
                    "</td></tr>"
                )
            )
        coverage_html = "".join(coverage_html_rows)
        validation_html_rows = []
        for x in validation:
            if x.get("уровень") == "предупреждение":
                css_class = "предупреждение"
                status = "предупреждение"
            elif x["успех"]:
                css_class = "хорошо"
                status = "пройдена"
            else:
                css_class = "плохо"
                status = "ошибка"
            validation_html_rows.append(
                "<tr><td>"
                f"{x['проверка']}"
                "</td><td class='"
                f"{css_class}"
                "'>"
                f"{status}"
                "</td><td>"
                f"{x['детали']}"
                "</td></tr>"
            )
        validation_html = "".join(validation_html_rows)
        body = (
            "<h1>Контроль качества измерения</h1><div "
            "class='секция'><h2>Покрытие</h2><table "
            "class='таблица'><tbody>"
            f"{coverage_html}"
            "</tbody></table></div><div "
            "class='секция'><h2>Автоматические "
            "проверки</h2><table class='таблица'><tbody>"
            f"{validation_html}"
            "</tbody></table></div>"
        )
        self.send_headers()
        self.wfile.write(render_page("Качество", body))

    def follow_link(self, token):
        user_id = get_cookie_user(self.headers.get("Cookie"))
        try:
            p = register_click(token, user_id)
        except KeyError:
            self.send_headers(404)
            self.wfile.write(
                render_page("Ошибка", "<h1>Неизвестная ссылка</h1>")
            )
            return
        self.send_response(302)
        self.send_header("Location", f"/start?click={quote(p['переход_id'])}")
        self.end_headers()

    def show_start(self, click):
        if not click:
            self.send_headers(400)
            self.wfile.write(
                render_page("Ошибка", "<h1>Нет идентификатора перехода</h1>")
            )
            return
        with connect_database() as db:
            row = db.execute(
                (
                    "SELECT r.канал,r.креатив,p.пользователь_id "
                    "FROM переходы p JOIN "
                    "размещения r USING(размещение_id) WHERE "
                    "p.переход_id=?"
                ),
                (click,),
            ).fetchone()
        if not row:
            self.send_headers(404)
            self.wfile.write(
                render_page("Ошибка", "<h1>Переход не найден</h1>")
            )
            return
        u = get_cookie_user(self.headers.get("Cookie"))
        if not u or row["пользователь_id"] != u:
            body = (
                "<h1>Симулятор запуска бота</h1><p>Источник уже "
                "сохранён: <b>"
                f"{html.escape(row['канал'])}"
                "</b>, креатив «"
                f"{html.escape(row['креатив'])}"
                "».</p><form method='post' "
                "action='/bot-start'><input type='hidden' "
                "name='click' value='"
                f"{html.escape(click)}"
                "'><button class='кнопка'>Запустить бота и "
                "выполнить склейку</button></form>"
            )
        else:
            body = self.purchase_form(u, click, row["канал"])
        self.send_headers()
        self.wfile.write(render_page("Запуск бота", body))

    def purchase_form(self, u, click, channel):
        return (
            '<div class="заметка">Тестовая покупка: реальные деньги '
            "не списываются, платёжные данные не требуются.</div>"
            "<div class='успех'>Пользователь успешно связан с"
            " рекламным переходом. В аналитической базе "
            "хранится только внутренний "
            "идентификатор.</div><h1>Диалог и "
            "оплата</h1><p>Текущее касание: <b>"
            f"{html.escape(channel)}"
            "</b>.</p><p>Можно открыть другую отслеживаемую "
            "ссылку на панели, чтобы создать многокасательный"
            " путь, а затем вернуться к покупке.</p><form "
            "method='post' action='/purchase' "
            "class='форма'><input type='hidden' name='user' "
            "value='"
            f"{html.escape(u)}"
            "'><input type='hidden' name='click' value='"
            f"{html.escape(click)}"
            "'><div class='поле'><label>Интерес к "
            "продукту</label><input name='interest' "
            "value='Курс ПРО' required></div><div "
            "class='поле'><label>Сумма оплаты, "
            "₽</label><input name='amount' type='number' "
            "value='8950' min='0.01' step='0.01' "
            "required></div><div class='широкое'><button "
            "class='кнопка'>Подтвердить тестовую "
            "оплату</button></div></form>"
        )

    def handle_bot_start(self):
        f = self.read_form()
        existing_user = get_cookie_user(self.headers.get("Cookie"))
        if not f.get("click"):
            raise ValueError("Откройте ссылку размещения перед запуском.")
        u = start_bot(f.get("click"), existing_user)
        c = cookies.SimpleCookie()
        c["mvp_user"] = u
        c["mvp_user"]["path"] = "/"
        c["mvp_user"]["httponly"] = True
        c["mvp_user"]["samesite"] = "Lax"
        self.send_response(303)
        self.send_header(
            "Location", f"/start?click={quote(f.get('click', ''))}"
        )
        self.send_header("Set-Cookie", c.output(header="").strip())
        self.end_headers()

    def handle_purchase(self):
        f = self.read_form()
        u = get_cookie_user(self.headers.get("Cookie"))
        if not u:
            self.send_headers(400)
            self.wfile.write(
                render_page(
                    "Ошибка", "<h1>Сначала выполните склейку пользователя</h1>"
                )
            )
            return
        res = create_purchase(
            u,
            f.get("click"),
            f.get("interest", "Курс"),
            self.read_amount(f.get("amount", "")),
        )
        self.send_response(303)
        self.send_header(
            "Location", f"/receipt?order={quote(res['заказ_id'])}"
        )
        self.end_headers()

    def show_receipt(self, order_id):
        user_id = get_cookie_user(self.headers.get("Cookie"))
        with connect_database() as db:
            order = db.execute(
                "SELECT сумма FROM заказы "
                "WHERE заказ_id=? AND пользователь_id=?",
                (order_id, user_id),
            ).fetchone()
        if not order:
            self.send_headers(404)
            self.wfile.write(render_page("Ошибка", "<h1>Заказ не найден</h1>"))
            return
        body = (
            '<section class="error-card"><div class="step-label">Готово</div>'
            "<h1>Демонстрационная оплата зарегистрирована</h1>"
            '<div class="успех">Сквозной сценарий завершён.</div>'
            f"<h2>{format_rubles(order['сумма'])}</h2>"
            "<p>Реальные деньги не списывались. Заказ учтён в аналитике.</p>"
            f'<p class="код">{html.escape(order_id)}</p>'
            '<a class="кнопка" href="/">Перейти к аналитике</a></section>'
        )
        self.send_headers()
        self.wfile.write(render_page("Оплата", body))

    def add_placement(self):
        f = self.read_form()
        create_placement(
            f["campaign"],
            f["channel"],
            f["kind"],
            f["creative"],
            f.get("offer", ""),
            self.read_amount(f.get("cost", "")),
        )
        self.send_response(303)
        self.send_header("Location", "/registry?created=1")
        self.end_headers()

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(
        description="Локальный МВП измерительной системы"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Порт локального сервера"
    )
    parser.add_argument(
        "--db", help="Путь к SQLite-файлу (переопределяет MVP_DB)"
    )
    args = parser.parse_args()
    if args.db is not None:
        os.environ["MVP_DB"] = args.db
    try:
        create_database(False)
    except (OSError, ValueError, sqlite3.OperationalError) as error:
        parser.exit(1, f"Ошибка запуска: {error}\n")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RequestHandler)
    print(f"МВП запущен: http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
