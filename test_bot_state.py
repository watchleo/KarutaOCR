from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot import KarutaClient


class BotStateTests(unittest.TestCase):
    def test_save_state_logs_write_errors_without_raising(self) -> None:
        error = OSError("disk full")
        store = SimpleNamespace(load=Mock(return_value={}), save=Mock(side_effect=error))
        client = SimpleNamespace(store=store, last_command_at=123.0)

        with patch("bot.log.error") as log_error:
            KarutaClient._save_state(client)

        log_error.assert_called_once_with("State write failed: %s", error)

    def test_scheduler_callback_logs_task_errors(self) -> None:
        error = RuntimeError("scheduler failed")
        task = SimpleNamespace(
            cancelled=Mock(return_value=False),
            exception=Mock(return_value=error),
        )

        with patch("bot.log.error") as log_error:
            KarutaClient._log_scheduler_result(task)

        log_error.assert_called_once_with("Scheduler died: %r", error)

    def test_scheduler_callback_ignores_cancellation(self) -> None:
        task = SimpleNamespace(cancelled=Mock(return_value=True))

        with patch("bot.log.error") as log_error:
            KarutaClient._log_scheduler_result(task)

        log_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
