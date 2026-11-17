ALTER TABLE documents ADD UNIQUE (workspace_id, case_id, id);

CREATE TABLE field_corrections (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL,
  case_id uuid NOT NULL,
  document_id uuid NOT NULL,
  reviewer_id uuid NOT NULL,
  field_index integer NOT NULL CHECK (field_index BETWEEN 0 AND 79),
  revision integer NOT NULL CHECK (revision > 0),
  value jsonb NOT NULL,
  reason text NOT NULL CHECK (char_length(reason) BETWEEN 5 AND 500),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, case_id, document_id) REFERENCES documents (workspace_id, case_id, id),
  FOREIGN KEY (workspace_id, reviewer_id) REFERENCES members (workspace_id, id)
);
CREATE INDEX field_corrections_lookup ON field_corrections (case_id, document_id, field_index, id DESC);
