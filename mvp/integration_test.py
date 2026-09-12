# -*- coding: utf-8 -*-
"""
Интеграционная проверка локального веб-приложения через настоящий HTTP-
цикл.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
temporary_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
temporary_file.close()
TEMP_DATABASE = Path(temporary_file.name)
shutil.copy2(BASE_DIR / "mvp.sqlite", TEMP_DATABASE)
ENVIRONMENT = os.environ.copy()
ENVIRONMENT["MVP_DB"] = str(TEMP_DATABASE)
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    PORT = str(listener.getsockname()[1])

process = subprocess.Popen(
    [sys.executable, "app.py", "--port", PORT],
    cwd=BASE_DIR,
    env=ENVIRONMENT,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

try:
    for attempt in range(100):
        if process.poll() is not None:
            raise RuntimeError(process.communicate()[1])
        try:
            with socket.create_connection(
                ("127.0.0.1", int(PORT)), timeout=0.1
            ):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("Сервер не запустился")
    cookie_handler = urllib.request.HTTPCookieProcessor()
    client = urllib.request.build_opener(cookie_handler)

    baseline = json.loads(
        client.open(f"http://127.0.0.1:{PORT}/api/summary", timeout=5).read()
    )
    home_response = client.open(f"http://127.0.0.1:{PORT}/", timeout=5)
    assert home_response.status == 200
    dashboard = home_response.read().decode("utf-8")
    assert "Сквозное измерение" in dashboard
    assert "Внешний канал А" in dashboard
    for route, title in (
        ("/registry", "Реестр размещений"),
        ("/quality", "Контроль качества измерения"),
    ):
        with client.open(f"http://127.0.0.1:{PORT}{route}", timeout=5) as page:
            assert page.status == 200
            assert title in page.read().decode("utf-8")
    with client.open(
        f"http://127.0.0.1:{PORT}/api/placements", timeout=5
    ) as response:
        assert len(json.load(response)) >= 4

    click = client.open(f"http://127.0.0.1:{PORT}/r/%D0%B01", timeout=5)
    final_url = click.geturl()
    click_id = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)[
        "click"
    ][0]

    data = urllib.parse.urlencode({"click": click_id}).encode()
    response = client.open(
        urllib.request.Request(
            f"http://127.0.0.1:{PORT}/bot-start", data=data, method="POST"
        ),
        timeout=5,
    )
    assert "Диалог и оплата" in response.read().decode("utf-8")

    user_id = None
    for cookie in cookie_handler.cookiejar:
        if cookie.name == "mvp_user":
            user_id = cookie.value
    assert user_id

    for invalid_amount in ("-1", "0", "inf", "nan", "abc"):
        invalid_data = urllib.parse.urlencode(
            {"click": click_id, "amount": invalid_amount}
        ).encode()
        try:
            client.open(
                urllib.request.Request(
                    f"http://127.0.0.1:{PORT}/purchase", data=invalid_data
                ),
                timeout=5,
            )
        except urllib.error.HTTPError as error:
            assert error.code == 400
            error.close()
        else:
            raise AssertionError("Некорректная сумма принята")

    data = urllib.parse.urlencode(
        {
            "user": user_id,
            "click": click_id,
            "interest": "Курс ПРО",
            "amount": "8950",
        }
    ).encode()
    response = client.open(
        urllib.request.Request(
            f"http://127.0.0.1:{PORT}/purchase", data=data, method="POST"
        ),
        timeout=5,
    )
    assert "Сквозной сценарий завершён" in response.read().decode("utf-8")
    receipt_url = response.geturl()
    assert "/receipt?order=" in receipt_url
    for _ in range(2):
        with client.open(receipt_url, timeout=5) as receipt:
            assert receipt.status == 200
    with client.open(
        urllib.request.Request(
            f"http://127.0.0.1:{PORT}/export", data=b"", method="POST"
        ),
        timeout=5,
    ) as download:
        assert download.headers["Content-Type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(download.read())) as archive:
            assert len(archive.namelist()) == 5
            assert "mvp_placements.csv" in archive.namelist()
    for asset in ("app.css", "app.js"):
        with client.open(
            f"http://127.0.0.1:{PORT}/static/{asset}", timeout=5
        ) as resource:
            assert resource.status == 200
            assert len(resource.read()) > 100

    summary = json.loads(
        client.open(f"http://127.0.0.1:{PORT}/api/summary", timeout=5)
        .read()
        .decode("utf-8")
    )
    assert summary["демо_заказы"] == baseline["демо_заказы"] + 1
    assert summary["оплаты"] == baseline["оплаты"] + 1
    print(
        (
            "Интеграционная проверка пройдена: переход, "
            "склейка, лид, заказ, оплата и пересчёт метрик "
            "работают."
        )
    )
finally:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    process.stdout.close()
    process.stderr.close()
    TEMP_DATABASE.unlink(missing_ok=True)
