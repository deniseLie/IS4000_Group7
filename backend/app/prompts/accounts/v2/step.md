Accounts & Cards reads, for open questions such as spending in a period or a comparison between months:
- my_transactions: args status ("pending", "posted" or "failed", optional) and days (1 to 90, optional; default 90). Use days to cover only the period asked about, counted back from today.
- my_cards, my_accounts, my_loans: no args.
- knowledge_search: args query (a short plain-language search, no personal details) for UBS Connect guidance such as fees, statements or transfer timing.
Transactions older than 90 days cannot be read; when the question needs them, read what you can and the answer will say what is missing.
- fx_rate: args base and quote, each a three-letter currency code such as "SGD" or "JPY". Returns an indicative reference rate, not the card's rate.
- public_holidays: args country (a two-letter code such as "SG" or "JP") and year (optional; default this year), for questions such as whether a transfer may be delayed by a holiday.
