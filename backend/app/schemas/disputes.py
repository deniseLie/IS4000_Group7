"""A confirmed dispute draft for owned posted card charges; staff decide any refund or cancellation."""
from datetime import date
from typing import Annotated,Literal,Self
from pydantic import BaseModel,ConfigDict,Field,StrictFloat,StringConstraints,model_validator

DisputeCardId=Annotated[str,StringConstraints(strict=True,pattern=r'^card_[1-9][0-9]{0,14}$')]
DisputeTransactionId=Annotated[str,StringConstraints(strict=True,pattern=r'^txn_[1-9][0-9]{0,17}$')]
Possession=Literal['yes','no','unsure']


class DisputeTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: DisputeCardId
    transaction_ids: list[DisputeTransactionId]=Field(min_length=1,max_length=10)

    @model_validator(mode='after')
    def canonical(self) -> Self:
        if self.transaction_ids!=sorted(set(self.transaction_ids),key=lambda item:int(item[4:])):
            raise ValueError('Dispute transactions must be unique and in id order')
        return self


class DisputeItem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    transaction_id: DisputeTransactionId
    date: date
    merchant: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]
    amount: Annotated[StrictFloat,Field(lt=0,allow_inf_nan=False)]
    currency: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{3}$')]


class StoredDisputeTarget(DisputeTarget):
    last4: Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9]{4}$')]
    items: list[DisputeItem]=Field(min_length=1,max_length=10)

    @model_validator(mode='after')
    def items_match(self) -> Self:
        if sorted(item.transaction_id for item in self.items)!=sorted(self.transaction_ids):
            raise ValueError('Dispute items must be exactly the disputed transactions')
        return self


class DisputeInput(BaseModel):
    """The customer's answer when confirming: the only fact the records cannot supply."""
    model_config=ConfigDict(extra='forbid')
    card_in_possession: Possession


class DisputeResult(StoredDisputeTarget):
    card_in_possession: Possession
    status: Literal['submitted_for_review']
