"""Domain registry and the tool boundaries to enforce as each shared service is integrated."""
from app.schemas.contracts import AgentName, Domain

AGENT_FOR: dict[Domain, AgentName] = {'retail':'retail','smalltalk':'retail','fraud':'fraud',
    'compliance':'compliance','document':'document','relationship':'relationship'}
TOOLS: dict[AgentName, tuple[str, ...]] = {
    'retail': ('kb.search','kb.get_fact','customer.get_profile','customer.get_accounts',
        'customer.get_transactions','customer.get_credit_cards','customer.get_loans','handoff.create'),
    'fraud': ('customer.get_transactions','customer.get_credit_cards','actions.propose','handoff.create'),
    'compliance': ('compliance.get_case','compliance.workspace','compliance.update_checklist_item','kb.search','handoff.create'),
    'document': ('docs.precheck','compliance.workspace'),
    'relationship': ('kb.search','customer.get_profile','rm.get_assignment','requests.list',
        'appointments.find_slots','appointments.list','consent.check','consent.records','actions.propose','agenda.save','consent.request','handoff.create'),
}
