import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app


class StatusPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_status_page_renders_with_live_stats(self):
        fake_stats = {
            "guilds": 128,
            "users": 512,
            "uptime_human": "3h 20m",
            "latency_ms": 24,
            "online": True,
            "bot_name": "Jarvis",
        }

        def fake_fetch(path, timeout=5.0):
            if path == "/api/categories":
                return {"categories": {"🤖 AI": {"description": "AI", "commands": []}}}
            if path == "/api/stats":
                return fake_stats
            return None

        with patch("app._fetch_json", side_effect=fake_fetch):
            response = self.client.get("/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bot Status", response.text)
        self.assertIn("128", response.text)
        self.assertIn("512", response.text)


if __name__ == "__main__":
    unittest.main()
