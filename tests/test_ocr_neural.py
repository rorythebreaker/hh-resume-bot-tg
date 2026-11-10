from pathlib import Path
import pytest
import ocr_neural
from ocr_preprocess import variants
from PIL import Image
import io


def test_neural_selection_requires_agreement_and_high_confidence():
    assert ocr_neural.choose_candidate([('first word',.99,'one')]) is None
    assert ocr_neural.choose_candidate([('first word',.92,'one'),('first word',.92,'two')]) is None
    assert ocr_neural.choose_candidate([('first word',.97,'one'),('first word',.87,'two')])=='first word'
    assert ocr_neural.choose_candidate([('first word',.99,'same'),('first word',.99,'same')]) is None
    assert ocr_neural.choose_candidate([('invalid|answer',.99,'one'),('invalid|answer',.99,'two')]) is None


def test_incorrect_model_falls_back_without_exposing_input(tmp_path,monkeypatch):
    path=tmp_path/'model.onnx';path.write_bytes(b'bad weights')
    monkeypatch.setenv('HH_OCR_MODEL',str(path));monkeypatch.setattr(ocr_neural,'_MODEL',None)
    diagnostics=[]
    assert ocr_neural.recognize(b'private image',diagnostics=diagnostics.append) is None
    assert diagnostics[-1]['reason']=='engine_error'
    assert 'private' not in str(diagnostics)


def test_preprocessing_limits_and_transparent_images():
    image=Image.new('RGBA',(160,60),'white');buffer=io.BytesIO();image.save(buffer,format='PNG')
    result=variants(buffer.getvalue())
    assert result and all(prepared.mode=='L' for family,prepared in result)
    assert len(result)<=40
