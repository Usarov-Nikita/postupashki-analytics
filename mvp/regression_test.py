"""
Регрессионные проверки ввода, склейки и безопасной сборки базы.
"""

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import seed


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "test.sqlite"
        env = patch.dict(os.environ, {"MVP_DB": str(self.db)})
        env.start()
        self.addCleanup(env.stop)
        core.create_database()
        self.r = core.create_placement(
            "Тест", "Канал", "платный внешний", "Креатив", "", 100
        )
        self.click = core.register_click(self.r["токен"])["переход_id"]
        self.user = core.start_bot(self.click)

    def test_empty_database_setting(self):
        with patch.object(core, "DEFAULT_DATABASE", self.db):
            for value in ("", "  ", '""'):
                with patch.dict(os.environ, {"MVP_DB": value}):
                    self.assertEqual(
                        core.get_database_path(), self.db.resolve()
                    )
                    core.create_database()

    def test_relative_and_quoted_database_paths(self):
        with patch.object(core, "BASE_DIR", Path(self.tmp.name)):
            with patch.dict(os.environ, {"MVP_DB": '"nested/test.sqlite"'}):
                expected = Path(self.tmp.name) / "nested" / "test.sqlite"
                self.assertEqual(core.get_database_path(), expected.resolve())
                core.create_database()
                self.assertTrue(expected.is_file())

    def test_directory_database_path(self):
        with patch.dict(os.environ, {"MVP_DB": self.tmp.name}):
            with self.assertRaisesRegex(ValueError, "папку"):
                core.create_database()

    def test_invalid_amounts_are_atomic(self):
        for amount in (-100, 0, float("inf"), float("nan")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                core.create_purchase(self.user, self.click, "Курс", amount)
        self.assertEqual(core.get_metrics()["оплаты"], 0)
        with core.connect_database() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM лиды").fetchone()[0], 0
            )

    def test_click_cannot_change_owner(self):
        with self.assertRaises(ValueError):
            core.start_bot(self.click)
        other = core.start_bot(None)
        with self.assertRaises(ValueError):
            core.create_purchase(other, self.click, "Курс", 100)
        with core.connect_database() as db:
            owner = db.execute(
                "SELECT пользователь_id FROM переходы WHERE переход_id=?",
                (self.click,),
            ).fetchone()[0]
        self.assertEqual(owner, self.user)

    def test_stale_cookie_and_unknown_click(self):
        core.register_click(self.r["токен"], "missing")
        with self.assertRaises(ValueError):
            core.start_bot("missing")

    def test_seed_failure_preserves_database(self):
        original = self.db.read_bytes()
        with patch.object(
            seed, "import_order_history", side_effect=ValueError("bad CSV")
        ):
            with self.assertRaises(ValueError):
                seed.main()
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(os.environ["MVP_DB"], str(self.db))

    def test_export_reports(self):
        core.create_purchase(self.user, self.click, "Курс", 200)
        export_dir = Path(self.tmp.name) / "exports"
        core.export_reports(export_dir)
        self.assertEqual(len(list(export_dir.glob("*.csv"))), 5)
        with (export_dir / "mvp_placements.csv").open(
            encoding="utf-8-sig", newline=""
        ) as report:
            rows = list(csv.DictReader(report))
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["атрибутированная_выручка"]), 200)

    def test_seed_rebuild(self):
        for _ in range(2):
            seed.main()
            summary = core.get_metrics()
            self.assertEqual(summary["исторические_заказы"], 628)
            self.assertEqual(summary["оплаты"], 13)
            self.assertAlmostEqual(summary["демо_выручка"], 133920)
            self.assertTrue(all(x["успех"] for x in core.check_quality()
                     if x.get("уровень") != "предупреждение"))
        self.assertTrue(self.db.with_suffix(".sqlite.bak").is_file())


if __name__ == "__main__":
    unittest.main()
