"""One credential-free parser process. Reads bounded bytes from stdin, emits only the typed parser result."""
import sys
from pydantic import TypeAdapter
from app.schemas.document_uploads import DocumentMime
from app.services.document_parsers import parse


def main() -> None:
    if len(sys.argv)!=2:raise ValueError('Parser requires exactly one declared MIME type')
    mime=TypeAdapter(DocumentMime).validate_python(sys.argv[1])
    payload=sys.stdin.buffer.read(10485761)
    result=parse(payload,mime)
    sys.stdout.write(result.model_dump_json())


if __name__=='__main__':main()
