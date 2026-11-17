CREATE TABLE workspaces (
  id uuid PRIMARY KEY,
  slug text UNIQUE NOT NULL,
  name text NOT NULL
);
CREATE TABLE members (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces,
  email text NOT NULL,
  name text NOT NULL,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('analyst', 'reviewer')),
  UNIQUE (workspace_id, email),
  UNIQUE (workspace_id, id)
);
CREATE TABLE sessions (
  token_hash text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES members ON DELETE CASCADE,
  expires_at timestamptz NOT NULL
);
CREATE TABLE login_attempts (
  bucket text PRIMARY KEY,
  since timestamptz NOT NULL,
  attempts integer NOT NULL
);
CREATE TABLE cases (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces,
  title text NOT NULL,
  reference text NOT NULL,
  revision integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id)
);
CREATE INDEX cases_workspace ON cases (workspace_id, created_at DESC);
CREATE TABLE documents (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  case_id uuid NOT NULL,
  uploaded_by uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('po', 'invoice', 'receipt')),
  filename text NOT NULL,
  media_type text NOT NULL,
  digest text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
  attempts integer NOT NULL DEFAULT 0,
  lease_until timestamptz,
  facts jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, digest),
  FOREIGN KEY (workspace_id, case_id) REFERENCES cases(workspace_id, id),
  FOREIGN KEY (workspace_id, uploaded_by) REFERENCES members(workspace_id, id)
);
CREATE INDEX document_queue ON documents (created_at) WHERE status IN ('queued','processing');
CREATE TABLE proposals (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  case_id uuid NOT NULL,
  proposed_by uuid NOT NULL,
  revision integer NOT NULL,
  decision text NOT NULL CHECK (decision IN ('follow_up', 'close_review')),
  note text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  decided_by uuid,
  decided_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, case_id) REFERENCES cases(workspace_id, id),
  FOREIGN KEY (workspace_id, proposed_by) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, decided_by) REFERENCES members(workspace_id, id)
);
CREATE UNIQUE INDEX one_pending ON proposals (case_id, revision) WHERE status = 'pending';
CREATE UNIQUE INDEX one_approved ON proposals (case_id, revision) WHERE status = 'approved';
CREATE INDEX proposals_case ON proposals (case_id, created_at DESC);
CREATE TABLE mcp_grants (
  token_hash text PRIMARY KEY,
  workspace_id uuid NOT NULL,
  user_id uuid NOT NULL,
  case_id uuid NOT NULL,
  expires_at timestamptz NOT NULL,
  FOREIGN KEY (workspace_id, user_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, case_id) REFERENCES cases(workspace_id, id)
);
CREATE TABLE audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces,
  user_id uuid,
  case_id uuid,
  event text NOT NULL,
  metadata jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, user_id) REFERENCES members(workspace_id, id),
  FOREIGN KEY (workspace_id, case_id) REFERENCES cases(workspace_id, id)
);
CREATE INDEX audit_case ON audit_events (case_id, id DESC);
