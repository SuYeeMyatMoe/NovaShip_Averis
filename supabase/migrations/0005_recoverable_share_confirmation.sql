-- Keep confirmation recoverable: SENT is now the final state after all
-- case and audit side effects complete successfully.
alter table shares
  add column if not exists confirmation_started_at timestamptz;

create or replace function complete_share_confirmation(
  p_share_id text,
  p_tenant_id text,
  p_actor_id text
)
returns setof shares
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_share shares%rowtype;
  v_case cases%rowtype;
  v_target_status text;
  v_recipient_id text;
  v_shared_with jsonb;
  v_payload jsonb;
  v_now timestamptz := now();
begin
  select *
    into v_share
    from shares
   where id = p_share_id
     and tenant_id = p_tenant_id
   for update;

  if not found then
    return;
  end if;
  if v_share.status = 'SENT' then
    return next v_share;
    return;
  end if;
  if v_share.status <> 'CONFIRMING' then
    return;
  end if;

  select *
    into v_case
    from cases
   where id = v_share.case_id
     and tenant_id = p_tenant_id
   for update;

  if not found then
    return;
  end if;

  v_target_status := case
    when v_share.is_external then 'AWAITING_RESPONSE'
    else 'ASSIGNED'
  end;
  v_recipient_id := coalesce(
    v_share.recipient_user_id,
    v_share.recipient_party_id
  );
  v_shared_with := coalesce(v_case.shared_with, '[]'::jsonb);
  if v_recipient_id is not null
     and not (v_shared_with @> jsonb_build_array(v_recipient_id)) then
    v_shared_with := v_shared_with || jsonb_build_array(v_recipient_id);
  end if;

  insert into audit_events (
    event_id,
    tenant_id,
    case_id,
    timestamp,
    actor_type,
    actor_id,
    action,
    after,
    policy_version
  ) values (
    'evt_' || v_share.id || '_sent',
    p_tenant_id,
    v_case.id,
    v_now,
    'USER',
    p_actor_id,
    case when v_share.is_external then 'NOTIFY_PARTY_SENT' else 'SHARE_SENT' end,
    jsonb_build_object(
      'share_id', v_share.id,
      'recipient_label', v_share.recipient_label,
      'external', v_share.is_external,
      'fields', jsonb_path_query_array(
        v_share.payload_preview,
        '$.fields[*].field'
      ),
      'due_date', v_share.due_date
    ),
    'v1'
  ) on conflict (event_id) do nothing;

  if v_case.status is distinct from v_target_status then
    insert into audit_events (
      event_id,
      tenant_id,
      case_id,
      timestamp,
      actor_type,
      actor_id,
      action,
      before,
      after,
      policy_version
    ) values (
      'evt_' || v_share.id || '_status',
      p_tenant_id,
      v_case.id,
      v_now,
      'USER',
      p_actor_id,
      'STATUS_CHANGED',
      jsonb_build_object('status', v_case.status),
      jsonb_build_object('status', v_target_status),
      'v1'
    ) on conflict (event_id) do nothing;
  end if;

  v_payload := jsonb_set(v_case.payload, '{shared_with}', v_shared_with, true);
  v_payload := jsonb_set(
    v_payload,
    '{status}',
    to_jsonb(v_target_status),
    true
  );
  v_payload := jsonb_set(
    v_payload,
    '{updated_at}',
    to_jsonb(v_now),
    true
  );

  update cases
     set shared_with = v_shared_with,
         status = v_target_status,
         payload = v_payload,
         updated_at = v_now
   where id = v_case.id
     and tenant_id = p_tenant_id;

  update shares
     set status = 'SENT',
         sent_at = v_now
   where id = v_share.id
     and tenant_id = p_tenant_id
     and status = 'CONFIRMING'
  returning * into v_share;

  if not found then
    raise exception 'share confirmation state changed unexpectedly';
  end if;

  return next v_share;
end;
$$;

revoke execute on function complete_share_confirmation(text, text, text)
  from public, anon, authenticated;
grant execute on function complete_share_confirmation(text, text, text)
  to service_role;
