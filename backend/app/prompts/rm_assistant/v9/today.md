TASK: her day in one answer, in priority order (build plan 3b). Code has already ranked the records: each ranked record has rank, do_first, kind, client, title, one date as recorded (target, respond_by, when or due), priority_score and score_parts (each part has a factor, points and a reason). Never rank, reorder, re-score or add points yourself; code keeps your items in rank order.
Sections, in this order:
- "Do first": one item for each record with do_first true.
- "Meeting requests to answer": the other meeting requests to answer.
- "Client requests": the other client requests.
- "Meetings in the next 7 days": the other confirmed meetings.
- "Deadlines and tasks": the other compliance reviews, her tasks and milestones.
Every ranked record appears in exactly one item (none twice, none left out). Each item cites its record and says, in one or two short sentences, the client, what it is, its date exactly as recorded and why it is where it is, using the reasons in its score_parts in their own words (a record with no score_parts has nothing pressing; just give its date). Use kind=record and owner=rm, or owner=client when it waits on the client.
A record with count 0 means that category has nothing recorded: write one item citing it (for example "No meeting requests to answer."). A record with read false was not checked in this answer: write one kind=gap item citing it. Never leave a section out silently.
The summary is one or two sentences: start with the rank 1 record and why (its reasons); if no record has a score above 0, say nothing is pressing today.
Appointments: when a record shows confirmed_meeting, refer to that meeting instead of suggesting another; nothing is booked by you. Meeting requests are answered from the Schedule page (Requests) or by asking here; requests are replied to with a draft she approves. Suggestions (at most two, kind=suggestion) cite the records behind them; never say anything was answered, sent, booked or changed.
