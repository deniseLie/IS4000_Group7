-- Michelle's two sample fees (0082) were both recorded under the merchant "DemoBank fee", and the client reads show
-- only the merchant, so neither she nor the assistant could tell the annual card fee from the transfer fee (CR15).
-- Give each its own name. Synthetic data only; amounts and dates are unchanged.
update banking.transactions set merchant='Telegraphic transfer fee' where transaction_id=100239 and customer_id=1002;
update banking.transactions set merchant='Annual card fee' where transaction_id=100240 and customer_id=1002;
