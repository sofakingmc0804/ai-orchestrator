import json
import tempfile
import unittest
from pathlib import Path

import ai_governor


class GovernorRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ai_governor.initialize_governor(self.root)
        self.gov = ai_governor.Governor(self.root)

    def tearDown(self):
        try:
            self.gov.close()
        finally:
            self.tmp.cleanup()

    def test_ocr_routes_to_local_ocr_or_vision_model(self):
        route = self.gov.select_model(
            task_type="ocr",
            required_capabilities=["ocr"],
            context_tokens=2000,
            consequence="normal",
            value_reason=None,
        )

        self.assertEqual(route["decision"], "allow")
        self.assertIn(route["model_id"], {"glm-ocr:latest", "qwen2.5vl:3b", "gemma4:e2b", "gemma4:e4b"})
        self.assertEqual(route["billing_class"], "local_resource")

    def test_embedding_routes_to_embedding_model_only(self):
        route = self.gov.select_model(
            task_type="embedding",
            required_capabilities=["embedding"],
            context_tokens=1200,
            consequence="normal",
            value_reason=None,
        )

        self.assertEqual(route["decision"], "allow")
        self.assertIn("embedding", route["capabilities"])
        self.assertEqual(route["billing_class"], "local_resource")

    def test_bulk_classification_uses_local_resource(self):
        route = self.gov.select_model(
            task_type="classification",
            required_capabilities=["classification"],
            context_tokens=8000,
            consequence="normal",
            value_reason=None,
            bulk=True,
        )

        self.assertEqual(route["decision"], "allow")
        self.assertEqual(route["billing_class"], "local_resource")

    def test_unknown_and_metered_extra_providers_are_denied(self):
        route = self.gov.select_model(
            task_type="architecture",
            required_capabilities=["reasoning"],
            context_tokens=10000,
            consequence="high",
            value_reason="hard architecture review",
            provider_allowlist=["openrouter", "openai-api"],
        )

        self.assertEqual(route["decision"], "deny")
        self.assertIn("metered_extra_cost", route["reason"])

    def test_low_subscription_quota_preserves_reserve(self):
        self.gov.update_quota("github-copilot", remaining=100, entitlement=1500, percent_remaining=6.6)

        route = self.gov.select_model(
            task_type="code_review",
            required_capabilities=["coding", "reasoning"],
            context_tokens=12000,
            consequence="high",
            value_reason="final critique",
            provider_allowlist=["github-copilot"],
        )

        self.assertEqual(route["decision"], "deny")
        self.assertIn("reserve", route["reason"])

    def test_high_value_can_escalate_to_included_subscription_when_quota_is_healthy(self):
        self.gov.update_quota("github-copilot", remaining=1200, entitlement=1500, percent_remaining=80.0)

        route = self.gov.select_model(
            task_type="code_review",
            required_capabilities=["coding", "reasoning"],
            context_tokens=12000,
            consequence="high",
            value_reason="final critique",
            provider_allowlist=["github-copilot"],
        )

        self.assertEqual(route["decision"], "allow")
        self.assertEqual(route["provider_id"], "github-copilot")
        self.assertEqual(route["billing_class"], "subscription_quota")

    def test_project_record_contains_route_policy(self):
        project_root = self.root / "ExampleProject"
        project_root.mkdir()
        (project_root / "AGENTS.md").write_text("# Example\n", encoding="utf-8")

        records = self.gov.discover_projects([project_root])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["default_local_model"], "qwen3.5:4b")
        self.assertIn("metered_extra_cost", records[0]["forbidden_billing_classes"])

    def test_receipt_written_for_each_decision(self):
        route = self.gov.select_model(
            task_type="embedding",
            required_capabilities=["embedding"],
            context_tokens=100,
            consequence="normal",
            value_reason=None,
        )

        receipt = Path(route["receipt_path"])
        self.assertTrue(receipt.exists())
        data = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(data["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
