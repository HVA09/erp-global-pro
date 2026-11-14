-- ERP Global Pro — PostgreSQL signed tenant-context security prototype.
-- Apply only in a disposable PostgreSQL test database as a database owner.
-- Preconditions: roles {{SECURITY_OWNER_ROLE}} (NOLOGIN, NOSUPERUSER, NOBYPASSRLS)
-- and {{APP_ROLE}} (LOGIN, NOSUPERUSER, NOBYPASSRLS) already exist; {{CONTROL_SCHEMA}}.companies
-- and {{CONTROL_SCHEMA}}.company_memberships and {{CONTROL_SCHEMA}}.users exist.
-- This file does NOT migrate the ERP schema or enable RLS on business tables.

BEGIN;

-- Fail closed if the roles do not have the expected low-privilege posture.
DO $roles$
DECLARE
    security_role record;
    application_role record;
BEGIN
    SELECT * INTO security_role FROM pg_catalog.pg_roles WHERE rolname = '{{SECURITY_OWNER_ROLE}}';
    SELECT * INTO application_role FROM pg_catalog.pg_roles WHERE rolname = '{{APP_ROLE}}';

    IF security_role.rolname IS NULL
       OR security_role.rolcanlogin
       OR security_role.rolsuper
       OR security_role.rolbypassrls
       OR security_role.rolcreatedb
       OR security_role.rolcreaterole
       OR security_role.rolinherit THEN
        RAISE EXCEPTION 'security owner role must exist and be NOLOGIN/NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE/NOINHERIT';
    END IF;

    IF application_role.rolname IS NULL
       OR NOT application_role.rolcanlogin
       OR application_role.rolsuper
       OR application_role.rolbypassrls
       OR application_role.rolcreatedb
       OR application_role.rolcreaterole
       OR application_role.rolinherit THEN
        RAISE EXCEPTION 'application role must exist and be LOGIN/NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE/NOINHERIT';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member = application_role.oid)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member = security_role.oid) THEN
        RAISE EXCEPTION 'application and security-owner roles must not inherit or SET ROLE to other roles';
    END IF;
END;
$roles$;

CREATE SCHEMA IF NOT EXISTS {{CRYPTO_SCHEMA}} AUTHORIZATION {{SECURITY_OWNER_ROLE}};
GRANT CREATE ON SCHEMA {{CRYPTO_SCHEMA}} TO CURRENT_USER;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA {{CRYPTO_SCHEMA}};
REVOKE CREATE ON SCHEMA {{CRYPTO_SCHEMA}} FROM CURRENT_USER;
CREATE SCHEMA IF NOT EXISTS {{SECURITY_SCHEMA}} AUTHORIZATION {{SECURITY_OWNER_ROLE}};
DO $schemas$
DECLARE
    app_oid oid;
    security_owner_oid oid;
    crypto_schema_oid oid;
    security_schema_oid oid;
    control_schema_oid oid;
    app_owns_control_table boolean;
BEGIN
    SELECT oid INTO app_oid FROM pg_catalog.pg_roles WHERE rolname = '{{APP_ROLE}}';
    SELECT oid INTO security_owner_oid FROM pg_catalog.pg_roles WHERE rolname = '{{SECURITY_OWNER_ROLE}}';
    SELECT oid INTO crypto_schema_oid FROM pg_catalog.pg_namespace WHERE nspname = '{{CRYPTO_SCHEMA}}';
    SELECT oid INTO security_schema_oid FROM pg_catalog.pg_namespace WHERE nspname = '{{SECURITY_SCHEMA}}';
    SELECT oid INTO control_schema_oid FROM pg_catalog.pg_namespace WHERE nspname = '{{CONTROL_SCHEMA}}';

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_extension AS e
          JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
         WHERE e.extname = 'pgcrypto' AND n.nspname = '{{CRYPTO_SCHEMA}}'
    ) THEN
        RAISE EXCEPTION 'pgcrypto must be installed in the configured trusted crypto schema';
    END IF;
    IF '{{SECURITY_SCHEMA}}' IN ('public', 'pg_catalog', 'information_schema')
       OR '{{CRYPTO_SCHEMA}}' IN ('public', 'pg_catalog', 'information_schema')
       OR '{{CONTROL_SCHEMA}}' IN ('public', 'pg_catalog', 'information_schema') THEN
        RAISE EXCEPTION 'security, crypto and control schemas must be dedicated non-system schemas';
    END IF;
    IF security_schema_oid IS NULL OR
       (SELECT nspowner FROM pg_catalog.pg_namespace WHERE oid = security_schema_oid) <> security_owner_oid THEN
        RAISE EXCEPTION 'security schema must be owned by the NOLOGIN security owner role';
    END IF;
    IF crypto_schema_oid IS NULL OR
       (SELECT nspowner FROM pg_catalog.pg_namespace WHERE oid = crypto_schema_oid) <> security_owner_oid OR
       pg_catalog.has_schema_privilege(app_oid, crypto_schema_oid, 'CREATE') THEN
        RAISE EXCEPTION 'crypto schema must be owned by security owner and not writable by application role';
    END IF;
    IF control_schema_oid IS NULL OR
       (SELECT nspowner FROM pg_catalog.pg_namespace WHERE oid = control_schema_oid) = app_oid OR
       pg_catalog.has_schema_privilege(app_oid, control_schema_oid, 'CREATE') THEN
        RAISE EXCEPTION 'application role must not own or CREATE in the control schema';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS t
          JOIN pg_catalog.pg_namespace AS n ON n.oid = t.relnamespace
         WHERE n.nspname = '{{CONTROL_SCHEMA}}'
           AND t.relname IN ('companies', 'company_memberships', 'users')
           AND t.relkind IN ('r','p')
           AND t.relowner = app_oid
    ) INTO app_owns_control_table;
    IF app_owns_control_table THEN
        RAISE EXCEPTION 'application role must not own control-plane tables';
    END IF;
END;
$schemas$;

REVOKE CREATE ON SCHEMA {{CRYPTO_SCHEMA}} FROM PUBLIC, {{APP_ROLE}};
REVOKE CREATE ON SCHEMA {{SECURITY_SCHEMA}} FROM PUBLIC, {{APP_ROLE}};
REVOKE CREATE ON SCHEMA {{CONTROL_SCHEMA}} FROM PUBLIC, {{APP_ROLE}};
GRANT USAGE ON SCHEMA {{CRYPTO_SCHEMA}} TO {{SECURITY_OWNER_ROLE}};
GRANT USAGE ON SCHEMA {{CONTROL_SCHEMA}} TO {{SECURITY_OWNER_ROLE}};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {{CRYPTO_SCHEMA}} TO {{SECURITY_OWNER_ROLE}};
GRANT USAGE ON SCHEMA {{SECURITY_SCHEMA}} TO {{APP_ROLE}};

CREATE TABLE IF NOT EXISTS {{SECURITY_SCHEMA}}.tenant_signing_keys (
    key_id text PRIMARY KEY CHECK (key_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'),
    secret bytea NOT NULL CHECK (octet_length(secret) >= 32),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE {{SECURITY_SCHEMA}}.tenant_signing_keys OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON {{SECURITY_SCHEMA}}.tenant_signing_keys FROM PUBLIC;
REVOKE ALL ON {{SECURITY_SCHEMA}}.tenant_signing_keys FROM {{APP_ROLE}};
GRANT SELECT ON {{CONTROL_SCHEMA}}.companies, {{CONTROL_SCHEMA}}.company_memberships, {{CONTROL_SCHEMA}}.users TO {{SECURITY_OWNER_ROLE}};
-- Do not expose the global control plane directly to the application role.
-- Add narrow, separately reviewed SECURITY DEFINER read/admin routines before
-- integrating company/member/user management APIs.
REVOKE ALL PRIVILEGES
    ON {{CONTROL_SCHEMA}}.companies, {{CONTROL_SCHEMA}}.company_memberships, {{CONTROL_SCHEMA}}.users
    FROM PUBLIC, {{APP_ROLE}};

CREATE OR REPLACE FUNCTION {{SECURITY_SCHEMA}}.current_company_id()
RETURNS bigint
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, {{SECURITY_SCHEMA}}, {{CRYPTO_SCHEMA}}, pg_temp
AS $function$
DECLARE
    raw_payload text;
    supplied_signature text;
    claims jsonb;
    signing_secret bytea;
    expected_signature bytea;
    provided_signature bytea;
    signature_difference integer := 0;
    signature_index integer;
    claim_user_id bigint;
    claim_company_id bigint;
    issued_at bigint;
    expires_at bigint;
    membership_is_active boolean;
BEGIN
    raw_payload := pg_catalog.current_setting('erp.tenant_payload', true);
    supplied_signature := pg_catalog.current_setting('erp.tenant_signature', true);

    IF raw_payload IS NULL OR pg_catalog.octet_length(raw_payload) > 4096 THEN
        RETURN NULL;
    END IF;
    IF supplied_signature IS NULL OR supplied_signature !~ '^[0-9a-f]{64}$' THEN
        RETURN NULL;
    END IF;

    BEGIN
        claims := raw_payload::jsonb;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN NULL;
    END;

    IF pg_catalog.jsonb_typeof(claims) IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(claims -> 'iss') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(claims -> 'aud') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(claims -> 'kid') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(claims -> 'sub') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(claims -> 'company_id') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(claims -> 'iat') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(claims -> 'exp') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(claims -> 'jti') IS DISTINCT FROM 'string'
       OR NOT (claims ?& ARRAY['iss','aud','kid','sub','company_id','iat','exp','jti'])
       OR (claims - ARRAY['iss','aud','kid','sub','company_id','iat','exp','jti']) <> '{}'::jsonb
       OR claims ->> 'iss' IS DISTINCT FROM 'erp-global-pro'
       OR claims ->> 'aud' IS DISTINCT FROM 'erp-postgres-tenant-context'
       OR claims ->> 'kid' IS NULL
       OR claims ->> 'sub' !~ '^[1-9][0-9]{0,18}$'
       OR claims ->> 'company_id' !~ '^[1-9][0-9]{0,18}$'
       OR claims ->> 'iat' !~ '^[0-9]{1,12}$'
       OR claims ->> 'exp' !~ '^[0-9]{1,12}$' THEN
        RETURN NULL;
    END IF;

    claim_user_id := (claims ->> 'sub')::bigint;
    claim_company_id := (claims ->> 'company_id')::bigint;
    issued_at := (claims ->> 'iat')::bigint;
    expires_at := (claims ->> 'exp')::bigint;

    IF issued_at > pg_catalog.date_part('epoch', pg_catalog.statement_timestamp())::bigint + 30
       OR expires_at <= pg_catalog.date_part('epoch', pg_catalog.statement_timestamp())::bigint
       OR expires_at <= issued_at
       OR expires_at - issued_at > 900
       OR pg_catalog.length(COALESCE(claims ->> 'jti', '')) NOT BETWEEN 16 AND 128 THEN
        RETURN NULL;
    END IF;

    SELECT k.secret
      INTO signing_secret
      FROM {{SECURITY_SCHEMA}}.tenant_signing_keys AS k
     WHERE k.key_id = claims ->> 'kid' AND k.active = true;
    IF signing_secret IS NULL THEN
        RETURN NULL;
    END IF;

    expected_signature := {{CRYPTO_SCHEMA}}.hmac(
        pg_catalog.convert_to(raw_payload, 'UTF8'), signing_secret, 'sha256'
    );
    provided_signature := pg_catalog.decode(supplied_signature, 'hex');
    -- Compare all 32 bytes to avoid a data-dependent early-exit equality check.
    FOR signature_index IN 0..31 LOOP
        signature_difference := signature_difference |
            (pg_catalog.get_byte(expected_signature, signature_index) #
             pg_catalog.get_byte(provided_signature, signature_index));
    END LOOP;
    IF signature_difference <> 0 THEN
        RETURN NULL;
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM {{CONTROL_SCHEMA}}.company_memberships AS m
          JOIN {{CONTROL_SCHEMA}}.companies AS c ON c.id = m.company_id
          JOIN {{CONTROL_SCHEMA}}.users AS u ON u.id = m.user_id
         WHERE m.user_id = claim_user_id
           AND m.company_id = claim_company_id
           AND m.status = 'active'
           AND c.status = 'active'
           AND u.is_active IS TRUE
    ) INTO membership_is_active;

    IF NOT membership_is_active THEN
        RETURN NULL;
    END IF;
    RETURN claim_company_id;
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN NULL;
END;
$function$;

ALTER FUNCTION {{SECURITY_SCHEMA}}.current_company_id() OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON FUNCTION {{SECURITY_SCHEMA}}.current_company_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{SECURITY_SCHEMA}}.current_company_id() TO {{APP_ROLE}};

-- Return the principal only after the signed company context has been fully
-- verified (signature, expiry, user status, membership and company status).
-- This is deliberately not a login/bootstrap function: a valid company-scoped
-- context must already exist.
CREATE OR REPLACE FUNCTION {{SECURITY_SCHEMA}}.current_user_id()
RETURNS bigint
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, {{SECURITY_SCHEMA}}, {{CRYPTO_SCHEMA}}, pg_temp
AS $function$
DECLARE
    verified_company_id bigint;
    raw_payload text;
    claims jsonb;
BEGIN
    verified_company_id := {{SECURITY_SCHEMA}}.current_company_id();
    IF verified_company_id IS NULL THEN
        RETURN NULL;
    END IF;
    raw_payload := pg_catalog.current_setting('erp.tenant_payload', true);
    BEGIN
        claims := raw_payload::jsonb;
        RETURN (claims ->> 'sub')::bigint;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN NULL;
    END;
END;
$function$;

ALTER FUNCTION {{SECURITY_SCHEMA}}.current_user_id() OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON FUNCTION {{SECURITY_SCHEMA}}.current_user_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{SECURITY_SCHEMA}}.current_user_id() TO {{APP_ROLE}};

-- Narrow read-only control-plane functions. The app role still has no direct
-- SELECT on control tables; these functions return only the current user's
-- active memberships and the selected company's safe profile fields.
CREATE OR REPLACE FUNCTION {{SECURITY_SCHEMA}}.list_my_companies()
RETURNS TABLE (
    company_id bigint,
    company_name text,
    country text,
    currency text,
    industry text,
    membership_role text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, {{SECURITY_SCHEMA}}, {{CRYPTO_SCHEMA}}, pg_temp
AS $function$
    SELECT c.id, c.name, c.country, c.currency, c.industry, m.role
      FROM {{CONTROL_SCHEMA}}.company_memberships AS m
      JOIN {{CONTROL_SCHEMA}}.companies AS c ON c.id = m.company_id
     WHERE m.user_id = {{SECURITY_SCHEMA}}.current_user_id()
       AND m.status = 'active'
       AND c.status = 'active'
     ORDER BY c.name, c.id
$function$;

ALTER FUNCTION {{SECURITY_SCHEMA}}.list_my_companies() OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON FUNCTION {{SECURITY_SCHEMA}}.list_my_companies() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{SECURITY_SCHEMA}}.list_my_companies() TO {{APP_ROLE}};

CREATE OR REPLACE FUNCTION {{SECURITY_SCHEMA}}.get_current_company_profile()
RETURNS TABLE (
    company_id bigint,
    company_name text,
    legal_name text,
    industry text,
    country text,
    currency text,
    timezone text,
    company_status text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, {{SECURITY_SCHEMA}}, {{CRYPTO_SCHEMA}}, pg_temp
AS $function$
    SELECT c.id, c.name, c.legal_name, c.industry, c.country,
           c.currency, c.timezone, c.status
      FROM {{CONTROL_SCHEMA}}.companies AS c
     WHERE c.id = {{SECURITY_SCHEMA}}.current_company_id()
       AND c.status = 'active'
$function$;

ALTER FUNCTION {{SECURITY_SCHEMA}}.get_current_company_profile() OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON FUNCTION {{SECURITY_SCHEMA}}.get_current_company_profile() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{SECURITY_SCHEMA}}.get_current_company_profile() TO {{APP_ROLE}};

CREATE OR REPLACE FUNCTION {{SECURITY_SCHEMA}}.get_current_membership()
RETURNS TABLE (
    membership_id bigint,
    user_id bigint,
    company_id bigint,
    membership_role text,
    membership_status text,
    joined_at text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, {{SECURITY_SCHEMA}}, {{CRYPTO_SCHEMA}}, pg_temp
AS $function$
    SELECT m.id, m.user_id, m.company_id, m.role, m.status, m.joined_at::text
      FROM {{CONTROL_SCHEMA}}.company_memberships AS m
     WHERE m.user_id = {{SECURITY_SCHEMA}}.current_user_id()
       AND m.company_id = {{SECURITY_SCHEMA}}.current_company_id()
       AND m.status = 'active'
$function$;

ALTER FUNCTION {{SECURITY_SCHEMA}}.get_current_membership() OWNER TO {{SECURITY_OWNER_ROLE}};
REVOKE ALL ON FUNCTION {{SECURITY_SCHEMA}}.get_current_membership() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{SECURITY_SCHEMA}}.get_current_membership() TO {{APP_ROLE}};

-- Example policy for a tenant-owned business table after its PostgreSQL migration:
-- ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.products FORCE ROW LEVEL SECURITY;
-- CREATE POLICY products_company_scope ON public.products
--   USING (company_id = {{SECURITY_SCHEMA}}.current_company_id())
--   WITH CHECK (company_id = {{SECURITY_SCHEMA}}.current_company_id());
-- Apply equivalent policies only after composite FKs and application grants are reviewed.

COMMIT;
