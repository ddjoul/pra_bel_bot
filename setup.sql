-- Run in Supabase SQL Editor. Review and back up before applying.
-- Existing posts, authors and their policies are not deleted.
begin;
alter table public.posts enable row level security;
create table public.telegram_users (
 telegram_id bigint primary key,
 username text,
 author_id uuid references public.authors(id),
 display_name text,
 is_approved boolean not null default false,
 created_at timestamptz not null default now()
);
create table public.telegram_drafts (
 telegram_id bigint primary key references public.telegram_users(telegram_id) on delete cascade,
 id uuid not null unique default gen_random_uuid(),
 phase text not null default 'kind',
 payload jsonb not null default '{}'::jsonb,
 result jsonb,
 updated_at timestamptz not null default now()
);
alter table public.telegram_users enable row level security;
alter table public.telegram_drafts enable row level security;
revoke all on public.telegram_users, public.telegram_drafts from anon, authenticated;
grant select, insert, update, delete on public.telegram_users, public.telegram_drafts to service_role;

create function public.telegram_submit(p_telegram_id bigint, p_draft_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
 u public.telegram_users%rowtype;
 d public.telegram_drafts%rowtype;
 a uuid;
 cat public.categories%rowtype;
 lang public.languages%rowtype;
 orig public.languages%rowtype;
 st public.post_statuses%rowtype;
 v_body text;
 v_title text;
 v_original_author text;
 r jsonb;
begin
 select * into u from public.telegram_users where telegram_id=p_telegram_id for update;
 if not found then raise exception 'User not registered'; end if;
 select * into d from public.telegram_drafts where telegram_id=p_telegram_id and id=p_draft_id for update;
 if not found then raise exception 'Draft not found'; end if;
 if d.phase='sent' then return d.result; end if;
 if d.phase<>'review' then raise exception 'Draft not ready'; end if;
 select * into cat from public.categories where code=d.payload->>'category';
 if not found or cat.code not in ('poem','translation') then raise exception 'Invalid category'; end if;
 select * into lang from public.languages where code=d.payload->>'language_code';
 if not found then raise exception 'Invalid language'; end if;
 v_body=d.payload->>'body'; v_title=d.payload->>'title';
 if v_body is null or length(btrim(v_body))=0 or length(v_body)>60000 then raise exception 'Invalid body'; end if;
 if v_title is null or length(btrim(v_title))=0 or length(v_title)>200 then raise exception 'Invalid title'; end if;
 if cat.code='translation' then
  select * into orig from public.languages where code=d.payload->>'original_language';
  if not found then raise exception 'Invalid original language'; end if;
  v_original_author=d.payload->>'original_author';
  if v_original_author is null or length(btrim(v_original_author))=0 or length(v_original_author)>200 then raise exception 'Invalid original author'; end if;
 end if;
 a=u.author_id;
 if a is null then
  if u.display_name is null or length(btrim(u.display_name))=0 or length(u.display_name)>100 then raise exception 'Author name required'; end if;
  insert into public.authors(name) values(u.display_name) returning id into a;
  update public.telegram_users set author_id=a where telegram_id=p_telegram_id;
 end if;
 select * into st from public.post_statuses where code=case when u.is_approved then 'published' else 'draft' end;
 if not found then raise exception 'Status not configured'; end if;
 insert into public.posts(id,author_id,title,body,category,category_id,status,status_id,
 language_code,language_id,original_language,original_author,published_at)
 values(d.id,a,v_title,v_body,cat.code,cat.id,st.code,st.id,lang.code,lang.id,
 case when cat.code='translation' then orig.code else null end,
 case when cat.code='translation' then v_original_author else null end,
 case when st.code='published' then now() else null end);
 r=jsonb_build_object('post_id',d.id,'status',st.code,'author_id',a);
 update public.telegram_drafts set phase='sent',result=r,updated_at=now() where telegram_id=p_telegram_id;
 return r;
end;
$$;
revoke all on function public.telegram_submit(bigint,uuid) from public, anon, authenticated;
grant execute on function public.telegram_submit(bigint,uuid) to service_role;
commit;
