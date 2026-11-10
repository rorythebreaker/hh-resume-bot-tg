"""Private DOCX benchmark. Reports contain no CAPTCHA images or answer text."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image


def examples(path):
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        relationships={row.attrib['Id']:row.attrib['Target'] for row in ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
        for paragraph in ET.fromstring(archive.read('word/document.xml')).findall('.//w:body//w:p',ns):
            images=paragraph.findall('.//a:blip',ns)
            if not images:continue
            answer=''.join(node.text or '' for node in paragraph.findall('.//w:t',ns)).strip()
            if not answer:raise ValueError('Image without an answer')
            target=relationships[images[0].attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed']]
            yield archive.read('word/'+target),answer


def remove_document_frame(data):
    with Image.open(io.BytesIO(data)) as source:
        gray=source.convert('L');width,height=gray.size;pixels=gray.load()
        rows=[y for y in range(height) if sum(pixels[x,y]>65 for x in range(width))>width*.6]
        if rows and rows[-1]-rows[0]>height*.5:
            source=source.crop((0,rows[0],width,rows[-1]+1))
        buffer=io.BytesIO();source.save(buffer,format='PNG');return buffer.getvalue()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docx',type=Path)
    parser.add_argument('--engine',choices=('neural','tesseract'),default='neural')
    parser.add_argument('--model',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.engine=='tesseract':
        if not shutil.which('tesseract'):raise SystemExit('Tesseract CLI is not installed; refusing an invalid baseline comparison')
        os.environ['HH_OCR_MODEL']=str(Path(__file__).parent/'absent-model.onnx')
    elif args.model:os.environ['HH_OCR_MODEL']=str(args.model.resolve())
    from captcha_ocr import recognize
    from ocr_neural import model_path
    if args.engine=='neural' and not model_path().is_file():raise SystemExit('Neural model is not installed')
    results=[]
    for index,(image,answer) in enumerate(examples(args.docx),1):
        started=time.monotonic();diagnostics=[]
        text=recognize(remove_document_frame(image),diagnostics=diagnostics.append)
        exact=text is not None and ' '.join(text.casefold().split())==' '.join(answer.casefold().split())
        results.append({'sample':index,'attempted':text is not None,'exact':exact,
                        'milliseconds':round((time.monotonic()-started)*1000),'diagnostics':diagnostics})
        print(f'{index}: attempted={text is not None}, exact={exact}',flush=True)
    report={'engine':args.engine,'total':len(results),'correct':sum(row['exact'] for row in results),
            'wrong':sum(row['attempted'] and not row['exact'] for row in results),
            'manual':sum(not row['attempted'] for row in results),'samples':results}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print({key:value for key,value in report.items() if key!='samples'})

if __name__=='__main__':main()
