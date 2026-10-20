"""Bounded authenticated retail evidence; no product catalogue or inferred currencies."""
from typing import Self
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,model_validator
from app.schemas.customer_data import BankAccount,CreditCard,CustomerLoan,CustomerProfile,CustomerTransaction


class RetailRecords(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile: CustomerProfile
    accounts: list[BankAccount]=Field(max_length=200)
    cards: list[CreditCard]=Field(max_length=200)
    loans: list[CustomerLoan]=Field(max_length=200)
    transactions: list[CustomerTransaction]=Field(max_length=200)
    captured_at: AwareDatetime

    @model_validator(mode='after')
    def distinct_records(self) -> Self:
        keys=[[row.account_id for row in self.accounts],[row.card_id for row in self.cards],
            [row.loan_id for row in self.loans],[row.txn_id for row in self.transactions]]
        if any(len(values)!=len(set(values)) for values in keys):raise ValueError('Retail evidence contains duplicate record identifiers')
        cards={row.card_id:row.last4 for row in self.cards}
        if any(row.card_id is not None and cards.get(row.card_id)!=row.card_last4 for row in self.transactions):
            raise ValueError('Retail transactions reference a card outside the scoped card snapshots')
        return self
