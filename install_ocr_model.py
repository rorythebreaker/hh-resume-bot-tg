"""Download the pinned public OCR weights during Docker build, never at runtime."""
import hashlib
import urllib.request
from pathlib import Path

URL='https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/cyrillic_PP-OCRv5_rec_mobile.onnx'
EXPECTED='90f761b4bfcce0c8c561c0cb5c887b0971d3ec01c32164bdf7374a35b0982711'
path=Path('/opt/hh-ocr/cyrillic.onnx')
path.parent.mkdir(parents=True,exist_ok=True)
with urllib.request.urlopen(URL,timeout=60) as response:
    data=response.read()
if hashlib.sha256(data).hexdigest()!=EXPECTED:
    raise SystemExit('OCR model checksum mismatch')
path.write_bytes(data)
path.chmod(0o644)
print('Pinned Cyrillic OCR model installed')
