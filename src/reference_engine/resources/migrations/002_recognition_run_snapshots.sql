ALTER TABLE recognition_runs
ADD COLUMN input_snapshot_json TEXT
CHECK (
    input_snapshot_json IS NULL
    OR json_valid(input_snapshot_json)
);

ALTER TABLE recognition_runs
ADD COLUMN input_snapshot_sha256 TEXT
CHECK (
    input_snapshot_sha256 IS NULL
    OR (
        length(input_snapshot_sha256) = 64
        AND input_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE TRIGGER recognition_runs_snapshot_insert
BEFORE INSERT ON recognition_runs
WHEN
    (
        (NEW.input_snapshot_json IS NULL)
        <> (NEW.input_snapshot_sha256 IS NULL)
    )
    OR
    (
        NEW.outcome <> 'failed'
        AND NEW.input_snapshot_json IS NULL
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'recognition run snapshot invariant violated'
    );
END;

CREATE TRIGGER recognition_runs_snapshot_update
BEFORE UPDATE OF
    outcome,
    input_snapshot_json,
    input_snapshot_sha256
ON recognition_runs
WHEN
    (
        (NEW.input_snapshot_json IS NULL)
        <> (NEW.input_snapshot_sha256 IS NULL)
    )
    OR
    (
        NEW.outcome <> 'failed'
        AND NEW.input_snapshot_json IS NULL
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'recognition run snapshot invariant violated'
    );
END;
