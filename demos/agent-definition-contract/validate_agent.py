#!/usr/bin/env python3
"""Validate declarative AgentDefinition YAML without registering or running it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver


SCHEMA_FILE = Path(__file__).with_name("agent-definition.schema.json")
MAX_YAML_BYTES = 1024 * 1024


class StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML subset with unique string keys and no aliases or merge keys."""

    def compose_node(self, parent: object, index: object) -> yaml.Node:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ConstructorError(None, None, "YAML aliases are not supported", event.start_mark)
        return super().compose_node(parent, index)


def construct_unique_mapping(
    loader: StrictSafeLoader, node: yaml.Node, deep: bool = False
) -> dict[str, object]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(None, None, "a mapping is required", node.start_mark)
    values: dict[str, object] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ConstructorError(None, None, "YAML merge keys are not supported", key_node.start_mark)
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ConstructorError(None, None, "mapping keys must be strings", key_node.start_mark)
        if key in values:
            raise ConstructorError(None, None, f"duplicate key: {key}", key_node.start_mark)
        values[key] = loader.construct_object(value_node, deep=deep)
    return values


StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping)


def validate_file(path: Path, validator: Draft202012Validator) -> list[str]:
    try:
        if path.stat().st_size > MAX_YAML_BYTES:
            return [f"{path}: YAML exceeds {MAX_YAML_BYTES} bytes"]
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=StrictSafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [f"{path}: {exc}"]

    issues = sorted(
        validator.iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    return [
        f"{path}: {'/'.join(str(part) for part in issue.absolute_path) or '$'}: {issue.message}"
        for issue in issues
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="+", type=Path, help="AgentDefinition YAML file")
    args = parser.parse_args()

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    failed = False
    for path in args.manifest:
        issues = validate_file(path, validator)
        if issues:
            failed = True
            for issue in issues:
                print(issue, file=sys.stderr)
        else:
            print(f"valid: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
