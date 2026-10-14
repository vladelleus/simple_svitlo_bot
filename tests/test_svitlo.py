import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import svitlo  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "ztoe_2026-10-14.html"


def load_fixture():
    return FIXTURE.read_text(encoding="cp1251")


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.updated, self.schedule = svitlo.parse_schedule(load_fixture())

    def test_update_time(self):
        self.assertEqual(self.updated, "16:00 14.10.2026")

    def test_all_subqueues_present(self):
        self.assertEqual(list(self.schedule), ["14.10.2026"])
        expected = [f"{q}.{s}" for q in range(1, 7) for s in (1, 2)]
        self.assertEqual(sorted(self.schedule["14.10.2026"]), expected)
        for slots in self.schedule["14.10.2026"].values():
            self.assertEqual(len(slots), 48)

    def intervals(self, sub):
        slots = self.schedule["14.10.2026"][sub]
        return [svitlo.fmt_interval(a, b) for a, b in svitlo.to_intervals("14.10.2026", slots)]

    def test_intervals(self):
        self.assertEqual(self.intervals("2.2"), ["10:30–13:00", "16:00–18:00", "20:00–22:00"])
        self.assertEqual(self.intervals("3.1"), ["14:30–16:00"])
        self.assertEqual(self.intervals("4.2"), ["06:00–08:00", "14:30–16:00", "22:00–24:00"])


class NotifyTest(unittest.TestCase):
    """Several consecutive runs with a faked clock: no duplicate messages."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sent = []
        patches = [
            mock.patch.object(svitlo, "STATE_FILE", Path(tmp.name) / "state.json"),
            mock.patch.object(svitlo, "fetch_page", load_fixture),
            mock.patch.object(svitlo, "send_telegram", self.sent.append),
            mock.patch.object(sys, "argv", ["svitlo.py"]),
            mock.patch.dict(os.environ, {"SUBQUEUE": "2.2", "REMIND_MINUTES": "30"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.state_file = svitlo.STATE_FILE

    def run_at(self, hh, mm):
        now = datetime(2026, 10, 14, hh, mm, tzinfo=svitlo.TZ)
        fake = mock.Mock(wraps=datetime)
        fake.now.return_value = now
        with mock.patch.object(svitlo, "datetime", fake):
            before = len(self.sent)
            svitlo.main()
            return self.sent[before:]

    def test_schedule_then_reminders(self):
        first = self.run_at(9, 0)
        self.assertEqual(len(first), 1)
        self.assertIn("Графік на 14.10.2026", first[0])
        self.assertIn("10:30–13:00", first[0])

        self.assertEqual(self.run_at(9, 10), [])

        remind = self.run_at(10, 5)
        self.assertEqual(len(remind), 1)
        self.assertIn("через 25 хв", remind[0])

        self.assertEqual(self.run_at(10, 20), [])
        self.assertIn("через 10 хв", self.run_at(15, 50)[0])

    def test_changed_schedule(self):
        self.run_at(9, 0)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["schedule"]["14.10.2026"] = ["00:00–01:00"]
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        msgs = self.run_at(9, 10)
        self.assertEqual(len(msgs), 1)
        self.assertIn("Змінився графік", msgs[0])


def accident(acc_id, plan_in="2026-10-14T18:00:00+00:00", buildings="1, 2, 17а"):
    # Trimmed real record from api-ztoe-poweron.inneti.net/api/pw_accidents
    return {
        "id": acc_id,
        "dateEvent": "2026-10-14T16:00:00+00:00",
        "datePlanIn": plan_in,
        "koment": "ГПВ-2.2 зима",
        "switchSubReason": 2,
        "typeOff": 1,
        "buildingNames": buildings,
    }


class AccidentsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sent, self.live = [], []
        patches = [
            mock.patch.object(svitlo, "STATE_FILE", Path(tmp.name) / "state.json"),
            mock.patch.object(svitlo, "check_schedule", lambda *a: None),
            mock.patch.object(svitlo, "fetch_accidents", lambda city, street: self.live),
            mock.patch.object(svitlo, "send_telegram", self.sent.append),
            mock.patch.object(sys, "argv", ["svitlo.py"]),
            mock.patch.dict(os.environ, {"CITY_ID": "1", "STREET_ID": "2", "BUILDING": "17А"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_once(self):
        before = len(self.sent)
        svitlo.main()
        return self.sent[before:]

    def test_off_eta_change_on(self):
        self.assertEqual(self.run_once(), [])

        self.live = [accident(1)]
        msgs = self.run_once()
        self.assertEqual(len(msgs), 1)
        self.assertIn("Світло вимкнули", msgs[0])
        self.assertIn("Графік погодинних відключень", msgs[0])
        # API time is Kyiv local time despite the "+00:00" suffix
        self.assertIn("З 16:00 14.10, орієнтовно до 18:00 14.10", msgs[0])

        self.assertEqual(self.run_once(), [])

        self.live = [accident(1, plan_in="2026-10-14T19:30:00+00:00")]
        msgs = self.run_once()
        self.assertEqual(len(msgs), 1)
        self.assertIn("до 19:30", msgs[0])

        self.live = []
        msgs = self.run_once()
        self.assertEqual(len(msgs), 1)
        self.assertIn("Відключення завершилось", msgs[0])

    def test_other_buildings_ignored(self):
        self.live = [accident(1, buildings="3, 5, 7")]
        self.assertEqual(self.run_once(), [])

    def test_on_only_when_all_outages_end(self):
        self.live = [accident(1), accident(2)]
        self.assertEqual(len(self.run_once()), 2)
        self.live = [accident(2)]
        self.assertEqual(self.run_once(), [])
        self.live = []
        self.assertIn("Відключення завершилось", self.run_once()[0])

    def test_api_failure_keeps_state(self):
        self.live = [accident(1)]
        self.run_once()
        with mock.patch.object(svitlo, "fetch_accidents", side_effect=OSError("down")):
            with self.assertRaises(SystemExit):
                svitlo.main()
        # No false "power is back" after the API recovers
        self.assertEqual(self.run_once(), [])


if __name__ == "__main__":
    unittest.main()
