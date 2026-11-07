"""Bounded complete-call summarisation; selected summaries never replace the original source cues."""
from pydantic import Field,model_validator
from app.schemas.meeting_lifecycle import Hash,MinutesContent,Model,Text

class ChunkSlot(Model):
    chunk_hash: Hash
    content: MinutesContent|None
    charged_tokens: int=Field(strict=True,ge=0)

class MinutesSelection(Model):
    title: Text=Field(max_length=160)
    summary_indices: tuple[int,...]=Field(min_length=1,max_length=4)
    decision_indices: tuple[int,...]=Field(max_length=10)

    @model_validator(mode='after')
    def distinct(self) -> 'MinutesSelection':
        if len(self.summary_indices)!=len(set(self.summary_indices)):raise ValueError('Select each summary once')
        if len(self.decision_indices)!=len(set(self.decision_indices)):raise ValueError('Select each decision once')
        return self
