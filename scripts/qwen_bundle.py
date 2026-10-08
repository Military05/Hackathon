"""Split/verify/restore the exact local Qwen GGUF for Git LFS.

Never handles .env, database files, credentials, or arbitrary folders.
The GGUF source file is not modified. Run from a cloned checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "models" / "qwen3-4b-q4-k-m"
PARTS = BUNDLE / "parts"
MANIFEST = BUNDLE / "manifest.json"
FILENAME = "Qwen3-4B-Q4_K_M.gguf"
EXPECTED_SHA256 = "d0c2ac093a77c402f2ddc23a64f68b7c70cfef151899b33e3a6066247104ced3"
CHUNK_BYTES = 512 * 1024 * 1024  # Under the GitHub Free/Pro LFS 2 GiB per-object limit.
BUFFER_BYTES = 8 * 1024 * 1024
PART_NAME = re.compile(r"^Qwen3-4B-Q4_K_M[.]gguf[.]part[0-9]{3}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(BUFFER_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def require_lfs() -> None:
    try:
        done = subprocess.run(["git", "lfs", "version"], cwd=ROOT, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("Git is missing. Install Git for Windows and Git LFS first.") from exc
    if done.returncode:
        raise RuntimeError("Git LFS is missing. Install Git LFS and run: git lfs install")


def pack(source_path: Path) -> None:
    require_lfs()
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file() or source_path.name != FILENAME:
        raise RuntimeError(f"Select the existing {FILENAME}, not a model directory.")
    print("Verifying original GGUF SHA256 (source remains unchanged)...", flush=True)
    actual = file_sha256(source_path)
    if actual.lower() != EXPECTED_SHA256:
        raise RuntimeError(f"Wrong GGUF SHA256: {actual}; expected {EXPECTED_SHA256}. No model was copied.")
    if MANIFEST.exists() or (PARTS.exists() and any(PARTS.iterdir())):
        raise RuntimeError("Bundle directory is not empty. Verify existing parts; never overwrite automatically.")
    PARTS.mkdir(parents=True, exist_ok=True)
    generated = []
    entries = []
    try:
        with source_path.open("rb") as source:
            index = 0
            while True:
                first = source.read(min(BUFFER_BYTES, CHUNK_BYTES))
                if not first:
                    break
                if index > 999:
                    raise RuntimeError("Too many parts")
                name = f"{FILENAME}.part{index:03d}"
                target = PARTS / name
                temp = PARTS / (name + ".tmp")
                digest = hashlib.sha256()
                written = 0
                with temp.open("xb") as part:
                    digest.update(first)
                    part.write(first)
                    written += len(first)
                    while written < CHUNK_BYTES:
                        block = source.read(min(BUFFER_BYTES, CHUNK_BYTES - written))
                        if not block:
                            break
                        digest.update(block)
                        part.write(block)
                        written += len(block)
                temp.rename(target)
                generated.append(target)
                entries.append({"name": name, "size_bytes": written, "sha256": digest.hexdigest()})
                print(f"Prepared {name}: {written} bytes", flush=True)
                index += 1
        if not entries:
            raise RuntimeError("Source file was empty")
        data = {
            "format": "contour-qwen-lfs-parts-v1",
            "filename": FILENAME,
            "sha256": EXPECTED_SHA256,
            "size_bytes": source_path.stat().st_size,
            "chunk_bytes": CHUNK_BYTES,
            "source": "lmstudio-community/Qwen3-4B-GGUF Q4_K_M",
            "origin_license": "Apache-2.0 (verify upstream model card before redistribution)",
            "parts": entries,
        }
        MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        for target in generated:
            target.unlink(missing_ok=True)
        for target in PARTS.glob("*.tmp"):
            target.unlink(missing_ok=True)
        raise
    print(f"Ready: {len(entries)} parts. Review Git LFS tracking before committing.")


def checked_parts():
    if not MANIFEST.is_file():
        raise RuntimeError("Model manifest is missing. Run 'pack' on the source computer.")
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if doc.get("format") != "contour-qwen-lfs-parts-v1" or doc.get("filename") != FILENAME:
        raise RuntimeError("Unexpected model manifest")
    if doc.get("sha256") != EXPECTED_SHA256:
        raise RuntimeError("Model digest does not match the pinned project model")
    entries = doc.get("parts")
    if not isinstance(entries, list) or not entries or len(entries) > 999:
        raise RuntimeError("Invalid parts list")
    if sum(int(e.get("size_bytes", -1)) for e in entries) != doc.get("size_bytes"):
        raise RuntimeError("Manifest sizes do not add up")
    for i, entry in enumerate(entries):
        name = entry.get("name", "")
        if not PART_NAME.fullmatch(name) or name != f"{FILENAME}.part{i:03d}":
            raise RuntimeError("Unsafe or unordered part name")
        part = PARTS / name
        if not part.is_file() or part.stat().st_size != entry["size_bytes"]:
            raise RuntimeError(f"Part is missing, or is just an LFS pointer: {name}")
        if file_sha256(part) != entry.get("sha256"):
            raise RuntimeError(f"Part SHA256 mismatch: {name}")
    return doc, entries


def verify() -> None:
    doc, entries = checked_parts()
    digest = hashlib.sha256()
    for entry in entries:
        with (PARTS / entry["name"]).open("rb") as source:
            for block in iter(lambda: source.read(BUFFER_BYTES), b""):
                digest.update(block)
    if digest.hexdigest() != doc["sha256"]:
        raise RuntimeError("Reassembled model checksum mismatch")
    print(f"Verified {len(entries)} parts, {doc['size_bytes']} bytes, SHA256 OK.")


def restore(destination: Path) -> None:
    destination = destination.expanduser().resolve()
    if destination.exists():
        if destination.is_file() and file_sha256(destination) == EXPECTED_SHA256:
            print(f"Exact model already installed: {destination}")
            return
        raise RuntimeError("Destination exists with different content. Refusing overwrite.")
    verify()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise RuntimeError(f"Temporary file exists: {temporary}. Check it manually.")
    try:
        with temporary.open("xb") as output:
            for part in sorted(PARTS.glob(f"{FILENAME}.part???")):
                with part.open("rb") as source:
                    for block in iter(lambda: source.read(BUFFER_BYTES), b""):
                        output.write(block)
        if file_sha256(temporary) != EXPECTED_SHA256:
            raise RuntimeError("Output checksum mismatch; destination not installed")
        temporary.rename(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Restored exact Qwen GGUF: {destination}")
    print("Open LM Studio/Bionic and confirm this file is recognized as Qwen3 4B Q4_K_M.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("verify", help="Verify all LFS parts and the complete model hash")
    p = sub.add_parser("pack", help="Split a local licensed GGUF into <=512 MiB LFS parts")
    p.add_argument("--source", required=True, type=Path)
    p = sub.add_parser("restore", help="Reassemble model from LFS parts on another computer")
    p.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "pack":
        pack(args.source)
    elif args.action == "verify":
        verify()
    else:
        restore(args.destination)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
