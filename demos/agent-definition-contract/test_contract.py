from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from validate_agent import SCHEMA_FILE, validate_file


HERE = Path(__file__).parent


class ContractValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def test_both_runner_examples_share_the_schema(self) -> None:
        examples = []
        for name in ("goose", "maf"):
            with self.subTest(name=name):
                path = HERE / "examples" / f"{name}.agent.yaml"
                self.assertEqual([], validate_file(path, self.validator))
                examples.append(yaml.safe_load(path.read_text(encoding="utf-8")))
        self.assertEqual(
            {"cve-investigation"}, {example["metadata"]["useCaseId"] for example in examples}
        )
        self.assertEqual(2, len({example["metadata"]["agentId"] for example in examples}))
        self.assertEqual(
            2, len({example["spec"]["implementation"]["revision"] for example in examples})
        )

    def test_schema_refs_can_be_omitted_for_a_minimal_candidate(self) -> None:
        document = self._example()
        del document["metadata"]["useCaseId"]
        for field in ("inputSchemaRef", "outputSchemaRef", "evaluationRef"):
            del document["spec"][field]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "minimal.yaml"
            path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
            self.assertEqual([], validate_file(path, self.validator))

    def test_present_schema_refs_must_be_safe_relative_json_paths(self) -> None:
        for field in ("inputSchemaRef", "outputSchemaRef", "evaluationRef"):
            for ref in ("/tmp/input.json", "../input.json", "https://example.com/input.json"):
                with self.subTest(field=field, ref=ref):
                    document = self._example()
                    document["spec"][field] = ref
                    self.assertTrue(self._issues(document))

    def test_secret_and_command_fields_are_rejected(self) -> None:
        for field, value in (
            ("secret", "not-a-real-secret"),
            ("command", "echo example"),
            ("digest", "sha256:" + "a" * 64),
        ):
            with self.subTest(field=field):
                document = self._example()
                document["spec"]["implementation"][field] = value
                self.assertTrue(self._issues(document))

    def test_duplicate_yaml_keys_are_rejected_before_schema_validation(self) -> None:
        source = (HERE / "examples" / "goose.agent.yaml").read_text(encoding="utf-8")
        source = source.replace("  version: 0.1.0", "  version: 0.1.0\n  version: 0.2.0", 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yaml"
            path.write_text(source, encoding="utf-8")
            self.assertIn("duplicate key", "\n".join(validate_file(path, self.validator)))

    @staticmethod
    def _example() -> dict:
        return yaml.safe_load((HERE / "examples" / "goose.agent.yaml").read_text(encoding="utf-8"))

    def _issues(self, document: dict) -> list:
        return list(self.validator.iter_errors(document))


if __name__ == "__main__":
    unittest.main()
