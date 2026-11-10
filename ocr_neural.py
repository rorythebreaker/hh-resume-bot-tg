"""Offline Cyrillic recognition; never use fixture labels in inference."""
import hashlib
import io
import math
import os
import re
import time
from pathlib import Path

MODEL_SHA256='90f761b4bfcce0c8c561c0cb5c887b0971d3ec01c32164bdf7374a35b0982711'
_MODEL=None


def model_path():
    return Path(os.environ.get('HH_OCR_MODEL','/opt/hh-ocr/cyrillic.onnx'))


def choose_candidate(entries):
    groups={}
    for text,score,family in entries:
        normalized=' '.join(text.casefold().split())
        if len(normalized.split())!=2 and not normalized.isdigit():
            continue
        if not 2<=len(normalized)<=60 or not re.fullmatch(r'[^\W_]+(?: [^\W_]+)*',normalized):
            continue
        if score<.85:
            continue
        groups.setdefault(normalized,[]).append((score,family))
    eligible=[]
    for text,values in groups.items():
        views=len({family for score,family in values})
        peak=max(score for score,family in values)
        if views>=2 and peak>=.93:
            eligible.append((views,peak,text))
    return max(eligible)[2] if eligible else None


def _session():
    global _MODEL
    path=model_path()
    if _MODEL is not None and _MODEL[0]==str(path):return _MODEL[1:]
    if not path.is_file():return None
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_SHA256:
        raise ValueError('OCR model checksum mismatch')
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
    chars=['']+session.get_modelmeta().custom_metadata_map['character'].splitlines()+[' ']
    _MODEL=(str(path),session,chars)
    return session,chars


def recognize(image, diagnostics=None):
    started=time.monotonic()
    def report(reason,**fields):
        if diagnostics:diagnostics({'engine':'cyrillic-onnx','reason':reason,'elapsed_ms':round((time.monotonic()-started)*1000),**fields})
    try:
        loaded=_session()
        if not loaded:report('model_missing');return None
        session,chars=loaded
        import numpy as np
        from PIL import Image
        from ocr_preprocess import variants
        source=Image.open(io.BytesIO(image))
        if source.width*source.height>2_000_000 or max(source.size)>4096:
            report('image_too_large');return None
        plans=[('original',source.copy())]+variants(image)
        entries=[]
        for family,prepared in plans:
            if time.monotonic()-started>10:
                break
            prepared=prepared.convert('RGB');height=48
            width=max(32,min(1024,math.ceil(height*prepared.width/prepared.height)))
            array=np.asarray(prepared.resize((width,height),Image.Resampling.BICUBIC))[:,:,::-1].astype(np.float32).transpose(2,0,1)/127.5-1
            output=session.run(None,{'x':array[None]})[0][0]
            indices=output.argmax(axis=1);scores=output.max(axis=1)
            text=[];confidence=[];previous=-1
            for index,score in zip(indices,scores):
                if index and index!=previous:
                    if index>=len(chars):raise ValueError('Unknown OCR character')
                    text.append(chars[index]);confidence.append(float(score))
                previous=index
            entries.append((''.join(text),sum(confidence)/len(confidence) if confidence else 0,family))
        selected=choose_candidate(entries)
        report('consensus' if selected else 'uncertain',variants=len(entries),peak=round(max((score for text,score,family in entries),default=0),3))
        return selected
    except Exception as exc:
        report('engine_error',error=type(exc).__name__)
        return None
