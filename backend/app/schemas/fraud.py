"""Bounded, masked customer evidence and validated fraud proposals. Credentials remain runtime-only."""
from datetime import date
from typing import Annotated,Self
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictFloat,StringConstraints,model_validator
from app.schemas.actions import CardId
from app.schemas.agents import AgentDraft
from app.schemas.contracts import AgentResult

Last4=Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9]{4}$')]
TransactionId=Annotated[str,StringConstraints(strict=True,pattern=r'^txn_[1-9][0-9]{0,17}$')]
Text=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=300,pattern=r'\S')]


class SecurityCard(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId
    last4: Last4
    status: Text


class SecurityTransaction(BaseModel):
    model_config=ConfigDict(extra='ignore')
    txn_id: TransactionId
    date: date
    description: Text
    amount: Annotated[StrictFloat,Field(allow_inf_nan=False)]
    currency: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{3}$')]
    status: Text
    card_id: CardId | None
    card_last4: Last4 | None


class SecurityRecords(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cards: list[SecurityCard]=Field(max_length=200)
    transactions: list[SecurityTransaction]=Field(max_length=200)
    captured_at: AwareDatetime

    @model_validator(mode='after')
    def consistent_cards(self) -> Self:
        cards={item.card_id:item for item in self.cards}
        if len(cards)!=len(self.cards) or len({item.txn_id for item in self.transactions})!=len(self.transactions):
            raise ValueError('Security evidence contains duplicate record identifiers')
        for item in self.transactions:
            if item.card_id is not None and (item.card_id not in cards or cards[item.card_id].last4!=item.card_last4):
                raise ValueError('Transaction card identity does not match the scoped card records')
            if (item.card_id is None)!=(item.card_last4 is None):raise ValueError('Card identity and masked suffix must appear together')
        return self


class FraudProposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    card_id: CardId
    transaction_id: TransactionId


class FraudDraft(AgentDraft):
    answer: str=Field(min_length=1,max_length=3000)
    proposals: list[FraudProposal]=Field(max_length=1)

    @model_validator(mode='after')
    def proposal_requires_answer(self) -> Self:
        if self.proposals and (self.status=='needs_clarification' or self.answer_kind!='banking'):
            raise ValueError('A proposal requires grounded banking guidance, not a clarification')
        return self


class DisputeProposal(BaseModel):
    """A records-derived dispute draft: the reported charge plus rule-related charges on the same card, never model output."""
    model_config=ConfigDict(extra='forbid')
    card_id: CardId
    transaction_ids: list[TransactionId]=Field(min_length=1,max_length=10)

    @model_validator(mode='after')
    def canonical(self) -> Self:
        if self.transaction_ids!=sorted(set(self.transaction_ids),key=lambda item:int(item[4:])):
            raise ValueError('Dispute transactions must be unique and in id order')
        return self


class FraudDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    result: AgentResult
    proposal: FraudProposal | None
    prompt_id: str
    dispute: DisputeProposal | None=None
