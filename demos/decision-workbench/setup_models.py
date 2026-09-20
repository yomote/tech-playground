"""Explicitly download public model snapshots; inference never downloads models."""

import argparse
from pathlib import Path

from inference import CACHE_DIR, MODELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["all", *MODELS], default="all")
    args = parser.parse_args()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        parser.exit(1, "Install requirements.txt into this demo's .venv first.\n")
    selected = MODELS.items() if args.model == "all" else [(args.model, MODELS[args.model])]
    for model_id, spec in selected:
        print(f"Downloading {spec['repo']} @ {spec['revision']}", flush=True)
        path = snapshot_download(
            spec["repo"],
            revision=spec["revision"],
            cache_dir=str(CACHE_DIR),
            token=False,
            allow_patterns=["*.json", "*.safetensors", "*.model", "README.md"],
        )
        size = sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())
        print(f"{model_id}: {path} ({size / 1024**2:.1f} MiB)", flush=True)


if __name__ == "__main__":
    main()
