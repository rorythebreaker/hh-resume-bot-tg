"""Bounded local Tesseract OCR. No remote service and no answer logging."""
import csv
import io
import os
import re
import shutil
import subprocess


def recognize(image, diagnostics=None):
    if not image or len(image)>1024*1024:
        return None
    from ocr_neural import model_path, recognize as recognize_neural
    if model_path().is_file():
        return recognize_neural(image,diagnostics=diagnostics)
    if not shutil.which('tesseract'):
        if diagnostics:diagnostics({'engine':'tesseract','reason':'engine_missing'})
        return None
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(image)) as source:
            if source.width*source.height>2_000_000 or source.width>4096 or source.height>4096:
                return None
            prepared=ImageOps.autocontrast(ImageOps.grayscale(source))
            prepared=prepared.resize((prepared.width*2,prepared.height*2))
            prepared=ImageOps.expand(prepared,border=16,fill='white')
            buffer=io.BytesIO()
            prepared.save(buffer,format='PNG')
            image=buffer.getvalue()
        result=subprocess.run(['tesseract','stdin','stdout','-l','rus+eng','--psm','7','tsv'],
            input=image,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,
            env={**os.environ,'OMP_THREAD_LIMIT':'1'})
        if result.returncode:
            return None
        words=[]
        for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8')),delimiter='\t'):
            text=(row.get('text') or '').strip()
            if text:
                confidence=float(row.get('conf','-1'))
                if confidence<65:
                    if diagnostics:diagnostics({'engine':'tesseract','reason':'low_confidence'})
                    return None
                words.append(text)
        text=' '.join(words)
        if 2<=len(text)<=40 and re.fullmatch(r'[^\W_]+(?: [^\W_]+)*',text,re.UNICODE):
            return text
    except Exception as exc:
        if diagnostics:diagnostics({'engine':'tesseract','reason':'engine_error','error':type(exc).__name__})
        # OCR is optional: malformed images, missing models, process failures
        # and resource guards must all fall back to owner input.
        pass
    return None
