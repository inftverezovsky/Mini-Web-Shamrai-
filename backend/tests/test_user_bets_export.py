import unittest
from io import BytesIO

from openpyxl import load_workbook

from src.api.users import _user_export_rows, _user_xlsx_response


async def _streaming_response_bytes(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks)


class UserBetsExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_export_contains_month_day_flat_stake_and_full_bet_columns(self):
        rows = _user_export_rows([
            {
                "resolved_at": "2026-06-10T15:00:00+03:00",
                "event_name": "Team A - Team B",
                "status": "win",
                "coefficient": 1.9,
                "profit_units": 0.9,
                "source_type": "feed",
                "sport_type": "Футбол",
                "bookmaker_names": ["Фонбет"],
                "outcome": "П1",
            }
        ])

        self.assertEqual(rows[0]["month"], "Июнь 2026")
        self.assertEqual(rows[0]["day"], "10.06.2026")
        self.assertEqual(rows[0]["flat_stake"], 1)
        self.assertEqual(rows[0]["result"], "Победа")

        response = _user_xlsx_response(rows, "my-bets.xlsx")
        content = await _streaming_response_bytes(response)
        wb = load_workbook(BytesIO(content))
        ws = wb["My Bets"]
        headers = [cell.value for cell in ws[1]]

        for header in [
            "Месяц",
            "День",
            "Вид спорта",
            "Матч",
            "БК",
            "КФ",
            "Ставка, флет",
            "Прибыль, флеты",
            "Результат",
        ]:
            self.assertIn(header, headers)

        self.assertLess(headers.index("Вид спорта"), headers.index("Матч"))
        self.assertLess(headers.index("БК"), headers.index("КФ"))
        self.assertLess(headers.index("Ставка, флет"), headers.index("Прибыль, флеты"))
        self.assertEqual(ws.cell(row=2, column=headers.index("Ставка, флет") + 1).value, 1)
        self.assertEqual(ws.cell(row=2, column=headers.index("Результат") + 1).value, "Победа")


if __name__ == "__main__":
    unittest.main()
