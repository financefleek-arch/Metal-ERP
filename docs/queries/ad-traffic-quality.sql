-- Google Ads traffic quality for metal.fleekfinance.in, from Umami. Read-only. Change the interval to look at another day.
-- Run on the VPS (Umami keeps its own database on the shared Postgres):
--   ssh fleek-vps 'docker exec -i postgres sh -c "psql -U \$POSTGRES_USER -d umami -X"' < docs/queries/ad-traffic-quality.sql
-- Reading it: "Left in <3s" high + few taps = accidental taps (placement problem, not bots). Compare "Sessions" with the clicks Google
-- reports: the gap is clicks that never loaded the page. Use the placement table to build the Google Ads exclusion list.
\pset pager off
create temp view m as
  select e.*, s.browser from website_event e join session s using (session_id)
  where e.website_id = '37810516-965e-4d75-ac59-a4d68093a551' and e.created_at > now() - interval '24 hours';
create temp view ss as
  select session_id, min(browser) browser, min(created_at) t0, max(created_at) t1,
         count(*) filter (where event_name in ('landing-create-account', 'landing-signin', 'landing-google-play')) taps,
         count(*) filter (where event_name = 'signup-codes-sent') codes,
         count(*) filter (where event_name = 'signup-complete') done,
         coalesce(min(referrer_domain), '(none)') ref, bool_or(gclid is not null) has_gclid
  from m group by 1;

\echo == 1. Whole day
select count(*) sessions, count(*) filter (where has_gclid) with_gclid,
       round(100.0 * count(*) filter (where t1 - t0 < interval '3 seconds') / count(*)) pct_left_under_3s,
       count(*) filter (where taps > 0) tapped_a_button, count(*) filter (where codes > 0) codes_sent, count(*) filter (where done > 0) accounts
from ss;

\echo == 2. By where the click came from
select case when ref ~ 'youtube' then 'youtube' when ref ~ 'doubleclick|googlesyndication|googleads' then 'google display'
            when ref ~ 'glance' then 'glance lockscreen' when ref ~ 'googlequicksearchbox' then 'google app'
            when ref = '(none)' then '(none)' else 'other sites/apps' end source,
       count(*) sessions, round(100.0 * count(*) filter (where t1 - t0 < interval '3 seconds') / count(*)) pct_under_3s,
       count(*) filter (where taps > 0) taps, count(*) filter (where codes > 0) codes, count(*) filter (where done > 0) accounts
from ss group by 1 order by 2 desc;

\echo == 3. Placements to consider excluding (3+ sessions, nobody tapped anything)
select ref placement, count(*) sessions, count(*) filter (where taps > 0) taps, count(*) filter (where done > 0) accounts
from ss group by 1 having count(*) >= 3 and count(*) filter (where taps > 0) = 0 order by 2 desc limit 30;

\echo == 4. In-app browser vs normal browser
select browser, count(*) sessions, count(*) filter (where taps > 0) taps, count(*) filter (where done > 0) accounts from ss group by 1 order by 2 desc;

\echo == 5. Same click id used by more than one session
select gclid, count(distinct session_id) sessions from m where gclid is not null group by 1 having count(distinct session_id) > 1 order by 2 desc limit 10;

\echo == 6. Sessions by hour (IST)
select to_char(date_trunc('hour', t0 at time zone 'Asia/Kolkata'), 'DD Mon HH24:00') hr, count(*) from ss group by 1 order by min(t0);
