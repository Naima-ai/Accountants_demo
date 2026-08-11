#OCR and real receipts
from datasets import load_dataset
ds = load_dataset("darentang/sroie")
#cord Consolidated Receipt Dataset 
from datasets import load_dataset

ds = load_dataset("naver-clova-ix/cord-v2")
#includes annotated data from Amazon receipts and invoices structured into JSON format 
from datasets import load_dataset

ds = load_dataset("manuelaschrittwieser/invoice-extraction-dataset-v2")
#FUNSD 
from datasets import load_dataset

# Login using e.g. `huggingface-cli login` to access this dataset
ds = load_dataset("nielsr/funsd")
