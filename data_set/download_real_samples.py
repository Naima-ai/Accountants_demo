"""
download_real_samples.py
This script
pulls a handful of real images from two of them and saves them as real
files in data_set/, ready to run through test_pipeline_on_file.py.

Datasets used:
  - darentang/sroie   -- real photographed retail receipts (SROIE)
  - naver-clova-ix/cord-v2 -- real photographed receipts (CORD)
Both are public, no Hugging Face login needed. (FUNSD, also referenced
in data_load.py, IS gated and needs `huggingface-cli login` -- skipped
here to avoid that friction; ask if you want it later.)

Note: these are real receipts, but not Italian -- SROIE is mostly
Malaysian retail, CORD mostly Indonesian. Don't expect the Chart of
Accounts categorization to make sense on these; the point is stress-
testing OCR/classification/extraction against real photographed messiness
(angles, lighting, folds) that a synthetic image can't replicate.

Setup (once):
    pip install datasets huggingface_hub

Run (from inside demo_1/ or data_set/, doesn't matter -- uses absolute-ish paths):
    python download_real_samples.py
"""

import os

from datasets import load_dataset, load_from_disk

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "samples", "images")
os.makedirs(OUTPUT_DIR, exist_ok=True)
N_SAMPLES = 10

# If you already have SROIE saved locally (a folder with train/, test/,
# dataset_dict.json -- the output of datasets' save_to_disk()), put its
# full path here to load it directly instead of downloading again. Leave
# empty to stream from the Hub -- note darentang/sroie currently fails
# under datasets>=3.0 ("Dataset scripts are no longer supported"), an
# upstream incompatibility, not something a local path works around.
LOCAL_SROIE_PATH = ""


def save_samples_from_disk(local_path: str, prefix: str, n: int = N_SAMPLES, split: str = "test"):
    print(f"\nLoading local dataset from: {local_path}")
    try:
        ds_dict = load_from_disk(local_path)
        ds = ds_dict[split] if split in ds_dict else ds_dict[list(ds_dict.keys())[0]]
    except Exception as e:
        print(f"  Failed to load from disk: {e}")
        return

    saved = 0
    for i in range(min(n, len(ds))):
        example = ds[i]
        if "image" not in example:
            print(f"  Unexpected schema, no 'image' field. Keys: {list(example.keys())}")
            return
        img = example["image"].convert("RGB")
        out_path = os.path.join(OUTPUT_DIR, f"{prefix}_{saved+1}.png")
        img.save(out_path)
        print(f"  Saved: {out_path}")
        saved += 1


def save_samples(dataset_name: str, prefix: str, n: int = N_SAMPLES, split: str = "train"):
    print(f"\nLoading {dataset_name} (streaming, only pulling {n} samples)...")
    try:
        ds = load_dataset(dataset_name, split=split, streaming=True)
    except Exception as e:
        print(f"  Failed to load {dataset_name}: {e}")
        return

    saved = 0
    for i, example in enumerate(ds):
        if saved >= n:
            break
        if "image" not in example:
            print(f"  Unexpected schema, no 'image' field. Keys: {list(example.keys())}")
            return
        img = example["image"].convert("RGB")
        out_path = os.path.join(OUTPUT_DIR, f"{prefix}_{saved+1}.png")
        img.save(out_path)
        print(f"  Saved: {out_path}")
        saved += 1

    if saved == 0:
        print(f"  No samples saved from {dataset_name} -- check the dataset loaded correctly.")


if __name__ == "__main__":
    if LOCAL_SROIE_PATH:
        save_samples_from_disk(LOCAL_SROIE_PATH, "real_sroie_receipt")
    else:
        save_samples("darentang/sroie", "real_sroie_receipt")
    save_samples("naver-clova-ix/cord-v2", "real_cord_receipt")
    print("\nDone. Test any of them with:")
    print("  python test_pipeline_on_file.py ../data_set/real_sroie_receipt_1.png")
