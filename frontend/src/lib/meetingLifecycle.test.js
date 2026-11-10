import test from 'node:test';
import assert from 'node:assert/strict';
import {friendlyMeetingError,guessRoles,meetingStep,minutesAsNotes,notesCues,sourceTitle,speakerList,stepState,transcriptSpeakers,transcriptSummary} from './meetingLifecycle.js';

test('the lifecycle stage decides the current step of the meeting page',()=>{
  assert.equal(meetingStep(null,null),0);
  assert.equal(meetingStep('brief_review',{state:'scheduled'}),0);
  assert.equal(meetingStep('brief_approved',{state:'scheduled'}),1);
  assert.equal(meetingStep('brief_review',{state:'in_progress'}),2);
  assert.equal(meetingStep('minutes_review',{state:'ended'}),2);
  assert.equal(meetingStep('following_up',{state:'ended'}),3);
  assert.equal(meetingStep('done',{state:'ended'}),4);
  assert.deepEqual([0,1,2,3].map(i=>stepState(i,2)),['done','done','current','todo']);
});

test('speaker names are read from the export and guessed against the client name',()=>{
  const vtt='WEBVTT\n\n00:00:05.000 --> 00:00:12.000\n<v Sophie Lim>Thanks for making time.\n\n00:00:13.000 --> 00:00:21.000\n<v Michelle Ng>Yes.\n\n00:00:22.000 --> 00:00:30.000\n<v Sophie Lim>Next.';
  assert.deepEqual(transcriptSpeakers(vtt),['Sophie Lim','Michelle Ng']);
  assert.deepEqual(transcriptSpeakers('Sophie: hello\nMichelle Ng: hi\nsophie: again'),['Sophie','Michelle Ng']);
  const roles=guessRoles(['Sophie Lim','Michelle Ng','Guest'],'Michelle Ng');
  assert.deepEqual(roles,{'Sophie Lim':'rm','Michelle Ng':'client','Guest':'unmapped'});
  assert.deepEqual(speakerList(roles),[{label:'Sophie Lim',role:'rm'},{label:'Michelle Ng',role:'client'}]);
});

test('brief sources and minutes are shown in the RM’s words',()=>{
  assert.equal(sourceTitle('rm_review_status:abc'),'Compliance case');
  assert.equal(sourceTitle('rm_talking_points:abc'),'Chat (topics she shared)');
  const draft={content:{summary:[{text:'Condo next year.'}],decisions:[{text:'Explore a Lombard loan.'}],actions:[{owner:'rm',text:'Send time deposit options',due_on:'2026-11-13'}]}};
  assert.equal(minutesAsNotes(draft),'Condo next year.\nExplore a Lombard loan.\nRM action: Send time deposit options (due 2026-11-13)');
  assert.equal(notesCues('rm: hi\nclient: hello')[1].speaker,'client');
  assert.deepEqual(notesCues('- Michelle: I want Tesla shares\nSophie Lim: I will send research by 2026-11-12\nSent the checklist').map(c=>c.speaker),['client','rm','rm']);
  assert.equal(notesCues('1. Client: Note: keep cash')[0].text,'Note: keep cash');
});

test('the file card summarises the chosen transcript',()=>{
  const vtt='WEBVTT\n\n00:00:05.000 --> 00:00:12.000\n<v Sophie Lim>Thanks for making time.\n\n00:39:13.000 --> 00:39:21.000\n<v Michelle Ng>Bye.';
  assert.deepEqual(transcriptSummary(vtt),{lines:2,first:'00:05',last:'39:21',preview:[{name:'Sophie Lim',text:'Thanks for making time.'},{name:'Michelle Ng',text:'Bye.'}]});
});

test('technical meeting errors read as plain next steps',()=>{
  assert.match(friendlyMeetingError('due date needs exact ISO-date evidence in the same cue'),/Draft minutes again/);
  assert.match(friendlyMeetingError('transcript position exceeds the documented occurrence duration'),/longer than the recorded call/);
  assert.match(friendlyMeetingError("{'code': 'timeout', 'message': 'Model request timed out'}"),/took too long/);
  assert.equal(friendlyMeetingError('Only the assigned RM can import this meeting transcript'),'Only the assigned RM can import this meeting transcript');
});
