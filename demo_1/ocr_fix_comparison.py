"""
ocr_fix_comparison.py


"""

import os
import random
import tempfile

import pytesseract
from PIL import Image, ImageOps
from datasets import load_from_disk

if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

LOCAL_SROIE_PATH = r"C:\Users\User\Desktop\sroie"
N_SAMPLES = 8
RANDOM_SEED = 42  


def ocr_as_is(img: Image.Image):
    """Exactly what ingestion.py's ImageIngestor currently does: no preprocessing."""
    ocr_data = pytesseract.image_to_data(img.convert("RGB"), output_type=pytesseract.Output.DICT)
    words = [w for w in ocr_data["text"] if w.strip()]
    confs = [int(c) for c, w in zip(ocr_data["conf"], ocr_data["text"]) if w.strip() and c != "-1"]
    text = " ".join(words)
    avg_conf = (sum(confs) / len(confs)) if confs else None
    return text, avg_conf


def ocr_with_fix(img: Image.Image):
    """The proposed fix: grayscale -> autocontrast -> binarize -> psm 6."""
    gray = ImageOps.grayscale(img)
    contrast = ImageOps.autocontrast(gray)
    binarized = contrast.point(lambda x: 0 if x < 150 else 255, '1')
    ocr_data = pytesseract.image_to_data(binarized, config='--psm 6', output_type=pytesseract.Output.DICT)
    words = [w for w in ocr_data["text"] if w.strip()]
    confs = [int(c) for c, w in zip(ocr_data["conf"], ocr_data["text"]) if w.strip() and c != "-1"]
    text = " ".join(words)
    avg_conf = (sum(confs) / len(confs)) if confs else None
    return text, avg_conf


def main():
    print(f"Loading local SROIE dataset from: {LOCAL_SROIE_PATH}")
    ds_dict = load_from_disk(LOCAL_SROIE_PATH)
    from datasets import concatenate_datasets
    ds = concatenate_datasets([ds_dict["train"], ds_dict["test"]])

    random.seed(RANDOM_SEED)
    indices = random.sample(range(len(ds)), min(N_SAMPLES, len(ds)))
    print(f"Testing the same {len(indices)} samples as the last accuracy_scorer.py run.\n")

    for i, idx in enumerate(indices):
        example = ds[idx]
        gt = example.get("objects", {}).get("entities", {})
        img = example["image"].convert("RGB")

        text_as_is, conf_as_is = ocr_as_is(img)
        text_fixed, conf_fixed = ocr_with_fix(img)

        print(f"=== [{i+1}/{len(indices)}] Sample #{idx} (ground truth company: '{gt.get('company')}') ===")
        print(f"AS-IS:  conf={conf_as_is}, chars={len(text_as_is)}")
        print(f"        {text_as_is[:300]}")
        print(f"FIXED:  conf={conf_fixed}, chars={len(text_fixed)}")
        print(f"        {text_fixed[:300]}")
        print()


if __name__ == "__main__":
    main()