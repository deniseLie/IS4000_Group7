"""Runtime contracts for public knowledge records and immutable section citations."""
import re
from datetime import date
from typing import Annotated,Literal,Self
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StringConstraints,model_validator
from app.schemas.events import Source

ArticleId=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z0-9][a-z0-9-]{0,59}$')]
Anchor=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z0-9][a-z0-9-]{0,39}$')]
FactId=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z][a-z0-9_.]{0,79}$')]
CitationId=Annotated[str,StringConstraints(strict=True,pattern=r'^kb:[a-z0-9][a-z0-9-]{0,59}@v[1-9][0-9]*#[a-z0-9][a-z0-9-]{0,39}$')]
Scope=Literal['public','retail','fraud','compliance','document','relationship']
Text=Annotated[str,StringConstraints(strict=True,min_length=1,pattern=r'\S')]


class KnowledgeQuery(BaseModel):
    model_config=ConfigDict(extra='ignore')
    query: Annotated[str,StringConstraints(strict=True,max_length=4000)]
    scope: Scope
    k: Annotated[int,Field(strict=True,ge=1,le=20)]


class SectionKey(BaseModel):
    model_config=ConfigDict(extra='ignore')
    article_id: ArticleId
    version: Annotated[int,Field(strict=True,gt=0,le=2147483647)]
    anchor: Anchor


class KnowledgeSection(SectionKey):
    cite: CitationId
    title: Annotated[Text,Field(max_length=200)]
    heading: Annotated[Text,Field(max_length=200)]
    body: Annotated[Text,Field(max_length=16000)]
    publisher: Annotated[Text,Field(max_length=200)]
    scope: Annotated[Text,Field(max_length=1000)]
    reference_url: str | None
    checked_on: date | None
    review_due: date | None
    score: Annotated[float,Field(strict=True,ge=0,allow_inf_nan=False)]

    @model_validator(mode='after')
    def verified_citation(self) -> Self:
        if self.cite!=f'kb:{self.article_id}@v{self.version}#{self.anchor}':raise ValueError('Knowledge citation does not match the section key')
        if self.reference_url is None:
            if self.checked_on is not None or self.review_due is not None:raise ValueError('Demo guidance cannot contain external review dates')
        else:
            if not re.fullmatch(r'https://(?:www\.moneysense\.gov\.sg|www\.abs\.org\.sg)/[^\s@]*',self.reference_url):
                raise ValueError('Knowledge reference URL is outside the approved publisher domains')
            if self.checked_on is None or self.review_due is None or self.review_due<self.checked_on:
                raise ValueError('External knowledge requires ordered review dates')
        return self

    @property
    def key(self) -> SectionKey:
        return SectionKey(article_id=self.article_id,version=self.version,anchor=self.anchor)

    @property
    def current(self) -> bool:
        return self.reference_url is None or self.checked_on<=date.today()<=self.review_due

    def source(self) -> Source:
        return Source(id=self.cite,title=self.title,url=f'/knowledge/sections/{self.article_id}/{self.version}/{self.anchor}',
            version=f'v{self.version}',publisher=self.publisher,reference_url=self.reference_url,
            checked_on=self.checked_on.isoformat() if self.checked_on else None,
            review_due=self.review_due.isoformat() if self.review_due else None,current=self.current)


class StoredSection(KnowledgeSection):
    published: StrictBool

    @property
    def current(self) -> bool:
        return self.published and super().current


class KnowledgeFact(BaseModel):
    model_config=ConfigDict(extra='ignore',strict=True)
    value: Annotated[float,Field(allow_inf_nan=False)] | Text
    unit: Annotated[Text,Field(max_length=32)]
    source_id: CitationId


class KnowledgeDetails(Source):
    content: str
    notice: str
