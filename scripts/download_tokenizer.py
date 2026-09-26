import hashlib
import json
from pathlib import Path

import httpx
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Qwen/Qwen3-Embedding-8B"


def main() -> None:
    target = ROOT / "data/tokenizers/qwen3-embedding.json"
    if target.exists():
        Tokenizer.from_file(str(target))
        print("Verified existing tokenizer; no model weights downloaded")
        return
    with httpx.Client(follow_redirects=True, timeout=90) as client:
        info = client.get(f"https://huggingface.co/api/models/{MODEL}")
        info.raise_for_status()
        revision = info.json()["sha"]
        response = client.get(f"https://huggingface.co/{MODEL}/resolve/{revision}/tokenizer.json")
        response.raise_for_status()
    tokenizer = Tokenizer.from_str(response.text)
    assert tokenizer.get_vocab_size() > 100_000
    target.write_bytes(response.content)
    provenance = {
        "model": MODEL,
        "revision": revision,
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "file": "tokenizer.json",
    }
    target.with_suffix(".source.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps(provenance))


if __name__ == "__main__":
    main()
