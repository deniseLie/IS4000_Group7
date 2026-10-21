const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MARKS=['prepared','not_prepared'];
const CASE_STATES=['review_opened','documents_requested','documents_partially_received','awaiting_client_documents','under_review','information_requested','completed','closed'];
const SUBMISSION_STATES=['not_submitted','submitted_awaiting_review','accepted','rejected','unknown'];
function invalid() {return new Error('The checklist preparation receipt could not be verified. Reload recorded status.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function text(value) {if(typeof value!=='string'||!value.trim()||value.length>2000)throw invalid();return value;}
function timestamp(value) {if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();return value;}
export function parseChecklistTarget(value) {
  if(!MARKS.includes(value?.client_mark)||!MARKS.includes(value?.expected_client_mark)||value.client_mark===value.expected_client_mark||
    !CASE_STATES.includes(value.case_status)||!SUBMISSION_STATES.includes(value.submission_status))throw invalid();
  return {case_id:id(value.case_id),item_id:id(value.item_id),client_mark:value.client_mark,expected_client_mark:value.expected_client_mark,
    expected_client_marked_at:value.expected_client_marked_at===null?null:timestamp(value.expected_client_marked_at),
    case_title:text(value.case_title),item_label:text(value.item_label),case_status:value.case_status,submission_status:value.submission_status};
}
export function parseChecklistResult(value) {
  return {...parseChecklistTarget(value),client_marked_at:timestamp(value?.client_marked_at)};
}
export function checklistTargetMatches(target,result) {
  return Object.entries(target).every(([key,value])=>key==='expected_client_marked_at'&&value!==null?
    Date.parse(result[key])===Date.parse(value):result[key]===value);
}
