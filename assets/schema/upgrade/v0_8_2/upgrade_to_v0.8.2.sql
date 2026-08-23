-- From 0.8.1 to 0.8.2, we have the following changes:
USE dbgpt;

-- dbgpt_session_file, Private persistence for owner-bound session and task files
CREATE TABLE IF NOT EXISTS `dbgpt_session_file` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `file_id` varchar(64) NOT NULL COMMENT 'Public file ID',
  `owner_id` varchar(255) NOT NULL COMMENT 'Owning user ID',
  `session_id` varchar(255) DEFAULT NULL COMMENT 'Interactive session ID',
  `task_id` varchar(64) DEFAULT NULL COMMENT 'Scheduled task ID',
  `display_name` varchar(256) NOT NULL COMMENT 'Display file name',
  `storage_uri` varchar(512) NOT NULL COMMENT 'Private managed storage URI',
  `media_type` varchar(255) NOT NULL COMMENT 'Detected media type',
  `file_kind` varchar(32) NOT NULL COMMENT 'File kind',
  `size_bytes` bigint NOT NULL COMMENT 'File size in bytes',
  `sha256` varchar(64) NOT NULL COMMENT 'File content SHA-256',
  `ordinal` int NOT NULL COMMENT 'Stable order within the scope',
  `status` varchar(32) NOT NULL COMMENT 'File lifecycle status',
  `inspection_json` longtext DEFAULT NULL COMMENT 'Private inspection metadata JSON',
  `error_code` varchar(64) DEFAULT NULL COMMENT 'Private processing error code',
  `error_message` text DEFAULT NULL COMMENT 'Private processing error message',
  `source_file_id` varchar(64) DEFAULT NULL COMMENT 'Source file ID for task lineage',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Record creation time',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Record update time',
  PRIMARY KEY (`id`),
  CONSTRAINT `ck_session_file_scope` CHECK ((`session_id` IS NULL) <> (`task_id` IS NULL)),
  UNIQUE KEY `uk_session_file_file_id` (`file_id`),
  KEY `idx_session_file_owner_session` (`owner_id`,`session_id`,`ordinal`),
  KEY `idx_session_file_owner_task` (`owner_id`,`task_id`,`ordinal`),
  KEY `idx_session_file_sha256` (`owner_id`,`sha256`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Session file metadata table';
-- Report/evidence persistence (execution lineage, not model reasoning)
CREATE TABLE IF NOT EXISTS `dbgpt_report` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `report_id` varchar(64) NOT NULL COMMENT 'Stable report identifier',
  `owner_id` varchar(255) NOT NULL COMMENT 'Owning user ID',
  `title` varchar(512) NOT NULL COMMENT 'Latest report title',
  `conv_uid` varchar(255) NOT NULL COMMENT 'Source conversation ID',
  `latest_run_id` varchar(64) NOT NULL COMMENT 'Latest immutable run ID',
  `latest_revision` int NOT NULL COMMENT 'Latest report revision',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Record creation time',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Record update time',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_owner_report` (`owner_id`,`report_id`),
  KEY `idx_report_owner_updated` (`owner_id`,`updated_at`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Owner-scoped report identity';

CREATE TABLE IF NOT EXISTS `dbgpt_report_run` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `run_id` varchar(64) NOT NULL COMMENT 'Immutable report run ID',
  `report_id` varchar(64) NOT NULL COMMENT 'Stable report identifier',
  `owner_id` varchar(255) NOT NULL COMMENT 'Owning user ID',
  `revision` int NOT NULL COMMENT 'Monotonic report revision',
  `schema_version` int NOT NULL COMMENT 'Report protocol schema version',
  `trigger_type` varchar(32) NOT NULL DEFAULT 'interactive' COMMENT 'Run trigger type',
  `parent_run_id` varchar(64) DEFAULT NULL COMMENT 'Parent run for refresh or replay',
  `status` varchar(32) NOT NULL DEFAULT 'completed' COMMENT 'Persisted run status',
  `document_json` longtext NOT NULL COMMENT 'Lossless ReportDocument JSON',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Run creation time',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_run_id` (`run_id`),
  UNIQUE KEY `uk_report_run_owner_report_revision` (`owner_id`,`report_id`,`revision`),
  KEY `idx_report_run_owner_report_created` (`owner_id`,`report_id`,`id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Immutable report snapshots';

CREATE TABLE IF NOT EXISTS `dbgpt_report_artifact` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `run_id` varchar(64) NOT NULL COMMENT 'Immutable report run ID',
  `artifact_id` varchar(128) NOT NULL COMMENT 'Artifact ID within the run',
  `artifact_type` varchar(32) NOT NULL COMMENT 'Protocol artifact discriminator',
  `conv_uid` varchar(255) NOT NULL COMMENT 'Producing conversation ID',
  `step_id` varchar(255) NOT NULL COMMENT 'Producing execution step ID',
  `message_id` varchar(255) DEFAULT NULL COMMENT 'Producing message ID',
  `task_id` varchar(64) DEFAULT NULL COMMENT 'Producing scheduled task ID',
  `execution_run_id` varchar(64) DEFAULT NULL COMMENT 'Producing execution run ID',
  `artifact_json` longtext NOT NULL COMMENT 'Lossless artifact JSON',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_artifact_run_artifact` (`run_id`,`artifact_id`),
  KEY `idx_report_artifact_execution` (`conv_uid`,`step_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Report artifact query index';

CREATE TABLE IF NOT EXISTS `dbgpt_report_artifact_dependency` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `run_id` varchar(64) NOT NULL COMMENT 'Immutable report run ID',
  `artifact_id` varchar(128) NOT NULL COMMENT 'Dependent artifact ID',
  `input_artifact_id` varchar(128) NOT NULL COMMENT 'Input artifact ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_artifact_dependency` (`run_id`,`artifact_id`,`input_artifact_id`),
  KEY `idx_report_artifact_dependency_input` (`run_id`,`input_artifact_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Artifact execution lineage edges';

CREATE TABLE IF NOT EXISTS `dbgpt_report_evidence` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT 'Auto increment id',
  `run_id` varchar(64) NOT NULL COMMENT 'Immutable report run ID',
  `section_id` varchar(128) NOT NULL COMMENT 'Report section ID',
  `block_id` varchar(128) NOT NULL COMMENT 'Report block ID',
  `ordinal` int NOT NULL COMMENT 'Evidence order within the block',
  `artifact_id` varchar(128) NOT NULL COMMENT 'Referenced artifact ID',
  `role` varchar(32) NOT NULL COMMENT 'Evidence role',
  `selector_json` longtext NOT NULL COMMENT 'Artifact source selector JSON',
  `target_selector_json` longtext NOT NULL COMMENT 'Report target selector JSON',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_evidence_ordinal` (`run_id`,`block_id`,`ordinal`),
  KEY `idx_report_evidence_artifact` (`run_id`,`artifact_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Report block evidence bindings';
