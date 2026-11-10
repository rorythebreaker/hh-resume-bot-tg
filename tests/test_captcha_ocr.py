import io
import subprocess
from unittest.mock import Mock
import pytest
from PIL import Image
import captcha_ocr


def image_bytes():
    buffer=io.BytesIO();Image.new('RGB',(100,40),'white').save(buffer,format='PNG')
    return buffer.getvalue()


@pytest.mark.parametrize('confidence,expected',[(92,'hello 123'),(30,None)])
def test_ocr_confidence_and_timeout_bound(monkeypatch,confidence,expected,capsys):
    monkeypatch.setattr(captcha_ocr.shutil,'which',lambda _: '/usr/bin/tesseract')
    rows=('conf\ttext\n'+str(confidence)+'\thello\n95\t123\n').encode()
    run=Mock(return_value=Mock(returncode=0,stdout=rows))
    monkeypatch.setattr(captcha_ocr.subprocess,'run',run)
    assert captcha_ocr.recognize(image_bytes())==expected
    assert run.call_args.kwargs['timeout']==10
    assert run.call_args.kwargs['env']['OMP_THREAD_LIMIT']=='1'
    assert capsys.readouterr().out==''


def test_ocr_missing_engine_and_invalid_image_fall_back(monkeypatch):
    monkeypatch.setattr(captcha_ocr.shutil,'which',lambda _:None)
    assert captcha_ocr.recognize(image_bytes()) is None
    monkeypatch.setattr(captcha_ocr.shutil,'which',lambda _: '/usr/bin/tesseract')
    assert captcha_ocr.recognize(b'not an image') is None
    monkeypatch.setattr(captcha_ocr.subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('tesseract',10)))
    assert captcha_ocr.recognize(image_bytes()) is None
