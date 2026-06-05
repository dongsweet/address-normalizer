export type CandidateSource = "memory" | "standard" | "poi" | "qwen";

export interface AddressCandidate {
  source: CandidateSource;
  candidate_id: string;
  name?: string | null;
  full_address: string;
  province?: string | null;
  city?: string | null;
  district?: string | null;
  town?: string | null;
  category?: string | null;
  lon?: number | null;
  lat?: number | null;
  score: number;
  evidence?: string | null;
  metadata: Record<string, unknown>;
}

export interface NormalizedAddress {
  input: string;
  cleaned_input: string;
  normalized_address: string;
  output_line: string;
  components: Record<string, unknown>;
  anchor_type: string;
  anchor_id?: string | null;
  source: string;
  confidence: number;
  match_level: string;
  candidates: AddressCandidate[];
  warnings: string[];
  auto_persist_reason?: string | null;
  raw_model_output?: Record<string, unknown> | null;
}

export interface NormalizeBatchResponse {
  results: NormalizedAddress[];
}

export type NormalizeStreamEvent =
  | {
      type: "batch_start";
      total: number;
      concurrency: number;
      elapsed_ms: number;
    }
  | {
      type: "progress";
      index: number;
      input: string;
      status: "running";
      stage: string;
      message: string;
      elapsed_ms: number;
    }
  | {
      type: "result";
      index: number;
      input: string;
      status: "done";
      stage: "done";
      message: string;
      elapsed_ms: number;
      completed: number;
      failed: number;
      total: number;
      result: NormalizedAddress;
    }
  | {
      type: "error";
      index: number;
      input: string;
      status: "error";
      stage: "error";
      message: string;
      elapsed_ms: number;
      completed: number;
      failed: number;
      total: number;
    }
  | {
      type: "batch_complete";
      total: number;
      completed: number;
      failed: number;
      elapsed_ms: number;
    };

export interface RowProgress {
  index: number;
  input: string;
  status: "pending" | "running" | "done" | "error";
  stage: string;
  message: string;
  elapsed_ms?: number;
  result?: NormalizedAddress;
}

export interface ConfigStatus {
  database: string;
  qwen: string;
  mgeo: string;
  hive: string;
  hive_table?: string | null;
  poi_rows: number;
  memory_rows: number;
  memory_alias_rows: number;
  memory_detail_rows: number;
  default_city: string;
  hive_calls_today: number;
  qwen_calls_today: number;
}
