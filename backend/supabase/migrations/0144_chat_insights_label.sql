-- Client chat insights (8 Nov 2026). Synthetic data only.
-- The talking-points feature is called Client chat insights in words people read: "Chat insights" on her own pages,
-- "Client chat insights" on the RM side (code, the insights schema, insight.* actions and the shared TalkingPoint
-- contract keep their names). Data sharing reads its category labels from consent.categories (0109), so the chat_topics
-- row changes here. Only the label: the description is the consent notice text of ct-2026-11 and stays as she agreed it.

update consent.categories set label='Chat insights' where code='chat_topics' and label='Topics from your chats';
do $$ begin
  if (select label from consent.categories where code='chat_topics') is distinct from 'Chat insights' then
    raise exception 'consent.categories chat_topics label was not updated';
  end if;
end $$;
