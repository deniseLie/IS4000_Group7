-- Storyline scene 5 (docs/workflows/storyline-michelle.md): Michelle has drawn a Lombard fixed term advance against her
-- advised portfolio and markets fall, so the collateral no longer covers the borrowing. Sophie's Today shows "Lombard
-- collateral below its lending value" as a Do first item, and Michelle's client page shows the shortfall.
-- Run after 42 (migration 0116), only for the demo. 34_reset_meeting_demo.sql (or banking.reset_portfolio_demo()) puts the
-- portfolio back and clears the alert. Synthetic figures; owner connection (SQL Editor).
select rm.demo_lombard_scenario(720000,35);
