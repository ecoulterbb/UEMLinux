-- ============================================================
-- Fix 1: getDueScheduledEntry_68_1
-- ============================================================
ALTER FUNCTION uem.getduescheduledentry_68_1(integer, integer)
  RENAME TO getduescheduledentry_68_1_fn;

CREATE OR REPLACE PROCEDURE uem.getDueScheduledEntry_68_1(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_min_version integer DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState    TEXT;
  v_SQLErrorMsg      TEXT;
  v_SQLErrorDetail   TEXT;
  v_SQLDefErrorHint  TEXT;
  v_UserDefError     VARCHAR(10);
  v_UserDefErrorMsg  TEXT;
  v_numRowsAffected  INTEGER;
  c_indexQueueName                INTEGER := f_getLockHandle('SchdlrQ');
  c_proc_name                     VARCHAR(30) := 'getDueScheduledEntry_68_1';
  c_cfg_stng_num_scdlr_to_return  VARCHAR(30) := 'max.scheduled.entries.returned';
  c_dflt_upToNumEntries           INTEGER := 10;
  v_cfg_setting_value             obj_global_cfg_setting.value%TYPE;
  v_rowsToReturn                  INTEGER;
  v_tab_schdlrIdList              INTEGER[];
BEGIN
    v_cfg_setting_value := getglobalcfgsettingvalue(1, c_cfg_stng_num_scdlr_to_return, NULL, 0, v_cfg_setting_value);
    v_rowsToReturn := COALESCE(CAST(v_cfg_setting_value AS integer), c_dflt_upToNumEntries);
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg := ' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                         ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                         'when retrieving pg_advisory_xact_lock for queue '||c_indexQueueName||'.';
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      DELETE FROM obj_scheduler
        WHERE is_disabled_upon_expiry=false AND is_disabled=false
          AND (iterations=0 OR (final_callback<(SELECT now() at time zone 'utc')));
      UPDATE obj_scheduler SET is_disabled=true
        WHERE is_disabled_upon_expiry=true
          AND (iterations=0 OR (final_callback<(SELECT now() at time zone 'utc')));
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
    END;
    BEGIN
      WITH updated AS(
         UPDATE obj_scheduler
         SET next_callback=(SELECT now() at time zone 'utc')+callback_freq*INTERVAL '1 second'
            ,iterations=(case true when is_disabled=true then iterations
                                   when iterations=-1 then -1
                                   when iterations=0 then 0
                                   else (iterations-1) end)
         WHERE id_scheduler IN (
               SELECT os2.id_scheduler
                 FROM (SELECT os.id_scheduler FROM obj_scheduler os
                        WHERE (os.next_callback IS NULL OR os.next_callback<(SELECT now() at time zone 'utc'))
                          AND (min_version IS NULL OR min_version<=p_min_version)
                          AND iterations!=0 AND is_disabled=false
                        ORDER BY os.next_callback) os2
                 LIMIT v_rowsToReturn)
         RETURNING id_scheduler)
      SELECT array(SELECT id_scheduler FROM updated) INTO v_tab_schdlrIdList;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT os.id_scheduler,os.iterations,os.callback_freq,os.next_callback,os.description,
              os.handler,os.created,os.modified,os.run_on_monday,os.run_on_tuesday,
              os.run_on_wednesday,os.run_on_thursday,os.run_on_friday,os.run_on_saturday,
              os.run_on_sunday,os.start_time_of_day,os.end_time_of_day,os.final_callback,
              os.is_disabled_upon_expiry,os.is_disabled,os.planned_callback,os.schedule_type,
              os.is_user_event,os.id_snapin,os.external_tenant_id,os.id_tenant,os.task_name,
              os.min_version,os.is_handler_unique
         FROM obj_scheduler os WHERE os.id_scheduler=ANY(CAST($1 AS integer[]))'
      USING v_tab_schdlrIdList;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 2: getDueNotificationBatch_056_13
-- ============================================================
ALTER FUNCTION uem.getduenotificationbatch_056_13(integer, integer)
  RENAME TO getduenotificationbatch_056_13_fn;

CREATE OR REPLACE PROCEDURE uem.getDueNotificationBatch_056_13(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_upToNumUserDevices integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState text; v_SQLErrorMsg text; v_SQLErrorDetail text; v_SQLDefErrorHint text;
  c_indexQueueName integer := f_getLockHandle('NotifQ');
  c_proc_name varchar(30) := 'getDueNotificationBatch_056_13';
  v_int_array bigint[];
BEGIN
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS(
        WITH T AS (SELECT id_device_notification, id_user_device
                     FROM obj_device_notification odn
                    WHERE next_notification<=(SELECT now() at time zone 'utc')
                      AND EXISTS(SELECT a.id_device_action FROM obj_device_action a
                                  WHERE odn.id_device_notification=a.id_device_notification)
                    ORDER BY next_notification LIMIT p_upToNumUserDevices)
        UPDATE obj_device_notification
           SET next_notification=(SELECT now() at time zone 'utc')+(notification_ttl)*INTERVAL'1 second'
          FROM T WHERE obj_device_notification.id_device_notification=T.id_device_notification
        RETURNING obj_device_notification.id_device_notification)
      SELECT array(SELECT id_device_notification FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT dn.id_device_notification,dn.id_user_device,dn.uos,dn.created,dn.modified,
              dn.next_notification,ud.enrollment_token,ud.enrollment_secret,ud.perimeter_uuid,
              dof.name AS device_os_family_name,os.name AS device_os_name,dn.notification_ttl,
              dn.notification_channel,tnt.external_tenant_id,tnt.id_tenant,
              (SELECT COALESCE(CAST(value AS TEXT),large_value)
                 FROM obj_user_device_setting uds
                 JOIN def_user_device_setting_dfn dsd ON uds.id_user_device_setting_dfn=dsd.id_user_device_setting_dfn
                WHERE uds.id_user_device=ud.id_user_device AND dsd.name=''device.notification.token'') AS dvc_notification_token,
              ud.enrollment_type,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.cap.notification.apns'') AS dvc_cap_notify_client,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.cap.notification.apns.mdm'') AS dvc_cap_notify_mdm,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=N''device.enrollment.token'') AS dvc_enrollment_token,
              (SELECT value FROM obj_device_setting ds JOIN def_device_setting_definition dsd
               ON ds.id_device_setting_definition=dsd.id_device_setting_definition
               WHERE ds.id_device=ud.id_device AND dsd.name=''device.cap.gdclient'') AS dvc_cap_good_dynamics
         FROM obj_device_notification dn
          JOIN obj_user_device ud ON dn.id_user_device=ud.id_user_device
          JOIN obj_device d ON ud.id_device=d.id_device
          JOIN def_device_os os ON d.id_device_os=os.id_device_os
          JOIN def_device_os_family dof ON os.id_device_os_family=dof.id_device_os_family
          JOIN obj_tenant tnt ON d.id_tenant=tnt.id_tenant
        WHERE dn.id_device_notification=ANY(CAST($1 AS bigint[]))'
      USING v_int_array;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 3: getAttestationUserDevice_68_15
-- ============================================================
ALTER FUNCTION uem.getattestationuserdevice_68_15(integer, character varying, character varying)
  RENAME TO getattestationuserdevice_68_15_fn;

CREATE OR REPLACE PROCEDURE uem.getAttestationUserDevice_68_15(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_attestation_type character varying,
  p_timeSliceCfgSetting character varying
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT; v_numRowsAffected INTEGER;
  c_proc_name CONSTANT VARCHAR(64):='getAttestationUserDevice_68_15';
  c_indexQueueName CONSTANT VARCHAR(64):='AttestationQ-'||p_attestation_type;
  c_dflt_attestation_timeSlice_sec CONSTANT INTEGER:=60;
  c_type_safetynet CONSTANT VARCHAR(32):='SAFETYNET';
  v_attestation_timeslice INTEGER; v_cfg_setting_value TEXT;
  v_indexQueueId INTEGER; v_num_attestation_dvcs INTEGER;
  v_min_attestation_freq INTEGER; v_rows_to_return INTEGER;
  v_int_array INTEGER[]; v_iter INTEGER;
BEGIN
    v_indexQueueId:=f_getLockHandle(c_indexQueueName);
    v_cfg_setting_value:=getglobalcfgsettingvalue(1,p_timeSliceCfgSetting,NULL,0,v_cfg_setting_value);
    v_attestation_timeslice=COALESCE(CAST(v_cfg_setting_value AS INTEGER),c_dflt_attestation_timeSlice_sec);
    BEGIN
      PERFORM pg_advisory_xact_lock(v_indexQueueId);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                       ' When acquiring pg_advisory_xact_lock for queue ['||c_indexQueueName||'].';
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      SELECT coalesce(COUNT(*),0),MIN(frequency)
        INTO v_num_attestation_dvcs,v_min_attestation_freq
        FROM obj_user_device_attestation WHERE type=p_attestation_type;
      v_rows_to_return:=coalesce(CEIL((CAST(v_num_attestation_dvcs AS float)/v_min_attestation_freq)*v_attestation_timeslice),0);
      IF (v_rows_to_return=0) THEN v_rows_to_return=1; END IF;
      WITH updated AS(
           UPDATE obj_user_device_attestation
              SET next_attestation=(SELECT now() at time zone 'utc')+frequency*INTERVAL '1 second'
            WHERE id_user_device_attestation IN(
                  SELECT id_user_device_attestation FROM obj_user_device_attestation
                   WHERE next_attestation<(SELECT now() at time zone 'utc')
                     AND type=p_attestation_type
                     AND ((type!=c_type_safetynet) OR
                          (type=c_type_safetynet AND id_user_device_attestation NOT IN(
                            SELECT id_user_device_attestation FROM o2o_user_device_attestation_setting
                             WHERE compromised_state='HARD')))
                     AND is_periodic_attestation_enabled=true
                   ORDER BY priority DESC,next_attestation LIMIT v_rows_to_return)
        RETURNING id_user_device)
      SELECT array(SELECT id_user_device FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
    'SELECT ud.id_user_device,ud.id_user,ud.id_device,ud.perimeter_uuid,ud.perimeter_state_type,
            ud.last_perimeter_state_changed,ud.reactivation_count,ud.last_communication,ud.name,
            ud.language,ud.id_effective_swc,ud.device_encryption_key,ud.previous_key,ud.pending_key,
            ud.password_state,ud.compliance_rule_enabled,ud.enrollment_token,ud.enrollment_secret,
            ud.unlock_token,ud.gatekeeping_eas_state,ud.decrpt_engine_public_cert,
            ud.decrpt_engine_private_cert,ud.guid,ud.created,ud.modified,ud.enrollment_type,
            ud.id_server,ud.id_rcp_routing_entry,ud.identity_mgmt_cert,ud.IT_plcy_name,
            ud.IT_plcy_applied_time,ud.id_user_owner,ud.last_password_change_time
       FROM obj_user_device ud
       JOIN obj_user_device_attestation uda ON uda.id_user_device=ud.id_user_device AND uda.type=$1
      WHERE ud.id_user_device=ANY(CAST($2 AS integer[]))
        AND ud.perimeter_state_type<>''MIGRATE''
      ORDER BY uda.priority DESC, ud.id_user_device'
        USING p_attestation_type,v_int_array;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 4: getComplianceSchedNextRunList
-- ============================================================
ALTER FUNCTION uem.getcomplianceschednextrunlist(integer)
  RENAME TO getcomplianceschednextrunlist_fn;

CREATE OR REPLACE PROCEDURE uem.getComplianceSchedNextRunList(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState text; v_SQLErrorMsg text; v_SQLErrorDetail text; v_SQLDefErrorHint text;
  c_indexQueueName integer := f_getLockHandle('CompSchedQ');
  c_proc_name varchar(30) := 'getComplianceSchedNextRunList';
  v_array_id_udcs bigint[];
BEGIN
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS(
            WITH T AS(SELECT id_user_device_comp_schedule FROM obj_user_device_comp_schedule
                       WHERE ((next_run<(SELECT now() at time zone 'utc')) AND run_count>=0))
        UPDATE obj_user_device_comp_schedule
           SET next_run=next_run+interval*INTERVAL '1 second',
               run_count=run_count-1, modified=(SELECT now() at time zone 'utc')
          FROM T WHERE obj_user_device_comp_schedule.id_user_device_comp_schedule=T.id_user_device_comp_schedule
        RETURNING obj_user_device_comp_schedule.id_user_device_comp_schedule)
      SELECT array(SELECT id_user_device_comp_schedule FROM updated) INTO v_array_id_udcs;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
          'SELECT os.id_user_device_comp_schedule,os.id_user_device_comp_state,os.start_time,
                  os.interval,os.run_count,os.next_run,os.created,os.modified
             FROM obj_user_device_comp_schedule os
            WHERE os.id_user_device_comp_schedule=ANY(CAST($1 AS bigint[]))'
      USING v_array_id_udcs;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 5: getLicenseNextSyncList_036_28
-- ============================================================
ALTER FUNCTION uem.getlicensenextsynclist_036_28(integer)
  RENAME TO getlicensenextsynclist_036_28_fn;

CREATE OR REPLACE PROCEDURE uem.getLicenseNextSyncList_036_28(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLErrorState TEXT; v_SQLErrorMsg TEXT; v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  c_proc_name VARCHAR(30):='getLicenseNextSyncList_036_28';
  c_indexQueueName INTEGER:=f_getLockHandle('LicenseQ');
  c_cfg_stng_max_tnts_to_sync VARCHAR(40):='licensingsync.max.number.tenants.sync';
  c_cfg_stng_next_callback_time VARCHAR(40):='licensingsync.minimum.callbacktime';
  c_tnt_cfg_stng_next_cback_time CITEXT:='licensingsync.tenant.minimum.callbacktime';
  c_totalMillisecondInDay INTEGER:=86400000;
  c_tenant0_external_id VARCHAR(40):='502BD069-76C3-4834-BEBE-D7F120BCF3EF';
  v_cfg_setting_value text; v_synTime TIMESTAMP; v_globalMinCallbackTime VARCHAR(2000);
  v_int_array BIGINT[]; v_upToNumTenants INTEGER;
BEGIN
  v_cfg_setting_value:=getglobalcfgsettingvalue(1,c_cfg_stng_max_tnts_to_sync,NULL,0,v_cfg_setting_value);
  v_upToNumTenants=CAST(v_cfg_setting_value AS INTEGER);
  v_globalMinCallbackTime:=getglobalcfgsettingvalue(1,c_cfg_stng_next_callback_time,NULL,0,v_globalMinCallbackTime);
  v_syntime:=(SELECT now() at time zone 'utc');
    BEGIN
      PERFORM pg_advisory_xact_lock(c_indexQueueName);
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    BEGIN
      WITH updated AS (WITH T AS (SELECT ROW_NUMBER() OVER(ORDER BY id_licensing_state) AS rownum,id_licensing_state
                                    FROM obj_licensing_state
                                   WHERE next_synchronization<=v_syntime
                                     AND id_tenant NOT IN(SELECT id_tenant FROM obj_tenant_cfg_setting s
                                                           INNER JOIN def_cfg_setting_dfn d ON s.id_setting_definition=d.id_setting_definition
                                                          WHERE d.name='licensingsync.tenant.minimum.callbacktime' AND s.Value='-1')
                                     AND id_tenant IS NOT NULL
                                     AND id_tenant NOT IN(SELECT id_tenant FROM obj_tenant
                                                           WHERE external_tenant_id=c_tenant0_external_id OR is_enabled=false))
                  UPDATE obj_licensing_state
                     SET next_synchronization=v_syntime+(COALESCE(CAST(f_getTenantCfgSettingValue(
                           p_id_tenant:=id_tenant,p_cfg_setting_dfn_name:=c_tnt_cfg_stng_next_cback_time,
                           p_cfg_setting_tag:=NULL) AS INTEGER),CAST(v_globalMinCallbackTime AS INTEGER)))*INTERVAL '1 millisecond'
                        ,modified=v_syntime
                    FROM T WHERE obj_licensing_state.id_licensing_state=T.id_licensing_state
                      AND obj_licensing_state.next_synchronization IS NOT NULL
                RETURNING obj_licensing_state.id_licensing_state)
      SELECT array(SELECT id_licensing_state FROM updated) INTO v_int_array;
    EXCEPTION
      WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                                v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
        v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                       ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
        RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
        RETURN;
    END;
    OPEN prc_return_1 FOR EXECUTE
      'SELECT ls.id_licensing_state,ls.id_tenant,ls.next_count_update,ls.next_synchronization,
              ls.created,ls.modified,ls.last_report_immedite_usrdvc_md,ls.last_report_immedite_usrdvc_id,
              ls.last_report_peridcal_usrdvc_md,ls.last_report_peridcal_usrdvc_id,ls.last_licensing_status,
              ls.aaa_status,ls.intsct_status,ls.elm_status
         FROM obj_licensing_state ls WHERE ls.id_licensing_state=ANY(CAST($1 AS bigint[]))'
      USING v_int_array;
EXCEPTION
  WHEN others THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 6: getUsrDvcEvntPrd_52_01
-- ============================================================
ALTER FUNCTION uem.getusrdvcevntprd_52_01(integer, integer, integer)
  RENAME TO getusrdvcevntprd_52_01_fn;

CREATE OR REPLACE PROCEDURE uem.getUsrDvcEvntPrd_52_01(
  INOUT prc_return_1 refcursor,
  p_insidenestedtxn integer,
  p_maxResultsPerTenant integer,
  p_isTenantBased integer
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT; v_numRowsAffected INTEGER;
  c_proc_name CONSTANT VARCHAR(30):='getUsrDvcEvntPrd';
  c_policy_settingDfn_Name CONSTANT VARCHAR(255):='bbm_protected_encryption';
  c_policy_category_name CONSTANT VARCHAR(256):='IT_CONFIG';
  c_device_stn_dfn_name_iccid CONSTANT VARCHAR(256):='device.sim.iccid';
  c_device_stn_dfn_name_imsi CONSTANT VARCHAR(256):='device.sim.imsi';
  c_device_stn_dfn_name_netcur CONSTANT VARCHAR(256):='device.network.current';
  c_perimeter_state_type CONSTANT VARCHAR(256):='ENROLLED';
  v_current_time TIMESTAMP:=(SELECT now() at time zone 'utc');
  v_policy_setting_dfn_id def_policy_setting_definition.id_policy_setting_definition%TYPE;
  v_device_stn_dfn_id_iccid def_device_setting_definition.id_device_setting_definition%TYPE;
  v_device_stn_dfn_id_imsi def_device_setting_definition.id_device_setting_definition%TYPE;
  v_device_stn_dfn_id_netcur def_device_setting_definition.id_device_setting_definition%TYPE;
  v_SQL VARCHAR(8000); v_id_policy_category BIGINT;
BEGIN
  v_id_policy_category=(SELECT id_policy_category FROM def_policy_category WHERE name='SINGLE_APP_MODE');
  SELECT id_policy_setting_definition INTO v_policy_setting_dfn_id
    FROM def_policy_setting_definition psd WHERE psd.name=c_policy_settingDfn_Name;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_iccid
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_iccid;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_imsi
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_imsi;
  SELECT id_device_setting_definition INTO v_device_stn_dfn_id_netcur
    FROM def_device_setting_definition dsd WHERE dsd.name=c_device_stn_dfn_name_netcur;
  IF (p_isTenantBased=1) THEN
    CREATE TEMP TABLE v_udepAffectedRows ON COMMIT DROP AS
      SELECT udep.id_user_device_event_periodic,udep.id_user_device,udep.id_tenant,udep.eligibility_timestamp
        FROM obj_user_device_event_periodic udep JOIN obj_licensing_state ls ON udep.id_tenant=ls.id_tenant
       WHERE udep.eligibility_timestamp>ls.last_report_peridcal_usrdvc_md
          OR (udep.eligibility_timestamp=ls.last_report_peridcal_usrdvc_md
              AND udep.id_user_device_event_periodic>ls.last_report_peridcal_usrdvc_id);
  ELSE
    CREATE TEMP TABLE v_udepAffectedRows ON COMMIT DROP AS
      SELECT udep.id_user_device_event_periodic,udep.id_user_device,udep.id_tenant,udep.eligibility_timestamp
        FROM obj_user_device_event_periodic udep JOIN obj_licensing_state ls ON ls.id_tenant IS NULL
       WHERE udep.eligibility_timestamp>ls.last_report_peridcal_usrdvc_md
          OR (udep.eligibility_timestamp=ls.last_report_peridcal_usrdvc_md
              AND udep.id_user_device_event_periodic>ls.last_report_peridcal_usrdvc_id);
  END IF;
  CREATE INDEX IX_udepAffectedRows ON v_udepAffectedRows(id_user_device);
  CREATE TEMP TABLE v_usersWithPolicyApplied ON COMMIT DROP AS
    SELECT DISTINCT uep.id_user, ps.value
      FROM n2n_user_effective_policy uep
      JOIN obj_effective_policy ep ON uep.id_effective_policy=ep.id_effective_policy
      JOIN obj_effective_policy_param epp ON ep.id_effective_policy=epp.id_effective_policy
      JOIN obj_policy p ON epp.id_policy=p.id_policy
      JOIN obj_policy_setting ps ON p.id_policy=ps.id_policy AND ps.id_policy_setting_definition=v_policy_setting_dfn_id
      JOIN obj_user_device oud ON uep.id_user=oud.id_user
      JOIN v_udepAffectedRows udar ON oud.id_user_device=udar.id_user_device;
  CREATE INDEX ix_usersWithPolicyApplied ON v_usersWithPolicyApplied(id_user);
  v_SQL:='WITH cte AS(SELECT ROW_NUMBER() OVER ('
     ||CASE p_isTenantBased WHEN 1 THEN 'PARTITION BY udar.id_tenant ' ELSE '' END
     ||'ORDER BY udar.eligibility_timestamp,udar.id_user_device_event_periodic) rownum
            ,ud.id_user_device id_user_device_event,null id_tenant,4 event_type,f.name os_type
            ,d.udid hGuid,ud.perimeter_uuid sGuid,d.imei,ds.value iccid,d.meid
            ,d.network_home home_carrier_name,d.phone_number msisdn,dh.name device_vendor_id
            ,f_getModelName(dh.display_name,dh.model) AS device_model_id,os.version os_version
            ,ud.language,ud.perimeter_state_type,ud.enrollment_type,hv.name,bbm.value
            ,ud.id_user_device,ds.id_device_setting,dsimsi.value imsi,vcn.value visiting_carrier_name
            ,u.guid user_guid,ud.guid,t.external_tenant_id,u.ecoid,t.organization_id,t.country country_code
            ,u.email_address email_address,sdg.type device_use_category_type
            ,CASE WHEN rsp.id_policy IS NOT NULL THEN true
                  WHEN sdg.id_shared_device_group IS NOT NULL THEN false
                  WHEN COALESCE(udfp.id_effective_policy,uep.id_effective_policy) IS NULL THEN false
                  ELSE true END AS is_single_purpose_device
       FROM v_udepAffectedRows udar
       JOIN obj_user_device ud ON udar.id_user_device=ud.id_user_device AND ud.perimeter_state_type=$8
       JOIN obj_device d ON ud.id_device=d.id_device
       JOIN def_device_os os ON COALESCE(d.id_device_os_host,d.id_device_os)=os.id_device_os
       JOIN def_device_os_family f ON os.id_device_os_family=f.id_device_os_family
       JOIN def_device_hardware dh ON d.id_device_hardware=dh.id_device_hardware
       JOIN def_device_hardware_vendor hv ON dh.id_device_hardware_vendor=hv.id_device_hardware_vendor
  LEFT JOIN obj_device_setting ds ON d.id_device=ds.id_device AND ds.id_device_setting_definition=$1
  LEFT JOIN obj_device_setting dsimsi ON d.id_device=dsimsi.id_device AND dsimsi.id_device_setting_definition=$2
  LEFT JOIN obj_device_setting vcn ON d.id_device=vcn.id_device AND vcn.id_device_setting_definition=$3
       JOIN obj_user u ON ud.id_user=u.id_user JOIN obj_tenant t ON t.id_tenant=u.id_tenant
  LEFT JOIN(SELECT id_user,value FROM v_usersWithPolicyApplied UNION
            SELECT ou.id_user,ps.value FROM obj_user ou
              JOIN obj_policy p ON ou.id_tenant=p.id_tenant
              JOIN obj_policy_setting ps ON p.id_policy=ps.id_policy AND ps.id_policy_setting_definition=$4
              JOIN obj_user_device oud ON ou.id_user=oud.id_user
              JOIN v_udepAffectedRows udar ON oud.id_user_device=udar.id_user_device
             WHERE p.id_policy_category=(SELECT id_policy_category FROM def_policy_category WHERE name=$5)
               AND p.reserved=true
               AND NOT EXISTS(SELECT 1 FROM v_usersWithPolicyApplied uspa WHERE uspa.id_user=ou.id_user)
           ) bbm ON bbm.id_user=u.id_user
  LEFT JOIN obj_shared_device_group sdg ON sdg.id_user_owner=ud.id_user_owner
  LEFT JOIN obj_shared_device_group_resource_set rs ON rs.id_shared_device_group=sdg.id_shared_device_group
         AND rs.is_default=CASE WHEN ud.id_user_owner=ud.id_user THEN true ELSE false END
  LEFT JOIN n2n_shDvcGrpRsrcSet_policy rsp ON rsp.id_shared_device_group_resource_set=rs.id_shared_device_group_resource_set
         AND rsp.id_policy IN(SELECT id_policy FROM obj_policy WHERE id_policy_category=$9)
  LEFT JOIN n2n_usr_dvc_efctv_plcy udfp ON udfp.id_user_device=ud.id_user_device
         AND udfp.id_effective_policy IN(SELECT id_effective_policy FROM obj_effective_policy WHERE id_policy_category=$9)
  LEFT JOIN n2n_user_effective_policy uep ON uep.id_user=ud.id_user
         AND uep.id_effective_policy IN(SELECT id_effective_policy FROM obj_effective_policy WHERE id_policy_category=$9))
  SELECT cte.rownum,cte.id_user_device_event,cte.id_tenant,cte.event_type,cte.os_type,cte.hGuid,cte.sGuid,
         cte.imei,cte.iccid,cte.meid,cte.home_carrier_name,cte.msisdn,cte.device_vendor_id,cte.device_model_id,
         cte.os_version,cte.language,cte.perimeter_state_type,cte.enrollment_type,cte.name,cte.value,
         f_getFeatures(cte.id_user_device,cte.value) bes_features,cte.id_user_device,cte.id_device_setting,
         cte.imsi,cte.visiting_carrier_name,cte.user_guid,$6 created,$6 modified,cte.guid,
         cte.external_tenant_id,cte.ecoid,cte.organization_id,f_getKnoxDeviceKeys(cte.id_user_device) knox_device_keys,
         f_getMaxBesVersion() bes_version,cte.country_code,cte.email_address,cte.device_use_category_type,
         cte.is_single_purpose_device FROM cte WHERE rownum<=$7';
    OPEN prc_return_1 FOR EXECUTE v_SQL
      USING v_device_stn_dfn_id_iccid,v_device_stn_dfn_id_imsi,v_device_stn_dfn_id_netcur,
            v_policy_setting_dfn_id,c_policy_category_name,v_current_time,p_maxResultsPerTenant,
            c_perimeter_state_type,v_id_policy_category;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;

-- ============================================================
-- Fix 7: getLicenseCommand  (cursor was at position 3 in Oracle;
--         Hibernate always registers cursor at position 1 — reorder here)
-- ============================================================
ALTER FUNCTION uem.getlicensecommand(integer, integer)
  RENAME TO getlicensecommand_fn;

CREATE OR REPLACE PROCEDURE uem.getLicenseCommand(
  INOUT prc_return_1 refcursor,
  p_insideNestedTxn integer,
  p_tenantId integer DEFAULT NULL
)
LANGUAGE plpgsql AS $$
DECLARE
  v_SQLError INTEGER DEFAULT 0; v_SQLErrorState TEXT; v_SQLErrorMsg TEXT;
  v_SQLErrorDetail TEXT; v_SQLDefErrorHint TEXT;
  v_UserDefError VARCHAR(10); v_UserDefErrorMsg TEXT;
  c_proc_name CONSTANT VARCHAR(30):='getLicenseCommand';
  c_indexQueueName CONSTANT VARCHAR(30):='LicenseCmdQ';
  c_cfg_stng_license_cmd_hwm CONSTANT VARCHAR(40):='mdm.license.commmand.highwatermark';
  v_indexQueueId INTEGER; v_highWaterMark BIGINT; v_highWaterMark1 BIGINT;
  v_highWaterMark2 BIGINT; v_nextHighWaterMark BIGINT;
  v_batchSize INT; v_commandName VARCHAR(64);
BEGIN
  IF (p_insideNestedTxn IS NULL OR p_insideNestedTxn NOT IN (0,1)) THEN
    RAISE EXCEPTION USING MESSAGE=c_proc_name||': Illegal parameter value (p_insideNestedTxn)=('||p_insideNestedTxn||'); must be 0 or 1.';
    RETURN;
  END IF;
  v_indexQueueId:=f_getLockHandle(c_indexQueueName);
  BEGIN
    PERFORM pg_advisory_xact_lock(v_indexQueueId);
  EXCEPTION
    WHEN OTHERS THEN
      GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                              v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
      v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                     ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint||
                     ' when acquiring pg_advisory_xact_lock for queue ['||c_indexQueueName||'].';
      RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
      RETURN;
  END;
  IF (p_tenantId IS NOT NULL) THEN
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM obj_tenant WHERE id_tenant=p_tenantId) THEN
        RAISE EXCEPTION USING MESSAGE=c_proc_name||': Tenant '||p_tenantId||' not found.';
      END IF;
      v_highWaterMark:=(SELECT value FROM obj_internal_tnt_cfg_setting
                         WHERE id_tenant=p_tenantId AND name=c_cfg_stng_license_cmd_hwm);
      IF (v_highWaterMark IS NULL) THEN
        BEGIN
          v_highWaterMark:=0;
          INSERT INTO obj_internal_tnt_cfg_setting(id_tenant,name,value)
            VALUES(p_tenantId,c_cfg_stng_license_cmd_hwm,v_highWaterMark);
        EXCEPTION
          WHEN OTHERS THEN
            RAISE EXCEPTION USING MESSAGE=c_proc_name||': Error inserting tenant high watermark.';
            RETURN;
        END;
      END IF;
    END;
  ELSE
    v_highWaterMark:=0;
  END IF;
  IF (p_tenantId IS NOT NULL) THEN
    SELECT command_name,id_tenant INTO v_commandName,p_tenantId
      FROM obj_license_command_queue
     WHERE id_license_command_queue>v_highWaterMark AND id_tenant=p_tenantId
     ORDER BY id_license_command_queue LIMIT 1;
  ELSE
    SELECT command_name,id_tenant INTO v_commandName,p_tenantId
      FROM obj_license_command_queue
     WHERE id_license_command_queue>0 AND id_tenant IS NULL
     ORDER BY id_license_command_queue LIMIT 1;
  END IF;
  IF (v_commandName IS NOT NULL) THEN
    SELECT value INTO v_batchSize FROM obj_global_cfg_setting
     WHERE id_setting_definition=(SELECT id_setting_definition FROM def_cfg_setting_dfn
                                   WHERE name='mdm.license.helm.'||LOWER(v_commandName)||'.batch.size');
  ELSE
    v_batchSize:=0;
  END IF;
  IF (v_batchSize IS NULL) THEN
    RAISE EXCEPTION USING MESSAGE=c_proc_name||'Unable to locate batch size configuration row (mdm.license.helm.'||LOWER(v_commandName)||'.batch.size)';
  END IF;
  v_highWaterMark1:=(SELECT MIN(id_license_command_queue)-1 FROM obj_license_command_queue
                      WHERE command_name!=v_commandName AND id_license_command_queue>v_highWaterMark
                        AND((p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                         OR(p_tenantId IS NULL AND id_tenant IS NULL)));
  IF v_highWaterMark1 IS NULL THEN
    v_highWaterMark1:=(SELECT MAX(id_license_command_queue) FROM obj_license_command_queue
                        WHERE(p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                          OR(p_tenantId IS NULL AND id_tenant IS NULL));
  END IF;
  v_highWaterMark2:=(SELECT MAX(id_license_command_queue)
                      FROM(SELECT id_license_command_queue FROM obj_license_command_queue
                            WHERE id_license_command_queue>v_highWaterMark
                              AND((p_tenantId IS NOT NULL AND id_tenant=p_tenantId)
                               OR(p_tenantId IS NULL AND id_tenant IS NULL))
                            ORDER BY id_license_command_queue LIMIT v_batchSize) x);
  IF(v_highWaterMark1>v_highWaterMark2) THEN v_nextHighWaterMark=v_highWaterMark2;
  ELSE v_nextHighWaterMark=v_highWaterMark1; END IF;
  IF (p_tenantId IS NOT NULL) THEN
    OPEN prc_return_1 FOR
    SELECT id_license_command_queue,command_name,path_parameters,query_parameters,request_body,
           id_tenant,id_user,first_attempt,created,modified
      FROM obj_license_command_queue
     WHERE id_license_command_queue BETWEEN v_highWaterMark+1 AND v_nextHighWaterMark
       AND id_tenant=p_tenantId ORDER BY id_license_command_queue;
  ELSE
    OPEN prc_return_1 FOR
    SELECT id_license_command_queue,command_name,path_parameters,query_parameters,request_body,
           id_tenant,id_user,first_attempt,created,modified
      FROM obj_license_command_queue
     WHERE id_license_command_queue BETWEEN v_highWaterMark+1 AND v_nextHighWaterMark
       AND id_tenant IS NULL ORDER BY id_license_command_queue;
  END IF;
EXCEPTION
  WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_SQLErrorState=RETURNED_SQLSTATE, v_SQLErrorMsg=MESSAGE_TEXT,
                            v_SQLErrorDetail=PG_EXCEPTION_DETAIL, v_SQLDefErrorHint=PG_EXCEPTION_HINT;
    v_SQLErrorMsg:=' SQL State: '||v_SQLErrorState||' Message: '||v_SQLErrorMsg||
                   ' Details: '||v_SQLErrorDetail||' Hint: '||v_SQLDefErrorHint;
    RAISE EXCEPTION USING MESSAGE=c_proc_name||' Error: '||v_SQLErrorMsg, ERRCODE=v_SQLErrorState;
END;
$$;
