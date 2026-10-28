"""Records what the app's redacting LangSmith client would upload; nothing leaves the machine."""
from datetime import datetime
import langsmith
import pytest
from app.core import langsmith_client

class Recorder(langsmith.Client):
    """The app's redacting client, stopped at the point where it would send each run."""
    def _create_run(self,run,**kw):self.sink.created.append(run)
    def _update_run(self,run,**kw):self.sink.updated.append(run)

def when(value):
    return value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))

class Runs:
    def __init__(self):
        self.created,self.updated=[],[]
        self.client=Recorder(api_key='test',auto_batch_tracing=False,anonymizer=langsmith_client.scrub)
        self.client.sink=self
    def named(self,name):return [r for r in self.created if r['name']==name]
    def end(self,run):return next(u for u in self.updated if u['id']==run['id'])
    def ended(self,name):
        """Finished runs: start and end payloads merged, with the final metadata and times."""
        return [{**r,**self.end(r),'metadata':self.end(r)['extra']['metadata'],'start_time':when(r['start_time']),'end_time':when(self.end(r)['end_time'])}
                for r in self.named(name)]

@pytest.fixture
def langsmith_runs():
    runs=Runs()
    langsmith.configure(enabled=True,client=runs.client)  # global, so TestClient and worker threads trace too
    try:yield runs
    finally:langsmith.configure(enabled=None,client=None)
