"""Disposable local blob adapter for browser tests. Actual bytes, signed JWTs and SQL Storage RLS.

This exercises our upload/download boundary, not hosted Supabase Storage internals or Auth.
It never replaces a model, generates an AI reply or touches a hosted project.
"""
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
from uuid import UUID,uuid4
import jwt
import psycopg
from psycopg.types.json import Jsonb


@contextmanager
def local_storage(api,folder: Path):
    folder.mkdir(parents=True,exist_ok=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            body=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(body)
        def do_OPTIONS(self):
            self.send_response(204);self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Access-Control-Allow-Headers','*');self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS');self.end_headers()
        def claims(self):
            header=self.headers.get('Authorization','')
            if not header.startswith('Bearer '):raise ValueError('JWT required')
            return jwt.decode(header[7:],api.signing_key.public_key(),algorithms=['ES256'],audience='authenticated')
        def database(self,claims):
            db=psycopg.connect(api.database_uri)
            db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps(claims),));db.execute('set local role authenticated')
            return db
        def location(self):
            prefix='/storage/v1/object/demobank-compliance-private/'
            if not self.path.startswith(prefix):raise ValueError('unsupported storage route')
            path=self.path[len(prefix):]
            pieces=path.split('/')
            if len(pieces)!=3 or not pieces[0].isdigit():raise ValueError('invalid object path')
            UUID(pieces[1]);UUID(pieces[2]);return path
        def do_POST(self):
            try:
                claims=self.claims();path=self.location();size=int(self.headers['Content-Length'])
                if not 1<=size<=10485760+65536:raise ValueError('size limit')
                payload=self.rfile.read(size);mime=self.headers.get('Content-Type','')
                if mime.startswith('multipart/'):
                    message=BytesParser(policy=default).parsebytes(('Content-Type: '+mime+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+payload)
                    file=next(part for part in message.iter_parts() if part.get_filename() is not None)
                    payload=file.get_payload(decode=True);mime=file.get_content_type()
                if len(payload)>10485760 or mime not in ['application/pdf','image/png','image/jpeg','application/vnd.openxmlformats-officedocument.wordprocessingml.document']:raise ValueError('descriptor')
                object_id=uuid4()
                with self.database(claims) as db:
                    db.execute("insert into storage.objects(id,bucket_id,name,owner_id,metadata) values(%s,'demobank-compliance-private',%s,%s,%s)",
                        (object_id,path,claims['sub'],Jsonb({'size':len(payload),'mimetype':mime})))
                    (folder/object_id.hex).write_bytes(payload)
                self.reply(200,{'Id':str(object_id),'Key':'demobank-compliance-private/'+path})
            except (ValueError,KeyError,StopIteration,jwt.InvalidTokenError,psycopg.Error):self.reply(403,{'message':'Local storage denied this object','statusCode':'403'})
        def do_GET(self):
            try:
                claims=self.claims();path=self.location()
                with self.database(claims) as db:
                    row=db.execute("select id,metadata from storage.objects where bucket_id='demobank-compliance-private' and name=%s",(path,)).fetchone()
                if row is None:raise ValueError('not found')
                payload=(folder/row[0].hex).read_bytes()
                self.send_response(200);self.send_header('Content-Type',row[1]['mimetype']);self.send_header('Content-Length',str(len(payload)))
                self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(payload)
            except (ValueError,KeyError,FileNotFoundError,jwt.InvalidTokenError,psycopg.Error):self.reply(404,{'message':'Object unavailable'})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def template_pdf(lines: list[str],pages: int=1) -> bytes:
    """Actual text-layer PDF for parser/browser checks; no extracted text is fabricated."""
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
    writer=PdfWriter()
    for number in range(pages):
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        text='BT /F1 14 Tf 40 740 Td 24 TL\n'
        for line in (lines if number==0 else ['Synthetic DEMO continuation page']):
            safe=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            text+=f'({safe}) Tj T*\n'
        stream=DecodedStreamObject();stream.set_data((text+'ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);return output.getvalue()
